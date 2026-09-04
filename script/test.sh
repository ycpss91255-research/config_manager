#!/usr/bin/env bash
#
# The three test axes (§3.6.1), kept separate because they answer different
# questions and conflating them into one "four categories" list is the
# mistake this layout exists to avoid:
#
#   lint   static analysis  -- not a dynamic test level at all
#   level  unit / integration / system / acceptance -- scope
#   type   smoke / e2e / regression -- purpose, applied at some level
#
# Default runs lint + every level with coverage.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

usage() {
  cat <<'USAGE'
Usage: script/test.sh [--level <name>] [--lint [<tool>]] [--file <path>] [--filter <regex>]

  (no arguments)      lint + all levels + coverage
  --level <name>      unit | integration | system | acceptance
  --lint [<tool>]     all linters, or one of:
                      ruff | mypy | pylint | shellcheck | hadolint | commit
  --file <path>       a single spec file
  --filter <regex>    specs matching a pattern
USAGE
}

# A linter whose tool is absent must NOT pass quietly. "lint passed" has to
# mean "lint ran"; anything else is the silent-success invariant 2 forbids,
# and it already cost us -- hadolint was skipped locally on every run while
# CI failed on it, so a Dockerfile finding sat unnoticed across six pushes.
# Set CM_LINT_ALLOW_MISSING=1 to downgrade the abort to a warning while
# working on a host that lacks a tool on purpose.
require_tool() {
  local tool="$1" how="$2"
  command -v "${tool}" >/dev/null 2>&1 && return 0
  if [[ "${CM_LINT_ALLOW_MISSING:-}" == "1" ]]; then
    printf 'test.sh: %s not installed -- SKIPPED, this run did not lint it\n' "${tool}" >&2
    return 1
  fi
  printf 'test.sh: %s is not installed, so this check cannot run.\n' "${tool}" >&2
  printf 'test.sh: install it (%s), or set CM_LINT_ALLOW_MISSING=1 to skip with a warning.\n' "${how}" >&2
  exit 1
}

run_lint() {
  local tool="${1:-all}"
  cd "${REPO_ROOT}"
  case "${tool}" in
    ruff|all) require_tool ruff 'pip install -r config/pip/requirements-dev.txt' && ruff check src test ;;&
    mypy|all) require_tool mypy 'pip install -r config/pip/requirements-dev.txt' && mypy --strict src/core ;;&
    pylint|all) require_tool pylint 'pip install -r config/pip/requirements-dev.txt' && pylint src ;;&
    shellcheck|all)
      if require_tool shellcheck 'apt-get install shellcheck'; then
        local -a _sh=()
        mapfile -t _sh < <(find script -name '*.sh' -type f | sort)
        shellcheck --severity=warning "${_sh[@]}"
      fi
      ;;&
    hadolint|all)
      require_tool hadolint 'https://github.com/hadolint/hadolint/releases' \
        && hadolint --config .hadolint.yaml Dockerfile
      ;;&
    commit|all) ./script/lint_commit.sh ;;&
    ruff|mypy|pylint|shellcheck|hadolint|commit|all) return 0 ;;
    *) printf 'test.sh: unknown linter %s\n' "${tool}" >&2; return 2 ;;
  esac
}

main() {
  cd "${REPO_ROOT}"

  if (( $# == 0 )); then
    run_lint all
    exec pytest test/pytest --cov=src/core --cov-report=term-missing
  fi

  case "$1" in
    --lint) shift; run_lint "${1:-all}" ;;
    --level) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest "test/pytest/$1" ;;
    --file) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest "$1" ;;
    --filter) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest test/pytest -k "$1" ;;
    -h|--help) usage ;;
    *) printf 'test.sh: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
}

main "$@"
