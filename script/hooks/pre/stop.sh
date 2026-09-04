#!/usr/bin/env bash
# pre-stop hook. Runs before `just docker stop`.
#
# Empty by design. This is the seam the shared container template defines;
# it is placeheld and wired now so that adding a step later means editing
# this file only, not the wrapper. A failing pre-hook aborts the operation.
set -euo pipefail
