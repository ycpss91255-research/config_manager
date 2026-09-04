#!/usr/bin/env bash
#
# 回收這個 repo 建置後留下的東西。預設只限本專案範圍：會掃到別人映像的 prune
# 正是那種「方便一下、賠掉某人一個下午」的設計，所以放寬範圍的形式是 opt-in，
# 而且要完整拼出來。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
# shellcheck source=hooks/dispatch.sh
source "${REPO_ROOT}/script/hooks/dispatch.sh"

usage() {
  cat <<'USAGE'
用法：script/prune.sh [--all]

  （不帶參數）  移除本專案已停止的容器與懸空映像。config_repo volume 永遠不動。
  --all         另外移除 config_manager:devel 與 config_manager:runtime。

  --all 刻意不動 config_manager:runtime-test 與 config_manager-test-tools:local：
  那兩個是檢查用的映像，重建一次要好幾分鐘，而「回收」不該讓下一次檢查變慢。
  要刪它們就明確指名：docker image rm config_manager:runtime-test
USAGE
}

main() {
  local wide=0
  case "${1:-}" in
    --all) wide=1 ;;
    -h|--help) usage; return 0 ;;
    "") ;;
    *) printf 'prune.sh: 不認得的參數 %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac

  cd "${REPO_ROOT}"
  run_hook pre prune
  docker compose rm --stop --force
  docker image prune --force --filter "label=com.docker.compose.project=config_manager"

  if (( wide )); then
    # 只動映像。volume 裝的是唯一真實來源，只有 `docker compose down --volumes`
    # 刪得掉它，沒有別的路徑。
    docker image rm --force config_manager:devel config_manager:runtime 2>/dev/null || true
    # 訊息指名刪了哪兩個，而不是說「built images」。這裡建置出來的映像有四個
    # （devel、runtime、runtime-test、test-tools），說「全部」而只刪一半，是訊息
    # 宣稱的比實際做的多——不變式 2 的同一種形狀（#106）。
    printf 'prune.sh: 已移除 config_manager:devel 與 config_manager:runtime；'
    printf 'config_repo volume 與兩個檢查用映像未動\n'
  fi
  run_hook post prune
}

main "$@"
