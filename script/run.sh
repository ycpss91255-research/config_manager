#!/usr/bin/env bash
#
# Start the two services. The host-side precondition check lives in
# script/hooks/pre/run.sh, which this calls before bringing anything up.
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
