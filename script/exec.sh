#!/usr/bin/env bash
#
# 在執行中的服務裡開一個 shell（或執行一個指令）。預設 backend，
# 值得進去看的能力都在那個容器裡。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
# shellcheck source=hooks/dispatch.sh
source "${REPO_ROOT}/script/hooks/dispatch.sh"

main() {
  local service="backend"

  if [[ "${1:-}" == "--service" ]]; then
    shift
    [[ $# -gt 0 ]] || { printf 'exec.sh: --service needs a name\n' >&2; exit 2; }
    service="$1"
    shift
  fi

  cd "${REPO_ROOT}"
  run_hook pre exec "${service}"
  # 沒有 post-exec hook：這裡以 exec 把終端機交出去，永不返回。註冊成「之後」
  # 執行的 hook 只會靜默地永遠不跑，那比不提供這個 hook 更糟。
  if (( $# == 0 )); then
    exec docker compose exec "${service}" bash
  fi
  exec docker compose exec "${service}" "$@"
}

main "$@"
