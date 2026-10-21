#!/usr/bin/env bash
#
# 備份與還原的實際演練（T26 的第三個觀察位置，#52）。
#
# test/pytest/integration/test_backup.py 說的是「備份與還原這兩個動作本身對不對」，
# test/bats/unit/backup_restore.bats 說的是「主機上那兩道指令會不會在不該動的時候動」。這支說的是
# 第三件事：在**真的容器、真的 volume、真的檔案權限**底下，從備份到災後還原整套走得完，而且
# 該拒絕的時候真的拒絕。三種結果分開報；這支沒跑到的那一次，演練是待驗，不是通過。
#
# 走的是人會走的那條路：script/backup.sh 與 script/restore.sh，不直接叫 CLI。
#
# **只在 Linux 上有意義**（要從主機連 host 網路上的服務），由 CI 的 backup-drill job 執行。
# 它用隔離的 compose 專案名、自己的具名 volume（演練用的設定庫）與暫存的目標根目錄，結束時
# （含失敗時）全部清掉。8080／8081 或 compose 檔裡固定的容器名已有人在用就直接停下——
# **不碰任何既有的容器、volume 或資料**。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
readonly API="http://127.0.0.1:8080"
readonly BACKEND="config_manager_backend"
readonly FRONTEND="config_manager_frontend"
readonly REPO_IN_CONTAINER="/opt/config_manager/config-repo"
readonly IMAGE="config_manager:runtime"

STEP="準備"
WORK=""
RECORDER_PID=""

fail() {
  printf 'backup_drill: 失敗 [%s] %s。下一步：%s\n' "${STEP}" "$1" "$2" >&2
  exit 1
}

ok() {
  printf 'backup_drill: 通過 [%s] %s\n' "${STEP}" "$1"
}

usage() {
  cat <<'USAGE'
用法：script/backup_drill.sh

把備份與還原實際演練一次，用隔離的設定庫與目標目錄。只在 Linux 上有意義，由 CI 執行。

演練的內容：
  服務開著、進版持續發生的同時連做幾次備份
  服務還在跑時還原 → 應被拒絕
  服務停了、設定庫還有資料時還原 → 應被拒絕，設定庫不變
  設定庫整個沒了之後，拿截斷的備份檔還原 → 應被拒絕，設定庫仍是空的
  拿完好的備份還原並啟動：清單、已進版的值、歷史、白名單都回來
  目標狀態重新比對；被刪掉的那一份寫出修復後內容與權限和原本相同
  被改過的那一份沒有被自動覆寫

占用 8080／8081 與 compose 檔裡固定的容器名稱；已經有人在用就直接停下，不碰既有的東西。
USAGE
}

cleanup() {
  local status=$?
  [[ -n "${RECORDER_PID}" ]] && kill "${RECORDER_PID}" 2>/dev/null || true
  if [[ -n "${COMPOSE_PROJECT_NAME:-}" ]]; then
    docker compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -n "${WORK}" ]]; then
    # 目標根目錄裡的檔案是容器的使用者寫的，runner 的使用者不一定刪得掉；交給一個容器去刪。
    docker run --rm --user root --entrypoint rm -v "${WORK}:${WORK}" \
      "${IMAGE}" -rf "${WORK}/targets" >/dev/null 2>&1 || true
    rm -rf "${WORK}" 2>/dev/null || true
  fi
  return "${status}"
}

# json <Python 運算式> —— 對 stdin 的 JSON 問一個問題；`d` 是解出來的值。
json() {
  python3 -c 'import json, sys; d = json.load(sys.stdin); print(eval(sys.argv[1]))' "$1"
}

