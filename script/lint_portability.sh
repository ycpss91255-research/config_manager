#!/usr/bin/env bash
#
# Portability lint. Fails when a shell script uses an option only GNU tools
# accept.
#
# These scripts run in two places: CI and the check image are Linux with GNU
# coreutils, while a contributor's machine may be macOS with BSD ones. A GNU-only
# option is green in CI forever and only breaks on macOS -- and not always
# loudly. `grep -P` did exactly that here: BSD grep rejected the option, the
# Chinese-subject check in lint_commit stopped working, and it reported that a
# correct Chinese subject "has no Chinese". A wrong answer, not an error.
#
# THE LIST COMES FROM MEASUREMENT, NOT MEMORY. Each option below was run on
# macOS and observed to fail. Three plausible candidates were run and did NOT
# fail -- `readlink -f`, `sort -V`, `xargs -r` -- so they are deliberately not
# listed. Re-measure before adding to this list.
#
# This file excludes itself from the scan: it is the one script that has to
# spell the offending constructs out, in its rules and in its usage text.
set -euo pipefail

SELF="$(basename "${BASH_SOURCE[0]}")"
readonly SELF

usage() {
  cat <<'USAGE'
Usage: script/lint_portability.sh [<dir>]

  <dir>  Directory of shell scripts to check (default: script).

  fail  an option only GNU tools accept, in a non-comment line

Measured on macOS as failing with BSD tools: grep with -P, sed -i without a
backup suffix, find with -printf, stat with -c, date with -d, base64 with -w,
head with a negative -n, cp with --parents.
USAGE
}

# name<TAB>extended-regex. The regexes deliberately anchor on the command name
# followed by whitespace, so this table does not match itself.
_rules() {
  cat <<'RULES'
grep -P	(^|[^[:alnum:]_-])grep[[:space:]]+-[[:alnum:]]*P
find -printf	(^|[^[:alnum:]_-])find[[:space:]].*[[:space:]]-printf
stat -c	(^|[^[:alnum:]_-])stat[[:space:]]+-[[:alnum:]]*c
date -d	(^|[^[:alnum:]_-])date[[:space:]]+-d[[:space:]]
base64 -w	(^|[^[:alnum:]_-])base64[[:space:]]+-[[:alnum:]]*w
head -n -	(^|[^[:alnum:]_-])head[[:space:]]+-n[[:space:]]+-[0-9]
cp --parents	(^|[^[:alnum:]_-])cp[[:space:]].*--parents
RULES
}

# Non-comment lines, each keeping its original line number.
_code_lines() {
  awk '!/^[[:space:]]*#/ { printf "%d:%s\n", NR, $0 }' "$1"
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac
  local dir="${1:-script}"

  if [[ ! -d "${dir}" ]]; then
    printf 'lint_portability: %s is not a directory; nothing to check.\n' "${dir}"
    return 0
  fi

  local -a files=()
  mapfile -t files < <(find "${dir}" -name '*.sh' -type f | sort)
  if ((${#files[@]} == 0)); then
    printf 'lint_portability: no shell scripts in %s; nothing to check.\n' "${dir}"
    return 0
  fi

  local failures=0 file code name regex hits
  for file in "${files[@]}"; do
    [[ "$(basename "${file}")" == "${SELF}" ]] && continue
    code="$(_code_lines "${file}")"

    while IFS=$'\t' read -r name regex; do
      [[ -n "${name}" ]] || continue
      hits="$(printf '%s\n' "${code}" | grep -E "${regex}" || true)"
      [[ -n "${hits}" ]] || continue
      while IFS= read -r hit; do
        printf 'FAIL  %s:%s  uses %s, which BSD tools reject\n' \
          "${file}" "${hit%%:*}" "${name}" >&2
        failures=$((failures + 1))
      done <<<"${hits}"
    done < <(_rules)

    # sed -i is its own rule: GNU takes no backup suffix, BSD requires one, and
    # `sed -i ''` is the form both accept -- so only the suffix-less form fails.
    hits="$(printf '%s\n' "${code}" |
      grep -E "(^|[^[:alnum:]_-])sed[[:space:]]+-[[:alnum:]]*i" |
      grep -vE "sed[[:space:]]+-i[[:space:]]+(''|\"\")" || true)"
    if [[ -n "${hits}" ]]; then
      while IFS= read -r hit; do
        printf 'FAIL  %s:%s  uses sed -i with no backup suffix, which BSD rejects\n' \
          "${file}" "${hit%%:*}" >&2
        printf "      write it as: sed -i '' ...\n" >&2
        failures=$((failures + 1))
      done <<<"${hits}"
    fi
  done

  printf 'lint_portability: %d script(s) in %s -- %d violation(s)\n' \
    "${#files[@]}" "${dir}" "${failures}"
  ((failures == 0))
}

main "$@"
