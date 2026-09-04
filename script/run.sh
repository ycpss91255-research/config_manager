#!/usr/bin/env bash
#
# 啟動兩個服務。主機端的前置條件檢查放在 script/hooks/pre/run.sh，
# 這支在把任何東西拉起來之前先呼叫它。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
# shellcheck source=hooks/dispatch.sh
source "${REPO_ROOT}/script/hooks/dispatch.sh"

main() {
  cd "${REPO_ROOT}"
  run_hook pre run
  docker compose up -d "$@"
  run_hook post run
  docker compose ps
}

main "$@"
