#!/usr/bin/env bash
#
# Start the two services. The precondition check that would live in
# script/hooks/pre/run.sh under the shared template runs here: the target
# directory must exist on the HOST before the mount is made, because Docker
# silently creates a root-owned empty directory when it does not -- and the
# first symptom is a permission error deep inside an apply, far from the
# cause.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
readonly TARGET_ROOT="${CM_TARGET_ROOT:-/opt/robot/config}"

main() {
  if [[ ! -d "${TARGET_ROOT}" ]]; then
    printf 'run.sh: target root %s does not exist on the host.\n' "${TARGET_ROOT}" >&2
    printf 'run.sh: create it first (sudo mkdir -p %s), or set CM_TARGET_ROOT.\n' "${TARGET_ROOT}" >&2
    printf 'run.sh: Docker would otherwise create it root-owned and apply would fail later.\n' >&2
    exit 1
  fi

  cd "${REPO_ROOT}"
  docker compose up -d "$@"
  docker compose ps
}

main "$@"
