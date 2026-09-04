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