# call <方法> <路徑> [JSON 內容] —— 帶著身分的 cookie 打後端，回應印到 stdout。
# 只讀 cookie、不寫回：背景那個持續進版的迴圈與前景共用同一張票，兩邊同時寫同一個檔會互相蓋掉。
# 取得了編輯權（EDITING 有值）就帶上識別碼：寫入只接受持有編輯權的那個瀏覽器（F23）。背景迴圈是
# 之後才分出來的子程序，帶著同一個值；一般續期不換識別碼。
EDITING=""
call() {
  local method="$1" path="$2" body="${3:-}"
  local -a args=(--silent --show-error --fail-with-body --max-time 20
    --cookie "${WORK}/cookies" --request "${method}")
  [[ -n "${body}" ]] && args+=(--header 'content-type: application/json' --data "${body}")
  [[ -n "${EDITING}" ]] && args+=(--header "x-cm-editing-token: ${EDITING}")
  curl "${args[@]}" "${API}${path}"
}

# 以「演練」的身分取得編輯權（F23）。服務重啟後是新的程序：身分與編輯權都不在了，要重新來一次。
take_editing() {
  call POST /api/session '{"name":"演練","email":"drill@example.com","role":"developer"}' >/dev/null
  EDITING="$(call POST /api/session/lock | json 'd["token"]')"
}

wait_for_service() {
  local tries=0
  until curl --silent --fail --max-time 2 --output /dev/null "${API}/api/mode"; do
    tries=$((tries + 1))
    if (( tries >= 60 )); then
      docker compose logs --tail 40 >&2 || true
      fail "後端等了 60 秒仍然沒有回應" "看上面印出的容器日誌，啟動檢查失敗的原因會在最後幾行"
    fi
    sleep 1
  done
}

preconditions() {
  [[ "$(uname -s)" == "Linux" ]] \
    || fail "這台機器不是 Linux（$(uname -s)），從主機連不到 host 網路上的服務" "改在 Linux 上跑；CI 的 backup-drill job 就是做這件事"
  local name
  for name in "${BACKEND}" "${FRONTEND}"; do
    [[ -z "$(docker ps --all --quiet --filter "name=^${name}\$")" ]] \
      || fail "已經有一個叫 ${name} 的容器，這支不碰既有的容器" "在一台沒有部署這套服務的機器上跑，或先由負責的人把它移走"
  done
  local port code
  for port in 8080 8081; do
    code=0
    curl --silent --max-time 2 --output /dev/null "http://127.0.0.1:${port}/" || code=$?
    [[ "${code}" -eq 7 ]] \
      || fail "127.0.0.1:${port} 已經有東西在聽，這支不占用別人的埠號" "在一台這個埠號空著的機器上跑"
  done
}

# 在目標根目錄裡以**服務的使用者**身分做一件事（建檔、改檔、刪檔、量雜湊與權限）。
# 不經過 compose：服務停著、設定庫沒了的時候也要做得到。
in_targets() {
  docker run --rm --user 1000:1000 --entrypoint sh -v "${CM_TARGET_ROOT}:${CM_TARGET_ROOT}" \
    --workdir "${CM_TARGET_ROOT}" "${IMAGE}" -c "$1"
}

# 一個檔案的指紋：內容雜湊、擁有者:群組、權限。還原前後拿來比。
# 在容器裡用 Python 量，不用 stat：這支腳本裡的指令有一部分跑在主機上，量權限的寫法不該讓人
# 分不清哪一段只能在 Linux 上跑。
fingerprint() {
  docker run --rm --user 1000:1000 --entrypoint python \
    -v "${CM_TARGET_ROOT}:${CM_TARGET_ROOT}" "${IMAGE}" -c '
import grp, hashlib, os, pwd, sys

path = sys.argv[1]
info = os.stat(path)
owner = f"{pwd.getpwuid(info.st_uid).pw_name}:{grp.getgrgid(info.st_gid).gr_name}"
with open(path, "rb") as handle:
    print(hashlib.sha256(handle.read()).hexdigest(), owner, oct(info.st_mode & 0o7777)[2:])
' "${CM_TARGET_ROOT}/$1"
}

