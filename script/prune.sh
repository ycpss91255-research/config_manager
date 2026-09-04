#!/usr/bin/env bash
#
# Reclaim what this repo's builds left behind. Scoped to this project by
# default: a prune that reaches other people's images is the kind of
# convenience that costs someone an afternoon, so the wide form is opt-in
# and spelled out.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

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
  docker compose rm --stop --force
  docker image prune --force --filter "label=com.docker.compose.project=config_manager"

  if (( wide )); then
    # Images only. The volume holds the source of truth and is removed by
    # `docker compose down --volumes` and nothing else.
    docker image rm --force config_manager:devel config_manager:runtime 2>/dev/null || true
    printf 'prune.sh: removed built images; the config_repo volume is untouched.\n'
  fi
}

main "$@"
