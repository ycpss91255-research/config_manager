#!/usr/bin/env bash
#
# Open a shell (or run a command) in a running service. Defaults to backend,
# the container that holds every capability worth inspecting.
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
  # No post-exec hook: this hands the terminal over with exec and never
  # returns. A hook registered to run "after" would silently never run,
  # which is worse than not offering one.
  if (( $# == 0 )); then
    exec docker compose exec "${service}" bash
  fi
  exec docker compose exec "${service}" "$@"
}

main "$@"
