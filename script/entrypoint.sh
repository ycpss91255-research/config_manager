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

  if [[ ! -e "${repo}/.git" ]]; then
    # EMPTY and NON-EMPTY are different situations and this used to conflate
    # them: the comment said "empty directory" while the condition only asked
    # whether .git was absent, so a directory full of files was initialised and
    # announced as empty (#69).
    #
    # It matters because io/git.record starts with `git add -A`. Initialising
    # over someone else's files sweeps every one of them into the first commit,
    # and a mistyped mount path looks like a successful startup.
    if [[ -n "$(ls -A "${repo}")" ]]; then
      die "config-repo mount ${repo} is not empty but is not under version control;" \
        "initialise it deliberately (git init) or check the mount path"
    fi

    # An empty directory IS the first-run case: the volume exists, nothing has
    # initialised it yet. A mount pointed at the wrong empty directory cannot be
    # told apart from that from in here -- so print the resolved absolute path
    # and let whoever reads it recognise their own mistake, rather than pretend
    # the difference is knowable.
    printf 'entrypoint: initialising empty config-repo at %s\n' "$(cd "${repo}" && pwd)"
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
