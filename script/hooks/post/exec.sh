#!/usr/bin/env bash
# post-exec hook. Runs after `just docker exec`.
#
# Empty by design. This is the seam the shared container template defines;
# it is placeheld and wired now so that adding a step later means editing
# this file only, not the wrapper. A failing pre-hook aborts the operation.
set -euo pipefail

# NOTE: nothing calls this. exec.sh hands the terminal over with `exec` and
# never returns, so a post-hook could only ever be a promise that silently
# does not run. Kept for layout symmetry with the template, inert on purpose.
