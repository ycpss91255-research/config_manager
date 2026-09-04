#!/usr/bin/env bash
#
# Path lint. Fails when two tracked paths differ only by letter case.
#
# Linux is case-sensitive, so `Dockerfile` and `dockerfile/` are two entries
# there and CI never notices. macOS and Windows are case-insensitive: the two
# names are one entry, one of them wins the checkout, and the other simply is
# not on disk. The failure is silent and confusing -- `git status` reports a
# deletion nobody made, and the tool that wanted the missing file dies with a
# message that names neither cause nor cure.
#
# That is not hypothetical: this repo shipped exactly that pair, and on macOS
# the root Dockerfile could not be checked out at all, so `just test` stopped
# at `hadolint: Dockerfile: does not exist` while CI stayed green.
#
# CI runs on Linux, so no dynamic check can catch this -- the collision has to
# be read off the tracked paths themselves. Directory prefixes count: a file
# named `Dockerfile` collides with a directory named `dockerfile/` even though
# the directory never appears in `git ls-files`.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: script/lint_paths.sh

  fail  two tracked paths differ only by letter case (they collide on a
        case-insensitive filesystem: macOS, Windows)

Directory prefixes are included, so a file and a directory whose names differ
only by case are caught too.
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  # Every tracked path plus every directory prefix of it, deduplicated.
  local paths
  paths="$(git ls-files |
    awk -F/ '{ p = ""; for (i = 1; i <= NF; i++) { p = (i == 1 ? $i : p "/" $i); print p } }' |
    sort -u)"

  if [[ -z "${paths}" ]]; then
    printf 'lint_paths: no tracked paths; nothing to check.\n'
    return 0
  fi

  local failures=0 key originals
  while IFS= read -r key; do
    [[ -n "${key}" ]] || continue
    originals="$(printf '%s\n' "${paths}" | awk -v k="${key}" 'tolower($0) == k { printf "%s ", $0 }')"
    printf 'FAIL  these paths differ only by case: %s\n' "${originals}" >&2
    printf '      on macOS/Windows only one of them exists; the rest vanish from the checkout\n' >&2
    failures=$((failures + 1))
  done < <(printf '%s\n' "${paths}" | awk '{ print tolower($0) }' | sort | uniq -d)

  local count
  count="$(printf '%s\n' "${paths}" | grep -c .)"
  printf 'lint_paths: %d tracked path(s) checked -- %d collision(s)\n' "${count}" "${failures}"
  ((failures == 0))
}

main "$@"
