#!/usr/bin/env bash
#
# Container bringup. This IS the container entrypoint (the Dockerfile's
# ENTRYPOINT), not a fragment sourced by an orchestrator -- the shared
# template's two-part entrypoint arrives with the template, which v0.10.0
# does not adopt.
#
# One contract is honoured regardless: the last thing this file does is
# `exec "$@"`. Without it the workload runs as a child of this shell, PID 1
# stays here, and SIGTERM never reaches the thing that needs to shut down --
# a container that cannot be stopped cleanly.
#
# frontend and backend share this file. What differs is the command they are
# handed, so the checks below are the ones that hold for both, plus a
# backend-only block guarded by CM_ROLE.
set -euo pipefail

die() {
  # Errors name a file and a reason. "Startup failed" tells the operator
  # nothing they can act on (design principle: an error message must be
  # actionable), and startup is exactly when nobody is watching closely.
  printf 'entrypoint: %s\n' "$*" >&2
  exit 1
}

check_backend_preconditions() {
  local repo="${CM_CONFIG_REPO:-}"

  [[ -n "${repo}" ]] || die "CM_CONFIG_REPO is unset; the backend has no source repo to serve"
  [[ -d "${repo}" ]] || die "config-repo mount ${repo} does not exist (is the volume mounted?)"

  # An empty directory is the first-run case, not a fault: the volume exists
  # but nothing has initialised it yet. Say so and initialise, rather than
  # failing on a state the operator cannot distinguish from a real problem.
  if [[ ! -e "${repo}/.git" ]]; then
    printf 'entrypoint: initialising empty config-repo at %s\n' "${repo}"
    git init --quiet --initial-branch=main "${repo}" || die "git init failed at ${repo}"
    return 0
  fi

  git -C "${repo}" rev-parse --git-dir >/dev/null 2>&1 \
    || die "config-repo mount ${repo} exists but is not a git repository"
}

main() {
  case "${CM_ROLE:-backend}" in
    backend) check_backend_preconditions ;;
    frontend) ;;
    *) die "unknown CM_ROLE '${CM_ROLE}'; expected 'backend' or 'frontend'" ;;
  esac

  # Hand over. Nothing may follow this line.
  exec "$@"
}

main "$@"
