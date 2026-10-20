#!/usr/bin/env bash
#
# compose 的服務實測（T25 的第二個觀察位置，#51）。
#
# test/bats/integration/compose.bats 驗的是「compose 會照這樣起服務」；這支驗的是「這樣起的服務
# 真的是那樣」：host 與 bridge 各**實際起一次**，從外面連、整個容器移除再建、叫它停。兩種結果
# 分開報——那一檔綠了不代表這一支過了，這一支沒跑到的那一次，這一半是待驗，不是通過。
#
# **只在 Linux 上有意義**，所以由 CI 的 runner 執行：`host` 網路在 macOS 的 Docker Desktop 上
# 不是主機的網路，在那裡量到的「非本機位址連不到」什麼都不證明。
#
# 它會占用 8080／8081 與固定的容器名稱（compose 檔寫死的），所以一開始先確認兩者都沒人在用，
# 有的話直接停下——**不碰任何既有的容器或 volume**。自己用的是隔離的 compose 專案名、自己的
# 具名 volume、暫存的目標根目錄，結束時（含失敗時）全部清掉。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
readonly API="http://127.0.0.1:8080"
readonly WEB="http://127.0.0.1:8081"
readonly BACKEND="config_manager_backend"
readonly FRONTEND="config_manager_frontend"
readonly CONTROL="cm_smoke_control"
readonly CONTROL_PORT=18099
readonly STOP_GRACE=10

MODE="準備"
WORK=""
CONTROL_PID=""

fail() {
  printf 'compose_smoke: 失敗 [%s] %s。下一步：%s\n' "${MODE}" "$1" "$2" >&2
  exit 1
}

ok() {
  printf 'compose_smoke: 通過 [%s] %s\n' "${MODE}" "$1"
}

usage() {
  cat <<'USAGE'
用法：script/compose_smoke.sh

把 compose 的服務實際起起來驗一次：host 網路（compose.yaml）與 bridge 網路
（加 compose.bridge.yaml）各一次。只在 Linux 上有意義，由 CI 執行。

每一種網路模式驗：
  本機連得到後端與前端；後端是部署模式
  主機的非 loopback 位址連不到兩個服務（先以一個故意綁 0.0.0.0 的對照服務證明那個位址連得到）
  容器的 PID 1 是服務本身；重啟策略是 unless-stopped
  容器整個移除再建之後：清單、已進版的值、歷史、白名單都在，草稿與身分不在
  docker compose stop 在寬限時間內結束，不是被強制終止
另外驗一次：建好的 runtime 映像裡 import 不到測試工具。

占用 8080／8081 與 compose 檔裡固定的容器名稱；已經有人在用就直接停下，不碰既有的東西。
USAGE
}

# 這一輪起的東西全部收掉。失敗時也要跑，所以掛在 EXIT 上；每一步都容錯，收拾本身不該再製造
# 一個蓋掉原本錯誤的錯誤。
cleanup() {
  local status=$?
  [[ -n "${CONTROL_PID}" ]] && kill "${CONTROL_PID}" 2>/dev/null || true
  docker container rm --force "${CONTROL}" >/dev/null 2>&1 || true
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    COMPOSE_FILE="compose.yaml:compose.bridge.yaml" \
      docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -n "${WORK}" ]]; then
    # 目標根目錄裡的檔案是容器的使用者寫的，runner 的使用者不一定刪得掉；交給一個容器去刪。
    docker run --rm --user root --entrypoint rm -v "${WORK}:${WORK}" \
      "config_manager:runtime" -rf "${WORK}/targets" >/dev/null 2>&1 || true
    rm -rf "${WORK}" 2>/dev/null || true
  fi
  return "${status}"
}

# json <Python 運算式> —— 對 stdin 的 JSON 問一個問題；`d` 是解出來的值。
json() {
  python3 -c 'import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"
}

# call <方法> <路徑> [JSON 內容] —— 帶著同一罐 cookie 打後端，回應印到 stdout。取得了編輯權
# （EDITING 有值）就帶上識別碼：寫入只接受持有編輯權的那個瀏覽器（F23）。
EDITING=""
call() {
  local method="$1" path="$2" body="${3:-}"
  local -a args=(--silent --show-error --fail-with-body --max-time 20
    --cookie "${WORK}/cookies" --cookie-jar "${WORK}/cookies" --request "${method}")
  [[ -n "${body}" ]] && args+=(--header 'content-type: application/json' --data "${body}")
  [[ -n "${EDITING}" ]] && args+=(--header "x-cm-editing-token: ${EDITING}")
  curl "${args[@]}" "${API}${path}"
}

