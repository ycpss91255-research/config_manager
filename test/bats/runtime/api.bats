#!/usr/bin/env bats
#
# HTTP 端點（測試介面 T9）。在**真正要部署的那個映像**裡把服務起在 loopback，
# 打真實 HTTP。
#
# 為什麼在這裡而不在 test/pytest/system/：script/test.sh 是把自己 docker run
# 進測試映像裡跑的，那個容器沒掛 docker socket、也沒有 docker CLI（實測過），
# 所以從那裡啟動另一個容器是做不到的。Dockerfile 的 runtime-test 階段本來就
# 在跑 bats（見 smoke），系統測試延用同一個位置——不需要新權限、新工具或新模式。
#
# 這一層測不到 compose 的接線（volume 掛載點、CM_CONFIG_REPO 與掛載是否一致、
# host networking）。那半邊屬於 #90，兩邊都要有才算蓋住 T9。
#
# 用 python 的 urllib 而不是 curl：runtime 映像裡本來就有 python，為了測試而
# 多裝一個套件，測到的就不是要部署的那個環境。

CM_PORT=8080
CM_BASE="http://127.0.0.1:${CM_PORT}"

setup_file() {
  export CM_CONFIG_REPO="/tmp/config-repo"
  export CM_ROLE=backend
  mkdir -p "${CM_CONFIG_REPO}"

  # 經由 entrypoint 啟動，不直接叫 uvicorn：要驗的就是這條真正的啟動路徑，
  # 包含前置檢查與首次啟動種下清單檔。
  /entrypoint.sh python -m config_manager.api.cli serve \
    --host 127.0.0.1 --port "${CM_PORT}" &
  export CM_SERVER_PID=$!

  local attempt
  for attempt in $(seq 1 50); do
    if get /api/configs >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done

  printf 'server did not answer on %s within 10s\n' "${CM_BASE}" >&2
  return 1
}

teardown_file() {
  [[ -n "${CM_SERVER_PID:-}" ]] && kill "${CM_SERVER_PID}" 2>/dev/null
  return 0
}

# 回應主體印到 stdout；非 2xx 則以非零碼結束。
get() {
  python - "$1" <<'PY'
import sys
import urllib.error
import urllib.request

url = "http://127.0.0.1:8080" + sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=2) as response:
        sys.stdout.write(response.read().decode("utf-8"))
except urllib.error.HTTPError as error:
    sys.stderr.write(f"{error.code}\n")
    sys.exit(1)
except OSError as error:
    sys.stderr.write(f"{error}\n")
    sys.exit(1)
PY
}

