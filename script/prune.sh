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
Usage: script/prune.sh [--all]

  (no arguments)  Remove this project's stopped containers and dangling
                  images. The config_repo volume is never touched.
  --all           Also remove this project's built images.
USAGE
}

main() {
  local wide=0
  case "${1:-}" in
    --all) wide=1 ;;
    -h|--help) usage; return 0 ;;
    "") ;;
    *) printf 'prune.sh: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac

  cd "${REPO_ROOT}"
  run_hook pre prune
  docker compose rm --stop --force
  docker image prune --force --filter "label=com.docker.compose.project=config_manager"

  if (( wide )); then
    # 只動映像。volume 裝的是唯一真實來源，只有 `docker compose down --volumes`
    # 刪得掉它，沒有別的路徑。
    docker image rm --force config_manager:devel config_manager:runtime 2>/dev/null || true
    printf 'prune.sh: removed built images; the config_repo volume is untouched.\n'
  fi
  run_hook post prune
}

main "$@"
