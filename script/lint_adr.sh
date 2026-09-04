#!/usr/bin/env bash
#
# ADR lint. Checks doc/adr/ filenames, numbering, and structure.
#
# The rules are the ones ADR-00000017 records and doc/adr/README.md states.
# The fail/warn split mirrors that document (design §0.5): fail on what is
# unambiguous, warn on a signal that is sometimes legitimate.
#
#   filename        must match NNNNNNNN-<slug>.md (8-digit zero-padded)  -> fail
#   duplicate       two ADRs share a number                             -> fail
#   > 服務：        the Serves backref must be present                  -> fail
#   sections        ## Context / ## Decision / ## Consequences          -> fail per missing
#   Status          Accepted | Rejected | Superseded by ADR-NNNNNNNN    -> fail otherwise
#   gap             a missing number in the run                         -> warn
#   ## Alternatives absent                                              -> warn
#
# README.md is the one exempt non-ADR file. Every message names the file and
# the item. Written for bash 3.2 so a stock macOS shell runs it too.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: script/lint_adr.sh [<adr-dir>]

  <adr-dir>  Directory of ADR files to check (default: doc/adr).

  fail  filename must match NNNNNNNN-<slug>.md
  fail  duplicate ADR number
  fail  missing "> 服務：" backref
  fail  missing ## Context / ## Decision / ## Consequences
  fail  Status not in: Accepted | Rejected | Superseded by ADR-NNNNNNNN
  warn  a gap in the numbering
  warn  missing ## Alternatives
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac
  local dir="${1:-doc/adr}"

  if [[ ! -d "${dir}" ]]; then
    printf 'lint_adr: %s is not a directory; nothing to check.\n' "${dir}"
    return 0
  fi

  local failures=0 warnings=0 count=0
  local -a entries=() # "NNNNNNNN basename" per well-named ADR, for dup/gap

  local path base num section st
  for path in "${dir}"/*.md; do
    [[ -e "${path}" ]] || continue
    base="$(basename "${path}")"
    [[ "${base}" == "README.md" ]] && continue

    if [[ ! "${base}" =~ ^[0-9]{8}-.+\.md$ ]]; then
      printf 'FAIL %s  filename must match NNNNNNNN-<slug>.md\n' "${base}" >&2
      failures=$((failures + 1))
      continue
    fi

    count=$((count + 1))
    num="${base:0:8}"
    entries+=("${num} ${base}")

    if ! grep -q '^> 服務' "${path}"; then
      printf 'FAIL %s  missing "> 服務：" backref\n' "${base}" >&2
      failures=$((failures + 1))
    fi

    for section in Context Decision Consequences; do
      if ! grep -qx "## ${section}" "${path}"; then
        printf 'FAIL %s  missing "## %s" section\n' "${base}" "${section}" >&2
        failures=$((failures + 1))
      fi
    done

    st=""
    if grep -q '^- \*\*Status:\*\*' "${path}"; then
      st="$(grep -m1 '^- \*\*Status:\*\*' "${path}" |
        sed -E 's/^- \*\*Status:\*\*[[:space:]]*//; s/[[:space:]]*$//')"
    fi
    if [[ ! "${st}" =~ ^(Accepted|Rejected|Superseded\ by\ ADR-[0-9]{8})$ ]]; then
      printf 'FAIL %s  Status "%s" not in: Accepted | Rejected | Superseded by ADR-NNNNNNNN\n' \
        "${base}" "${st}" >&2
      failures=$((failures + 1))
    fi

    if ! grep -qx '## Alternatives' "${path}"; then
      printf 'WARN %s  missing "## Alternatives" section\n' "${base}" >&2
      warnings=$((warnings + 1))
    fi
  done

  if ((count > 0)); then
    # Duplicate numbers -> fail; name every file that shares the number.
    local dup files
    while IFS= read -r dup; do
      files="$(printf '%s\n' "${entries[@]}" | awk -v n="${dup}" '$1 == n { printf "%s ", $2 }')"
      printf 'FAIL duplicate number %s: %s\n' "${dup}" "${files}" >&2
      failures=$((failures + 1))
    done < <(printf '%s\n' "${entries[@]}" | awk '{ print $1 }' | sort | uniq -d)

    # Gap in the run -> warn.
    local first last n expect
    first="$(printf '%s\n' "${entries[@]}" | awk '{ print $1 }' | sort -u | head -1)"
    last="$(printf '%s\n' "${entries[@]}" | awk '{ print $1 }' | sort -u | tail -1)"
    for ((n = 10#${first}; n <= 10#${last}; n++)); do
      expect="$(printf '%08d' "${n}")"
      if ! printf '%s\n' "${entries[@]}" | awk -v e="${expect}" '$1 == e { f = 1 } END { exit !f }'; then
        printf 'WARN missing number %s (gap in the sequence)\n' "${expect}" >&2
        warnings=$((warnings + 1))
      fi
    done
  fi

  printf 'lint_adr: %d ADR(s) in %s -- %d failure(s), %d warning(s)\n' \
    "${count}" "${dir}" "${failures}" "${warnings}"
  ((failures == 0))
}

main "$@"
