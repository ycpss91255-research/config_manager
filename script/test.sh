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
  --lint [<tool>]     all linters, or one of: ruff | mypy | pylint | hadolint | commit
  --file <path>       a single spec file
  --filter <regex>    specs matching a pattern
USAGE
}

run_lint() {
  local tool="${1:-all}"
  cd "${REPO_ROOT}"
  case "${tool}" in
    ruff|all) ruff check src test ;;&
    mypy|all) mypy --strict src/core ;;&
    pylint|all) pylint src ;;&
    hadolint|all)
      if command -v hadolint >/dev/null 2>&1; then
        hadolint Dockerfile
      else
        printf 'test.sh: hadolint not installed, skipping\n' >&2
      fi
      ;;&
    commit|all) ./script/lint_commit.sh ;;&
    ruff|mypy|pylint|hadolint|commit|all) return 0 ;;
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