# 設定庫現在的 HEAD（前七碼）。服務在跑就問它的容器；停了就另起一個只跑 git 的。
repo_head() {
  if [[ -n "$(docker compose ps --status running --quiet backend)" ]]; then
    docker exec "${BACKEND}" git -C "${REPO_IN_CONTAINER}" rev-parse --short=7 HEAD
  else
    docker compose run --rm --no-deps --entrypoint git backend \
      -C "${REPO_IN_CONTAINER}" rev-parse --short=7 HEAD 2>/dev/null
  fi
}

entries_in_repo() {
  docker compose run --rm --no-deps --entrypoint sh backend \
    -c "ls -A '${REPO_IN_CONTAINER}' | wc -l" 2>/dev/null | tr -d '[:space:]'
}

state_of() {
  call GET /api/configs | json "[r['state'] for r in d if r['uid'] == '$1'][0]"
}

onboard() {
  call POST /api/configs "{\"source_path\":\"${CM_TARGET_ROOT}/$1\",\"format\":\"yaml\",\"ambiguity_note\":\"\"}" \
    | json 'd["uid"]'
}

promote() {
  # 寫入本身不算續期；三次備份可能超過續期逾時（150 秒），先續期，否則編輯權被收回、草稿被清掉。
  call POST /api/session/lock/renew "{\"token\":\"${EDITING}\"}" >/dev/null
  call POST /api/drafts "{\"uid\":\"$1\",\"edits\":{\"max_vel\":$2}}" >/dev/null
  call POST /api/promote '{}' >/dev/null
}

# 預期被拒絕的還原：結束碼要是 1，訊息要含指定的字。印出腳本實際說的那一句，當作證據。
refused() {
  local bundle="$1" expected="$2" output status=0
  output="$("${REPO_ROOT}/script/restore.sh" "${bundle}" 2>&1)" || status=$?
  [[ "${status}" -eq 1 ]] \
    || fail "還原的結束碼是 ${status}，預期是 1（被拒絕）。它說的是：${output}" "檢查 script/restore.sh 與 io/backup.restore 的拒絕條件"
  [[ "${output}" == *"${expected}"* ]] \
    || fail "還原被拒絕了，但訊息裡沒有「${expected}」。它說的是：${output}" "檢查拒絕的原因是不是預期的那一個"
  printf 'backup_drill:      它說：%s\n' "$(printf '%s' "${output}" | tail -n 1)"
}

seed() {
  STEP="備料"
  # 三份設定，還原之後各走一條路：keep 不動、lost 會被刪掉、drift 會被改掉。lost 給一個不是預設的
  # 權限（640），補回來的時候才看得出權限是照紀錄還原的，不是碰巧等於預設值。
  in_targets 'printf "max_vel: 0.8\n" > keep.yaml
              printf "max_vel: 0.5\n" > lost.yaml && chmod 0640 lost.yaml
              printf "max_vel: 0.3\n" > drift.yaml
              mkdir -p extra'
  curl --silent --show-error --fail-with-body --max-time 20 --cookie-jar "${WORK}/cookies" \
    --header 'content-type: application/json' \
    --data '{"name":"演練","email":"drill@example.com","role":"developer"}' \
    "${API}/api/session" >/dev/null
  take_editing
  call POST /api/allowed-roots "{\"prefix\":\"${CM_TARGET_ROOT}/extra\"}" >/dev/null
  KEEP="$(onboard keep.yaml)"
  LOST="$(onboard lost.yaml)"
  DRIFT="$(onboard drift.yaml)"
  LOST_BEFORE="$(fingerprint lost.yaml)"
  ok "納管三份設定（keep ${KEEP}、lost ${LOST}、drift ${DRIFT}），白名單加一個根目錄"
}

