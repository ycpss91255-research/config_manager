#!/usr/bin/env bash
# Hook dispatcher, sourced by the wrappers under script/.
#
# The shared container template ships its own dispatcher and will replace
# this file when it is adopted. Until then this is what makes script/hooks/
# a real extension point rather than a directory that merely looks wired:
# every wrapper calls run_hook before and after its work, so a hook dropped
# in here runs without editing the wrapper that runs it.
#
# A missing or non-executable hook is not an error -- most stay empty. A
# hook that FAILS is: pre-hooks exist to stop the operation, so their exit
# status propagates and the wrapper's `set -e` aborts.

run_hook() {
  local phase="$1" verb="$2"
  shift 2
  local hook="${REPO_ROOT}/script/hooks/${phase}/${verb}.sh"
  [[ -x "${hook}" ]] || return 0
  "${hook}" "$@"
}
