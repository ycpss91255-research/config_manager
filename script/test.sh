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
readonly TEST_IMAGE="config_manager-test-tools:local"
readonly TEST_DOCKERFILE="docker/Dockerfile.test-tools"

usage() {
  cat <<'USAGE'
Usage: script/test.sh [--level <name>] [--lint [<tool>]] [--file <path>] [--filter <regex>]

  (no arguments)      lint + all levels + coverage
  --level <name>      unit | integration | system | acceptance
  --lint [<tool>]     all linters, or one of:
                      ruff | mypy | pylint | shellcheck | hadolint | actionlint | commit | adr | paths
  --file <path>       a single spec file
  --filter <regex>    specs matching a pattern

Runs inside docker/Dockerfile.test-tools, which carries every checker.
The host is not evidence about the project: its Python, its pytest and its
absent linters have each produced a wrong answer here before.

  CM_TEST_LOCAL=1     run on this host instead. Whatever is missing is
                      named and skipped -- a loud skip, still not a check.
USAGE
}

# Re-run this script inside the image that has the tools, unless we are
# already in it. One environment, two callers: `just test` and CI take the
# same path, so a check cannot pass in one and fail in the other.
dispatch_to_container() {
  if ! command -v docker >/dev/null 2>&1; then
    printf 'test.sh: docker is not installed, so the checks cannot run in their image.\n' >&2
    printf 'test.sh: install docker, or set CM_TEST_LOCAL=1 to run on this host with whatever it has.\n' >&2
    exit 1
  fi

  # Cheap when cached; the layers only rebuild when the Dockerfile or the
  # pinned requirements actually change.
  printf 'test.sh: building %s\n' "${TEST_IMAGE}" >&2
  local -a _build=(docker build --quiet -f "${REPO_ROOT}/${TEST_DOCKERFILE}" -t "${TEST_IMAGE}")
  # The image defaults to a Taiwan Debian mirror because deb.debian.org is
  # unreachable from the networks this repo is developed on. Somewhere with
  # a different answer sets CM_APT_MIRROR rather than editing the file.
  [[ -n "${CM_APT_MIRROR:-}" ]] && _build+=(--build-arg "APT_MIRROR=${CM_APT_MIRROR}")
  "${_build[@]}" "${REPO_ROOT}" >/dev/null

  # The repo is mounted rather than copied so a failing check names a path
  # that exists on the host and an edit does not need a rebuild. A mount is
  # two-way, which is why --user matters: the image's own user is root, and a
  # root process writing into a bind mount leaves root-owned files behind on
  # the host. That is not hypothetical here -- it cost an uncommitted edit and
  # left 47 root-owned paths, including a file inside .git.
  #
  # HOME is redirected because the invoking uid has no home in this image, and
  # git.safe.directory comes through GIT_CONFIG_* rather than a global config
  # for the same reason: with no writable HOME there is nowhere to keep one.
  exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env GIT_CONFIG_COUNT=1 \
    --env GIT_CONFIG_KEY_0=safe.directory \
    --env GIT_CONFIG_VALUE_0='*' \
    --volume "${REPO_ROOT}:/repo" \
    --workdir /repo \
    "${TEST_IMAGE}" \
    ./script/test.sh "$@"
}

# What each linter needs on PATH, and how to get it. One table, so the
# up-front survey below and the per-check guard cannot disagree about which
# tools a run requires.
_tool_install_hint() {
  case "$1" in
    ruff|mypy|pylint|pytest) printf 'pip install -r config/pip/requirements-dev.txt' ;;
    shellcheck) printf 'apt-get install shellcheck' ;;
    hadolint) printf 'https://github.com/hadolint/hadolint/releases' ;;
    actionlint) printf 'https://github.com/rhysd/actionlint/releases' ;;
    *) printf 'see docker/Dockerfile.test-tools' ;;
  esac
}