backs_up_while_promoting() {
  STEP="備份"
  # 背景持續進版，前景連做三次備份。備份不停服務（定案 2），所以要在它最容易出事的時候做。
  : >"${WORK}/promoted"
  (
    i=0
    while [[ ! -e "${WORK}/stop" ]]; do
      i=$((i + 1))
      if promote "${KEEP}" "1.$(( i % 2 * 2 + 1 ))" 2>/dev/null; then
        printf 'x' >>"${WORK}/promoted"
      fi
    done
  ) &
  RECORDER_PID=$!

  local round
  for round in 1 2 3; do
    "${REPO_ROOT}/script/backup.sh" "${WORK}/backups" >"${WORK}/backup-${round}.out" 2>&1 \
      || fail "第 ${round} 次備份失敗：$(tail -n 2 "${WORK}/backup-${round}.out" | tr '\n' ' ')" "直接跑 script/backup.sh 看完整輸出"
    printf 'backup_drill:      %s\n' "$(tail -n 1 "${WORK}/backup-${round}.out")"
  done

  touch "${WORK}/stop"
  wait "${RECORDER_PID}" 2>/dev/null || true
  RECORDER_PID=""
  local during
  during="$(wc -c <"${WORK}/promoted" | tr -d '[:space:]')"
  (( during >= 1 )) \
    || fail "三次備份期間一筆進版都沒有成功，這次演練沒有量到「與進版同時發生」" "檢查背景那個進版迴圈為什麼每一次都失敗"
  ok "服務開著、背景進版 ${during} 筆的同時，三次備份都成功，每一份都當場驗證過還原得出來"

  # 最後再進版一筆、再備一次：這一份的內容是確定的，還原之後拿它來對。
  promote "${KEEP}" 2.5
  "${REPO_ROOT}/script/backup.sh" "${WORK}/backups" >"${WORK}/backup-final.out" 2>&1 \
    || fail "最後一次備份失敗：$(tail -n 2 "${WORK}/backup-final.out" | tr '\n' ' ')" "直接跑 script/backup.sh 看完整輸出"
  # 檔名帶 HEAD 前七碼；拿最新的那一份。
  GOOD="$(find "${WORK}/backups" -name '*.bundle' -newer "${WORK}/stop" | sort | tail -n 1)"
  [[ -n "${GOOD}" ]] || fail "找不到最後一次備份做出來的檔案" "看 ${WORK}/backup-final.out"
  GOOD_HEAD="$(basename "${GOOD}" .bundle | sed -E 's/.*-([0-9a-f]{7})$/\1/')"
  HISTORY_BEFORE="$(call GET "/api/configs/${KEEP}/history" | json 'len(d)')"
  [[ "$(repo_head)" == "${GOOD_HEAD}" ]] \
    || fail "最後一份備份的 HEAD 是 ${GOOD_HEAD}，設定庫現在是 $(repo_head)" "備份之後不該再有進版；檢查背景迴圈有沒有真的停下"
  ok "最後一份備份 $(basename "${GOOD}")：HEAD ${GOOD_HEAD}，keep 的歷史 ${HISTORY_BEFORE} 筆、已進版的值 2.5"
}

refuses_when_it_should() {
  STEP="拒絕：服務還在跑"
  refused "${GOOD}" "還在跑"
  ok "服務還在跑時還原被拒絕"

  STEP="拒絕：設定庫有資料"
  docker compose stop >/dev/null 2>&1
  refused "${GOOD}" "不是空目錄"
  [[ "$(repo_head)" == "${GOOD_HEAD}" ]] \
    || fail "被拒絕之後設定庫的 HEAD 變成 $(repo_head)，原本是 ${GOOD_HEAD}" "被拒絕的還原不該動到設定庫"
  ok "服務停了、設定庫還有資料時還原被拒絕，設定庫的 HEAD 仍是 ${GOOD_HEAD}"
}