wait_for() {
  local url="$1" tries=0
  until curl --silent --fail --max-time 2 --output /dev/null "${url}"; do
    tries=$((tries + 1))
    if (( tries >= 60 )); then
      docker compose logs --tail 40 >&2 || true
      fail "${url} 等了 60 秒仍然沒有回應" "看上面印出的容器日誌，啟動檢查失敗的原因會在最後幾行"
    fi
    sleep 1
  done
}

preconditions() {
  [[ "$(uname -s)" == "Linux" ]] \
    || fail "這台機器不是 Linux（$(uname -s)），host 網路在這裡不是主機的網路" "改在 Linux 上跑；CI 的 compose-smoke job 就是做這件事"
  local name
  for name in "${BACKEND}" "${FRONTEND}" "${CONTROL}"; do
    [[ -z "$(docker ps --all --quiet --filter "name=^${name}\$")" ]] \
      || fail "已經有一個叫 ${name} 的容器，這支不碰既有的容器" "在一台沒有部署這套服務的機器上跑，或先由負責的人把它移走"
  done
  # 連線被拒（curl 結束碼 7）才算空著。其餘任何結果——連得上、連上了但回的不是 HTTP——都代表
  # 那個埠號有人在用。
  local port code
  for port in 8080 8081 "${CONTROL_PORT}"; do
    code=0
    curl --silent --max-time 2 --output /dev/null "http://127.0.0.1:${port}/" || code=$?
    [[ "${code}" -eq 7 ]] \
      || fail "127.0.0.1:${port} 已經有東西在聽，這支不占用別人的埠號" "在一台這個埠號空著的機器上跑"
  done
}

# 主機上一個不是 loopback 的 IPv4 位址——「別台機器會用來連這台」的那一個。
lan_address() {
  local address
  address="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | grep -v '^127\.' | head -n 1 || true)"
  [[ -n "${address}" ]] \
    || fail "這台機器找不到任何非 loopback 的 IPv4 位址，沒辦法驗「非本機連不到」" "在有對外網卡的機器上跑"
  printf '%s\n' "${address}"
}

# 「連不到」只有在「本來連得到」的前提下才有意義。所以每一種模式都先起一個**故意綁 0.0.0.0**
# 的對照服務、從同一個非本機位址連它：連得到，才接著量被測的服務。host 模式的對照是主機上的
# 一個程序；bridge 模式的對照是一個埠號對應到 0.0.0.0 的容器——與被測的服務走同一條路。
start_control() {
  local address="$1"
  if [[ "${MODE}" == "host" ]]; then
    python3 -m http.server "${CONTROL_PORT}" --bind 0.0.0.0 --directory "${WORK}/control" >/dev/null 2>&1 &
    CONTROL_PID=$!
  else
    docker run --detach --name "${CONTROL}" --publish "0.0.0.0:${CONTROL_PORT}:8081" \
      --env CM_ROLE=frontend --stop-signal SIGINT "config_manager:runtime" \
      python -m http.server 8081 --bind 0.0.0.0 --directory /opt/config_manager/src/config_manager/web >/dev/null
  fi
  wait_for "http://${address}:${CONTROL_PORT}/"
  ok "對照：${address}:${CONTROL_PORT} 上一個綁 0.0.0.0 的服務連得到（測試端到得了這個位址）"
}

stop_control() {
  [[ -n "${CONTROL_PID}" ]] && kill "${CONTROL_PID}" 2>/dev/null || true
  CONTROL_PID=""
  docker container rm --force "${CONTROL}" >/dev/null 2>&1 || true
}

# 被測的埠號從非本機位址要是**被拒絕**（curl 結束碼 7），不是逾時：逾時可能是封包被丟掉，
# 那說不出有沒有東西在聽。
refused_from() {
  local address="$1" port="$2" code=0
  curl --silent --max-time 5 --output /dev/null "http://${address}:${port}/" || code=$?
  [[ "${code}" -eq 7 ]] \
    || fail "從 ${address}:${port} 連線的結果是 curl 結束碼 ${code}，預期是 7（連線被拒）" "0 代表連得到——檢查 compose 檔裡這個服務的監聽位址與埠號對應；28 是逾時，說不出有沒有在聽，換一台網路單純的機器再量"
  ok "${address}:${port} 連線被拒（只有本機連得到）"
}

