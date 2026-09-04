#!/usr/bin/env bash
#
# 停止服務。只停容器——config_repo volume 會留著，因為它裝的是唯一真實來源，
# 而「停止」不是「丟掉它」的決定。要刪它得寫全 `docker compose down --volumes`，
# 完整拼出來，才不會有人靠反射動作就把它刪了。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
# shellcheck source=hooks/dispatch.sh
source "${REPO_ROOT}/script/hooks/dispatch.sh"

main() {
  cd "${REPO_ROOT}"
  run_hook pre stop
  docker compose down --remove-orphans "$@"
  run_hook post stop
}

main "$@"