loses_the_repo_and_diverges() {
  STEP="模擬災難"
  # 設定庫整個沒了：容器與 volume 一起移除。這是演練自己的 volume（隔離的專案名），不是別人的。
  docker compose down --volumes --remove-orphans >/dev/null 2>&1
  # 同一段時間裡機器上的目標檔也變了：一份被刪、一份被人手改。備份裡沒有它們（定案 4）。
  in_targets 'rm lost.yaml && printf "max_vel: 9.9\n# 現場手改\n" > drift.yaml'
  DRIFT_EDITED="$(fingerprint drift.yaml)"
  [[ "$(entries_in_repo)" == "0" ]] \
    || fail "移除 volume 之後新的設定庫不是空的" "檢查 compose.yaml 的 config_repo volume"
  ok "設定庫整個移除（新的 volume 是空的）；目標檔 lost.yaml 被刪、drift.yaml 被手改"

  STEP="拒絕：壞掉的備份"
  local size
  size="$(wc -c <"${GOOD}" | tr -d '[:space:]')"
  head -c "$(( size - 40 ))" "${GOOD}" >"${WORK}/backups/truncated.bundle"
  refused "${WORK}/backups/truncated.bundle" "還原不出來"
  [[ "$(entries_in_repo)" == "0" ]] \
    || fail "壞掉的備份被拒絕之後，設定庫裡有東西" "驗證應該發生在碰設定庫之前；檢查 io/backup.restore 的順序"
  ok "截斷的備份檔被拒絕，設定庫仍是空的"
}

restores_and_compares_again() {
  STEP="還原"
  "${REPO_ROOT}/script/restore.sh" "${GOOD}" >"${WORK}/restore.out" 2>&1 \
    || fail "還原失敗：$(tail -n 2 "${WORK}/restore.out" | tr '\n' ' ')" "直接跑 script/restore.sh 看完整輸出"
  printf 'backup_drill:      %s\n' "$(grep '還原完成' "${WORK}/restore.out" | cut -c1-80)"
  docker compose up --detach >/dev/null 2>&1 \
    || fail "還原之後服務起不來" "跑 docker compose logs backend 看啟動檢查說了什麼"
  wait_for_service
  [[ "$(repo_head)" == "${GOOD_HEAD}" ]] \
    || fail "還原出來的 HEAD 是 $(repo_head)，備份裡的是 ${GOOD_HEAD}" "檢查還原的是不是指定的那一份備份"
  [[ "$(call GET "/api/configs/${KEEP}" | json 'd["values"]["max_vel"]')" == "2.5" ]] \
    || fail "還原後 keep 已進版的值不是 2.5" "檢查來源複本有沒有跟著備份回來"
  [[ "$(call GET "/api/configs/${KEEP}/history" | json 'len(d)')" == "${HISTORY_BEFORE}" ]] \
    || fail "還原後 keep 的歷史筆數與備份當時的 ${HISTORY_BEFORE} 筆不同" "檢查備份有沒有帶完整歷史"
  [[ "$(call GET /api/allowed-roots | json "'${CM_TARGET_ROOT}/extra' in d['prefixes']")" == "True" ]] \
    || fail "還原後從介面加的白名單根目錄不見了" "檢查 allowed-roots.toml 有沒有提交進設定庫"
  [[ "$(call GET /api/configs | json 'len(d)')" == "3" ]] \
    || fail "還原後清單不是三份" "檢查 config-list.toml 有沒有跟著備份回來"
  ok "還原後 HEAD ${GOOD_HEAD}：清單三份、keep 已進版的值 2.5、歷史 ${HISTORY_BEFORE} 筆、白名單都回來"

  STEP="重新比對"
  local states
  states="$(state_of "${KEEP}") $(state_of "${LOST}") $(state_of "${DRIFT}")"
  [[ "${states}" == "in_sync missing drift" ]] \
    || fail "還原後三份的狀態是「${states}」，預期「in_sync missing drift」" "目標狀態應該是當下重新比對的結果，不是備份當時的"
  ok "目標狀態重新比對：keep 一致、lost 未部署、drift 偏離"
}