# Report EVERY tool the requested run is missing, then stop -- not the first
# one. Aborting on the first turns a host survey into install-one, rerun,
# hit-the-next; and it means the run never says what it did not check, which
# is the thing this whole guard exists to make visible.
#
# Only reachable with CM_TEST_LOCAL=1: a normal run is inside an image that
# has all of them by construction.
survey_tools() {
  local -a needed=("$@") missing=()
  local t
  for t in "${needed[@]}"; do
    command -v "${t}" >/dev/null 2>&1 || missing+=("${t}")
  done
  (( ${#missing[@]} == 0 )) && return 0

  printf 'test.sh: this host is missing %d of the %d checkers this run needs:\n' \
    "${#missing[@]}" "${#needed[@]}" >&2
  for t in "${missing[@]}"; do
    printf '  %-11s %s\n' "${t}" "$(_tool_install_hint "${t}")" >&2
  done
  if [[ "${CM_LINT_ALLOW_MISSING:-}" == "1" ]]; then
    printf 'test.sh: CM_LINT_ALLOW_MISSING=1 -- continuing, and the above did NOT run.\n' >&2
    printf 'test.sh: a run with skips is not a passing run. Prefer dropping CM_TEST_LOCAL.\n' >&2
    return 0
  fi
  printf 'test.sh: install them, drop CM_TEST_LOCAL to use the image that has them,\n' >&2
  printf 'test.sh: or set CM_LINT_ALLOW_MISSING=1 to run the rest and be told what was skipped.\n' >&2
  exit 1
}

# A linter whose tool is absent must NOT pass quietly. "lint passed" has to
# mean "lint ran"; anything else is the silent-success invariant 2 forbids,
# and it already cost us -- hadolint was skipped locally on every run while
# CI failed on it, so a Dockerfile finding sat unnoticed across six pushes.
# survey_tools has already reported and decided by the time this runs; this
# is the per-check gate that keeps a missing tool from being invoked.
require_tool() {
  command -v "$1" >/dev/null 2>&1
}

run_lint() {
  local tool="${1:-all}"
  cd "${REPO_ROOT}"

  case "${tool}" in
    all) survey_tools ruff mypy pylint shellcheck hadolint actionlint ;;
    ruff|mypy|pylint|shellcheck|hadolint|actionlint) survey_tools "${tool}" ;;
  esac

  case "${tool}" in
    ruff|all) require_tool ruff && ruff check src test ;;&
    mypy|all) require_tool mypy && mypy --strict src/config_manager/core ;;&
    pylint|all) require_tool pylint && pylint src ;;&
    shellcheck|all)
      if require_tool shellcheck; then
        local -a _sh=()
        mapfile -t _sh < <(find script -name '*.sh' -type f | sort)
        shellcheck --severity=warning "${_sh[@]}"
      fi
      ;;&
    hadolint|all)
      require_tool hadolint \
        && hadolint --config .hadolint.yaml Dockerfile
      ;;&
    actionlint|all)
      # Workflow expressions are not YAML and no YAML parser checks them.
      # A double-quoted string literal inside ${{ }} is valid YAML and an
      # invalid expression -- GitHub rejects the whole file, runs zero jobs,
      # and reports it as a run failure with no job to open. Caught here now
      # rather than by pushing and reading the aftermath.
      require_tool actionlint \
        && actionlint .github/workflows/*.yaml
      ;;&
    commit|all) ./script/lint_commit.sh ;;&
    adr|all) ./script/lint_adr.sh ;;&
    paths|all) ./script/lint_paths.sh ;;&
    ruff|mypy|pylint|shellcheck|hadolint|actionlint|commit|adr|paths|all) return 0 ;;
    *) printf 'test.sh: unknown linter %s\n' "${tool}" >&2; return 2 ;;
  esac
}

main() {
  cd "${REPO_ROOT}"

  case "${1:-}" in -h|--help) usage; return 0 ;; esac

  if [[ "${CM_IN_TEST_IMAGE:-}" != "1" && "${CM_TEST_LOCAL:-}" != "1" ]]; then
    dispatch_to_container "$@"
  fi

  if (( $# == 0 )); then
    run_lint all
    exec pytest test/pytest --cov=src/config_manager/core --cov-report=term-missing
  fi

  case "$1" in
    --lint) shift; run_lint "${1:-all}" ;;
    --level) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest "test/pytest/$1" ;;
    --file) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest "$1" ;;
    --filter) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest test/pytest -k "$1" ;;
    *) printf 'test.sh: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
}

main "$@"