only_local() {
  local address
  address="$(lan_address)"
  start_control "${address}"
  refused_from "${address}" 8080
  refused_from "${address}" 8081
  stop_control
}

answers_locally() {
  wait_for "${API}/api/mode"
  wait_for "${WEB}/"
  [[ "$(curl --silent "${API}/api/mode" | json 'd["mode"]')" == "deployment" ]] \
    || fail "後端回報的模式不是 deployment" "檢查 compose.yaml 的 CM_MODE 預設值"
  local page
  page="$(curl --silent "${WEB}/")"
  [[ "${page}" == *"config_manager"* ]] \
    || fail "前端供出來的不是這套系統的頁面" "檢查 compose.yaml 裡 frontend 的 --directory"
  ok "本機連得到後端（部署模式）與前端"
}

runs_as_pid_one() {
  local name expected command
  for name in "${BACKEND}:config_manager.api.cli" "${FRONTEND}:http.server"; do
    expected="${name#*:}"
    command="$(docker exec "${name%%:*}" cat /proc/1/cmdline | tr '\0' ' ')"
    [[ "${command}" == *"${expected}"* ]] \
      || fail "${name%%:*} 的 PID 1 是「${command}」，不是服務本身" "檢查 script/entrypoint.sh 最後一行是不是 exec \"\$@\""
    [[ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "${name%%:*}")" == "unless-stopped" ]] \
      || fail "${name%%:*} 的重啟策略不是 unless-stopped" "檢查 compose.yaml 的 restart"
  done
  ok "兩個容器的 PID 1 都是服務本身，重啟策略都是 unless-stopped"
}

# 重啟保留範圍（驗證清單 U40）。「重啟」做得比 restart 更重：容器整個移除再建，只有 volume 留著
# ——留得下來的東西因此確定是住在 volume 裡，不是住在容器的可寫層。
survives_recreation() {
  local target="${CM_TARGET_ROOT}/smoke-${MODE}.yaml" extra="${CM_TARGET_ROOT}/extra-${MODE}"
  # 檔案由容器裡的服務使用者建：納管會記下它的擁有者，進版要能照那個擁有者寫回去。
  docker exec "${BACKEND}" sh -c 'printf "max_vel: 0.8\n" > "$1" && mkdir -p "$2"' _ "${target}" "${extra}"

  call POST /api/session '{"name":"實測","email":"smoke@example.com","role":"developer"}' >/dev/null
  # 取得編輯權（F23）：之後的寫入都帶它。容器重建後是新的程序、只做讀取，不必釋放。
  EDITING="$(call POST /api/session/lock | json 'd["token"]')"
  call POST /api/allowed-roots "{\"prefix\":\"${extra}\"}" >/dev/null
  local entry uid
  entry="$(call POST /api/configs "{\"source_path\":\"${target}\",\"format\":\"yaml\",\"ambiguity_note\":\"\"}")"
  uid="$(json 'd["uid"]' <<<"${entry}")"
  if [[ "${MODE}" == "bridge" ]]; then
    [[ "$(json 'd["hostname"]' <<<"${entry}")" == "${CM_HOSTNAME}" ]] \
      || fail "納管紀錄的 hostname 不是 CM_HOSTNAME 給的 ${CM_HOSTNAME}" "檢查 compose.bridge.yaml 有沒有把 CM_HOSTNAME 帶進後端"
    ok "納管紀錄的 hostname 是 CM_HOSTNAME 給的值，不是容器 ID"
  fi
  call POST /api/drafts "{\"uid\":\"${uid}\",\"edits\":{\"max_vel\":1.2}}" >/dev/null
  call POST /api/promote '{}' >/dev/null
  call POST /api/drafts "{\"uid\":\"${uid}\",\"edits\":{\"max_vel\":1.5}}" >/dev/null
  [[ "$(call GET /api/drafts | json 'd["count"]')" == "1" ]] \
    || fail "移除容器之前應該有一份未進版草稿" "檢查 POST /api/drafts 的回應"

  docker compose down --remove-orphans >/dev/null 2>&1
  docker compose up --detach >/dev/null 2>&1
  wait_for "${API}/api/mode"

  local seen
  seen="$(call GET /api/configs | json "[(r['state']) for r in d if r['uid'] == '${uid}']")"
  [[ "${seen}" == "['in_sync']" ]] \
    || fail "容器重建後清單裡這份設定是 ${seen}，預期是一份、狀態一致" "檢查 config_repo volume 有沒有掛在 CM_CONFIG_REPO 指的路徑"
  [[ "$(call GET "/api/configs/${uid}" | json 'd["values"]["max_vel"]')" == "1.2" ]] \
    || fail "容器重建後已進版的值不是 1.2" "檢查來源複本是不是寫在 volume 裡"
  [[ "$(call GET "/api/configs/${uid}/history" | json '[c["kind"] for c in d]')" == "['cfg']" ]] \
    || fail "容器重建後這份設定的歷史不是一筆進版紀錄" "檢查 config-repo 的 .git 是不是在 volume 裡"
  [[ "$(call GET /api/allowed-roots | json "'${extra}' in d['prefixes']")" == "True" ]] \
    || fail "容器重建後從介面加的白名單根目錄不見了" "檢查 allowed-roots.toml 是不是寫在 volume 裡"
  [[ "$(call GET /api/drafts | json 'd["count"]')" == "0" ]] \
    || fail "容器重建後還看得到未進版草稿" "草稿不該跨重啟保留（U40）；檢查是不是有東西把它寫進了 volume"
  [[ "$(call GET /api/session)" == "null" ]] \
    || fail "容器重建後服務還認得原本的身分" "身分不該跨重啟保留（U40）"
  ok "容器整個移除再建之後：清單、已進版的值、歷史、白名單都在；未進版草稿與身分不在"
}

stops_in_time() {
  local started elapsed name code
  started="$(date +%s)"
  docker compose stop --timeout "${STOP_GRACE}" >/dev/null 2>&1
  elapsed=$(( $(date +%s) - started ))
  for name in "${BACKEND}" "${FRONTEND}"; do
    code="$(docker inspect --format '{{.State.ExitCode}}' "${name}")"
    [[ "${code}" -ne 137 ]] \
      || fail "${name} 是被強制終止的（結束碼 137），停止訊號沒有被服務接住" "檢查 entrypoint 的 exec 交棒，以及 compose.yaml 裡這個服務的 stop_signal"
  done
  (( elapsed < STOP_GRACE )) \
    || fail "docker compose stop 花了 ${elapsed} 秒，等滿了 ${STOP_GRACE} 秒的寬限" "同上：有一個服務沒有接住停止訊號"
  ok "docker compose stop 在 ${elapsed} 秒內結束，兩個容器都不是被強制終止"
}

exercise() {
  MODE="$1"
  export COMPOSE_FILE="$2"
  printf 'compose_smoke: ── %s 網路（%s）──\n' "${MODE}" "${COMPOSE_FILE}"
  docker compose up --detach >/dev/null 2>&1 \
    || fail "docker compose up 失敗" "直接跑 COMPOSE_FILE=${COMPOSE_FILE} docker compose up 看它印出的原因"
  answers_locally
  only_local
  runs_as_pid_one
  survives_recreation
  stops_in_time
  docker compose down --volumes --remove-orphans >/dev/null 2>&1
  rm -f "${WORK}/cookies"
}

image_has_no_test_tooling() {
  MODE="映像"
  local module
  for module in pytest coverage ruff mypy pylint playwright; do
    if docker run --rm --entrypoint python "config_manager:runtime" -c "import ${module}" >/dev/null 2>&1; then
      fail "runtime 映像裡 import 得到 ${module}" "把它從 config/pip/requirements.txt 移到 requirements-dev.txt"
    fi
  done
  ok "runtime 映像裡 import 不到任何測試工具"
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    return 0
  fi
  cd "${REPO_ROOT}"
  preconditions

  WORK="$(mktemp -d)"
  trap cleanup EXIT
  export COMPOSE_PROJECT_NAME="cm_smoke_$$"
  export CM_TARGET_ROOT="${WORK}/targets"
  export CM_HOSTNAME="smoke-host"
  # 目標根目錄要讓容器裡的服務使用者寫得進去；它的 uid 與 runner 的不同，所以整個放開。
  # 這是暫存目錄，結束時就刪。
  mkdir -p "${CM_TARGET_ROOT}" "${WORK}/control"
  chmod 0777 "${CM_TARGET_ROOT}"
  printf 'control\n' >"${WORK}/control/index.html"

  MODE="建置"
  docker compose build >/dev/null 2>&1 \
    || fail "runtime 映像建不起來" "直接跑 docker compose build 看它印出的原因"
  ok "runtime 映像建好了"

  image_has_no_test_tooling
  exercise host "compose.yaml"
  exercise bridge "compose.yaml:compose.bridge.yaml"

  MODE="總結"
  ok "host 與 bridge 兩種網路的服務實測都過了"
}

main "$@"