repairs_the_missing_and_leaves_the_drifted() {
  STEP="補回缺檔"
  take_editing  # 還原之後重啟過：寫出修復也只接受持有編輯權的那個瀏覽器（F23）
  call POST "/api/configs/${LOST}/apply" >/dev/null
  local after
  after="$(fingerprint lost.yaml)"
  [[ "${after}" == "${LOST_BEFORE}" ]] \
    || fail "補回的 lost.yaml 是「${after}」，原本是「${LOST_BEFORE}」（內容雜湊 擁有者:群組 權限）" "寫出修復應該照設定庫的內容與納管時記下的權限寫回"
  [[ "$(state_of "${LOST}")" == "in_sync" ]] \
    || fail "補回之後 lost 的狀態不是一致" "檢查寫出修復之後的比對"
  ok "lost.yaml 以寫出修復補回：內容、擁有者與權限和原本相同（${after#* }）"

  STEP="偏離不覆寫"
  [[ "$(fingerprint drift.yaml)" == "${DRIFT_EDITED}" ]] \
    || fail "drift.yaml 在還原與啟動之後被改掉了" "還原與啟動都不該寫任何目標檔；偏離要由人處置"
  # 帶身分與編輯權去叫：被拒的原因要是「目標已經存在」，不是沒有身分或編輯權（那樣 409 會假通過）。
  local code
  code="$(curl --silent --output "${WORK}/drift-apply.out" --write-out '%{http_code}' --max-time 20 \
    --cookie "${WORK}/cookies" --header "x-cm-editing-token: ${EDITING}" \
    --request POST "${API}/api/configs/${DRIFT}/apply")"
  [[ "${code}" == "409" ]] \
    || fail "對偏離的那一份叫寫出修復回的是 HTTP ${code}，預期 409" "寫出修復只補不存在的檔案，不覆蓋既有內容"
  grep -q '目標檔案已經存在' "${WORK}/drift-apply.out" \
    || fail "寫出修復回 409，但原因不是目標已經存在：$(cat "${WORK}/drift-apply.out")" "檢查請求有沒有帶身分與編輯權"
  [[ "$(fingerprint drift.yaml)" == "${DRIFT_EDITED}" && "$(state_of "${DRIFT}")" == "drift" ]] \
    || fail "被 409 拒絕之後 drift.yaml 的內容或狀態變了" "被拒絕的寫出修復不該動到檔案"
  ok "drift.yaml 沒有被自動覆寫：還原、啟動、寫出修復（HTTP 409）之後內容都還是手改的那一版"
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
  export COMPOSE_PROJECT_NAME="cm_drill_$$"
  export COMPOSE_FILE="compose.yaml"
  export CM_TARGET_ROOT="${WORK}/targets"
  # 目標根目錄要讓容器裡的服務使用者寫得進去；它的 uid 與 runner 的不同，所以整個放開。
  mkdir -p "${CM_TARGET_ROOT}" "${WORK}/backups"
  chmod 0777 "${CM_TARGET_ROOT}"

  STEP="建置"
  docker compose build >/dev/null 2>&1 \
    || fail "runtime 映像建不起來" "直接跑 docker compose build 看它印出的原因"
  docker compose up --detach >/dev/null 2>&1 \
    || fail "docker compose up 失敗" "直接跑 docker compose up 看它印出的原因"
  wait_for_service
  ok "演練環境起來了（專案 ${COMPOSE_PROJECT_NAME}，自己的設定庫與目標目錄）"

  seed
  backs_up_while_promoting
  refuses_when_it_should
  loses_the_repo_and_diverges
  restores_and_compares_again
  repairs_the_missing_and_leaves_the_drifted

  STEP="總結"
  ok "備份、三種拒絕、災後還原、重新比對、補回缺檔、偏離不覆寫都走完了"
}

main "$@"