# 送出 JSON；非 2xx 則把狀態碼印到 stdout 並以非零碼結束。
post() {
  python - "$1" "$2" <<'PY'
import sys
import urllib.error
import urllib.request

url = "http://127.0.0.1:8080" + sys.argv[1]
request = urllib.request.Request(
    url,
    data=sys.argv[2].encode("utf-8"),
    headers={"content-type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=2) as response:
        sys.stdout.write(response.read().decode("utf-8"))
except urllib.error.HTTPError as error:
    sys.stdout.write(f"{error.code} {error.read().decode('utf-8')}")
    sys.exit(1)
except OSError as error:
    sys.stdout.write(str(error))
    sys.exit(1)
PY
}

@test "GET /api/configs 在全新的 config-repo 上回空清單" {
  # 什麼都還沒納管是合法狀態。回空清單，不是回錯誤，也不是起不來——
  # entrypoint 在同一次啟動裡種下了那份空的清單檔（#66）。
  run get /api/configs
  [ "${status}" -eq 0 ]
  [ "${output}" = "[]" ]
}

@test "服務是經由 entrypoint 起來的，前置檢查已經跑過" {
  # 種子檔存在即證明走的是 entrypoint 那條路徑，而不是繞過它直接叫 uvicorn。
  [ -f "/tmp/config-repo/config-list.toml" ]
}

@test "GET /api/configs 帶出每一筆的狀態" {
  # 掃描的三種狀態各一筆，走真實 HTTP 看端點吐出來的形狀。判定本身由 T21 逐條
  # 測過；這裡要證明的是「端點真的把它接上了」，而不是回一個寫死的欄位。
  #
  # 這一則刻意排在「回空清單」之後：它會改動 config-repo。
  local repo="/tmp/config-repo"
  mkdir -p "${repo}/files" "${repo}/deployed"
  printf 'a: 1\n' >"${repo}/files/a.yaml"
  printf 'b: 1\n' >"${repo}/files/b.yaml"
  printf 'c: 1\n' >"${repo}/files/c.yaml"
  printf 'a: 1\n' >"${repo}/deployed/a.yaml"   # 與來源相同 → 一致
  printf 'b: 2\n' >"${repo}/deployed/b.yaml"   # 與來源不同 → 偏離
                                               # c 沒有目標檔     → 未部署

  local letters=(a b c)
  local index letter
  for index in 0 1 2; do
    letter="${letters[${index}]}"
    cat >>"${repo}/config-list.toml" <<TOML

[[files]]
uid      = "mfz3k9q$((index + 1))"
name     = "${letter}"
hostname = "amr01"
source   = "files/${letter}.yaml"
target   = "${repo}/deployed/${letter}.yaml"
format   = "yaml"
groups   = []
TOML
  done

  run get /api/configs
  [ "${status}" -eq 0 ]
  [[ "${output}" == *'"state":"in_sync"'* ]]
  [[ "${output}" == *'"state":"drift"'* ]]
  [[ "${output}" == *'"state":"missing"'* ]]
  [[ "${output}" == *'"ref":"a@amr01-mfz3k9q1"'* ]]
}

@test "CLI 的 list 走的是同一支端點，看到的東西與畫面相同" {
  # ADR-00000009：不存在「CLI 能做但介面不能」或反之的情況，因為根本是同一組
  # 端點。這一則排在上一則之後，用的是上一則寫進 config-repo 的那三筆。
  #
  # 若 CLI 改成自己讀清單檔，這一則仍會綠——所以它不是靠比對輸出來把關，而是
  # 靠 --api 指向那個 HTTP 位址：讀檔的實作根本不會用到它。
  run python -m config_manager.api.cli list --api "http://127.0.0.1:${CM_PORT}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"一致"* ]]
  [[ "${output}" == *"偏離"* ]]
  [[ "${output}" == *"未部署"* ]]
  [[ "${output}" == *"a@amr01-mfz3k9q1"* ]]
}

@test "POST /api/session 記下身分，GET 回得出來" {
  # 這不是登入：沒有密碼、不驗證、角色是自我宣告（ADR-00000020）。驗的是
  # 「記錄的就是宣告的值」，而 git_author 是它存在的理由——變更紀錄的作者。
  run post /api/session '{"name":"陳小明","email":"ming@example.com","role":"developer"}'
  [ "${status}" -eq 0 ]
  [[ "${output}" == *'"git_author":"陳小明 <ming@example.com>"'* ]]
  [[ "${output}" == *'"role":"developer"'* ]]

  run get /api/session
  [ "${status}" -eq 0 ]
  [[ "${output}" == *'"name":"陳小明"'* ]]
}

@test "身分含會破壞作者字串的字元時被端點拒絕，訊息說得出下一步" {
  # 悄悄清洗會讓紀錄上的名字與輸入的不同，而紀錄的用途正是追溯到人。
  run post /api/session '{"name":"陳小明 <admin@example.com>","email":"ming@example.com","role":"user"}'
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"422"* ]]
}

@test "backend 沒起來時 CLI 大聲失敗，不回一份空清單" {
  # 「服務沒起來」與「什麼都還沒納管」看起來都是沒有東西——但該做的處置完全
  # 不同（不變式 2）。指一個沒有人在聽的 port。
  run python -m config_manager.api.cli list --api "http://127.0.0.1:9"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"讀不到"* ]]
}
