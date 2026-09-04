#!/usr/bin/env bash
#
# Stop the services. Containers only -- the config_repo volume survives,
# because it holds the source of truth and a stop is not a decision to
# discard it. Removing it is `docker compose down --volumes`, spelled out in
# full so it cannot happen by reflex.
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
