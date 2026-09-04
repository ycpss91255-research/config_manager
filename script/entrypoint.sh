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

# The smallest config-list.toml core/models will accept: list_version plus a
# defaults.permissions block. `files` defaults to an empty list, so it is left
# out -- nothing is managed yet, which is the correct state on a first run.
#
# Values are the design's own example (§4.3), not invented here.
#
# THIS DUPLICATES THE SHAPE core/models DECLARES, and that is a real cost. It
# does not go silently wrong, though: preflight runs later in this same startup
# and load() rejects a seed that stopped being valid. Drift fails loudly, here,
# rather than surfacing in some request later.
#
# It is committed, not left untracked. io/git.record starts with `git add -A`,
# so an untracked seed would be swept into whichever user change happened to be
# recorded first -- and that record would then claim changes it does not hold.
seed_config_list() {
  local repo="$1"
  local list="${repo}/config-list.toml"

  cat >"${list}" <<'TOML' || die "could not write ${list}"
list_version = 1

# 未個別指定時套用的預設權限
[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
TOML

  # -c over `git config`: the identity belongs to this one commit, not to the
  # repo the operator will later use.
  git -C "${repo}" add config-list.toml \
    && git -C "${repo}" \
      -c user.name="config_manager" \
      -c user.email="config_manager@localhost" \
      commit --quiet -m "chore(repo): 初始化空的 config 清單檔" \
    || die "could not commit the initial ${list}"

  printf 'entrypoint: seeded an empty config list at %s\n' "${list}"
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
    seed_config_list "${repo}"
    # Falls through to check_config_list on purpose. The seed duplicates the
    # shape core/models declares, and this is what keeps that duplication from
    # going quietly wrong: a seed that stopped being valid is rejected here, in
    # the same startup that wrote it, rather than in some later request.
  else
    git -C "${repo}" rev-parse --git-dir >/dev/null 2>&1 \
      || die "config-repo mount ${repo} exists but is not a git repository"
  fi

  check_config_list "${repo}"
}

# The list file and the source content it references. Delegated to Python
# because the judgement belongs to core/config_list -- reimplementing "is this
# list file valid" in shell would be a second, quietly diverging answer to a
# question that already has one.
#
# It runs BEFORE `exec "$@"`, which is the whole point: a broken list file must
# stop the container here, not surface in some request once the service is up.
check_config_list() {
  local repo="$1"
  local output

  if ! output="$(python -m config_manager.io.preflight "${repo}" 2>&1)"; then
    die "${output}"
  fi
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
