#!/usr/bin/env bash
#
# Commit-message lint. Checks the commits this branch adds, not history.
#
# The rules are derived from ycpss91255-docker/base, sampled over its 200
# most recent commits, because "align with base" is only checkable if what
# base actually does is written down somewhere a tool reads:
#
#   type      fix 63 / feat 38 / refactor 31 / docs 26 / test 15 /
#             chore 14 / ci 7 / perf 6                       -> 8 types, fail
#   scope     181 of 200 carry one                           -> warn when absent
#   subject   188 of 200 start lowercase                     -> not enforced
#   length    median 90, max 153                             -> not enforced
#   issue     118 of 200 carry (closes #N) / (refs #N)       -> not enforced
#
# CASE IS NOT ENFORCED, and this line used to claim it was. ADR-00000028 moved
# subjects to Chinese, which has no letter case, so the rule became true of
# every commit this repo will ever write -- a check that can never fire. The
# stale claim was itself the defect: #70 listed "capitalized subject -> warn"
# among the rules needing tests, copied from here, and no such rule exists.
#
# The fail/warn split mirrors the ADR lint (§0.5): fail on what is
# unambiguous, warn on what is a signal. A missing scope is usually a
# commit that touches too much, but sometimes it genuinely spans the repo --
# so it shows up in the output without blocking the merge.
#
# LENGTH IS DELIBERATELY NOT CHECKED. base's median title is 90 characters
# and its longest is 153; the conventional 50-character rule would reject
# most of the repo this lint exists to align with. base writes a declarative
# sentence saying what is now true ("base owns the orchestrator, the repo
# owns its bringup"), not an imperative naming a change ("add orchestrator").
# That is the house style, and a length cap would quietly fight it.
#
# LANGUAGE IS THIS REPO'S OWN RULE, not base's (ADR-00000028). base writes
# English because base is read by whoever consumes the template; this repo is
# not a base downstream and keeps its record in Chinese. So the machine-read
# half of the line -- type and scope -- stays as base defines it, and the half
# a human reads is written in the language the humans here use.
#
# HISTORY IS NOT LINTED. Only origin/main..HEAD -- the commits a branch
# proposes. Existing commits predate this rule and rewriting them would mean
# force-pushing a branch other people have.
set -euo pipefail

readonly TYPES="feat|fix|docs|refactor|test|chore|ci|perf"

usage() {
  cat <<'USAGE'
Usage: script/lint_commit.sh [<base-ref>]

  <base-ref>  Commits after this ref are checked (default: origin/main).

Rules, derived from ycpss91255-docker/base:

  fail  type must be one of: feat fix docs refactor test chore ci perf
  fail  the "type(scope): " prefix must be present and well-formed
  fail  subject must not end with a period
  fail  subject must contain Chinese (ADR-00000028); type/scope stay English
  warn  scope should be present -- type(scope): not bare type:

Not checked: title length, issue references. See this file's header for why.
USAGE
}

main() {
  case "${1:-}" in -h|--help) usage; return 0 ;; esac
  local base="${1:-origin/main}"

  if ! git rev-parse --verify --quiet "${base}" >/dev/null; then
    printf 'lint_commit: base ref %s does not exist; nothing to check.\n' "${base}"
    return 0
  fi

  # --no-merges: a merge commit's message is generated, not authored. CI
  # checks out refs/pull/N/merge on a pull request, so HEAD is a synthetic
  # "Merge <sha> into <sha>" that no convention could ever match -- and a
  # real merge of main into a branch is equally not the author's prose.
  local -a shas=()
  mapfile -t shas < <(git rev-list --no-merges "${base}..HEAD")
  if (( ${#shas[@]} == 0 )); then
    printf 'lint_commit: no commits after %s; nothing to check.\n' "${base}"
    return 0
  fi

  local failures=0 warnings=0 sha subject short
  for sha in "${shas[@]}"; do
    subject="$(git log -1 --format=%s "${sha}")"
    short="${sha:0:7}"

    # A squash merge appends " (#123)"; strip it before matching so a
    # already-merged commit re-checked on a branch does not trip the rules.
    local body="${subject% (#[0-9]*)}"

    if [[ ! "${body}" =~ ^(${TYPES})(\([A-Za-z0-9._/,-]+\))?:\  ]]; then
      printf 'FAIL %s  %s\n' "${short}" "${subject}" >&2
      if [[ "${body}" =~ ^([A-Za-z]+)(\(.*\))?: ]]; then
        printf '     type %s is not one of: %s\n' "${BASH_REMATCH[1]}" "${TYPES//|/ }" >&2
      else
        printf '     missing the "type(scope): " prefix\n' >&2
      fi
      printf '     rewrite as: <type>(<scope>): <lowercase sentence saying what is now true>\n' >&2
      failures=$(( failures + 1 ))
      continue
    fi

    # Both periods: the rule predates ADR-00000028, when subjects were English
    # and the only full stop was ASCII. Subjects are Chinese now, so the one a
    # writer actually types is U+3002 -- checking only "." left the rule
    # unenforced for the language this repo writes in.
    if [[ "${body}" == *. || "${body}" == *"。" ]]; then
      printf 'FAIL %s  %s\n' "${short}" "${subject}" >&2
      printf '     subject ends with a period (. or 。); drop it\n' >&2
      failures=$(( failures + 1 ))
      continue
    fi

    # The half a human reads is Chinese; type and scope stay as base defines
    # them because they are read by tools (ADR-00000028).
    # Byte-level non-ASCII, not a CJK range: grep -P is GNU-only (BSD grep on
    # macOS rejects -P outright, so the check silently mis-fired there and told
    # a correct Chinese subject it had none), and a UTF-8 literal range like
    # [一-鿿] is rejected by GNU grep under the image's C.UTF-8 locale
    # ("Invalid collation character"). LC_ALL=C makes it a byte test both greps
    # agree on: an all-English subject carries no byte above 0x7E, a Chinese
    # one does.
    if ! printf '%s' "${body#*: }" | LC_ALL=C grep -q '[^ -~]'; then
      printf 'FAIL %s  %s\n' "${short}" "${subject}" >&2
      printf '     subject has no Chinese. This repo keeps its record in Chinese;\n' >&2
      printf '     only the type(scope) prefix stays as base defines it.\n' >&2
      printf '     e.g. feat(core): 清單檔載入時攔截未知欄位\n' >&2
      failures=$(( failures + 1 ))
      continue
    fi

    if [[ ! "${body}" =~ ^(${TYPES})\( ]]; then
      printf 'WARN %s  %s\n' "${short}" "${subject}" >&2
      printf '     no scope. base carries one on 181 of its 200 most recent commits;\n' >&2
      printf '     a commit with no nameable scope often touches too much\n' >&2
      warnings=$(( warnings + 1 ))
    fi

  done

  printf 'lint_commit: %d commit(s) after %s -- %d failure(s), %d warning(s)\n' \
    "${#shas[@]}" "${base}" "${failures}" "${warnings}"
  (( failures == 0 ))
}

main "$@"
