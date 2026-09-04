#!/usr/bin/env bash
#
# Build the image. Defaults to the `devel` stage; `--stage` picks another.
#
# Every operation the UI offers has a CLI equal (design principle N-5), and
# that rule starts at the container operations: nothing here is reachable
# only by remembering a `docker compose` incantation.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
# shellcheck source=hooks/dispatch.sh
source "${REPO_ROOT}/script/hooks/dispatch.sh"

usage() {
  cat <<'USAGE'
Usage: script/build.sh [--stage <name>] [--no-cache]

  --stage <name>  Dockerfile stage to build (default: devel).
                  sys | devel-base | devel | runtime | runtime-test
  --no-cache      Build without the layer cache.
USAGE
}

main() {
  local stage="devel"
  local -a extra=()

  while (( $# )); do
    case "$1" in
      --stage) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; stage="$1" ;;
      --no-cache) extra+=("--no-cache") ;;
      -h|--help) usage; return 0 ;;
      *) printf 'build.sh: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
  done

  cd "${REPO_ROOT}"
  run_hook pre build "${stage}"
  CM_STAGE="${stage}" CM_TAG="${stage}" \
    docker compose build "${extra[@]}"
  run_hook post build "${stage}"
}

main "$@"
