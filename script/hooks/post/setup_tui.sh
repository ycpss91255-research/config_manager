#!/usr/bin/env bash
# post-setup_tui hook. Runs after `just docker setup_tui`.
#
# Empty by design. This is the seam the shared container template defines;
# it is placeheld and wired now so that adding a step later means editing
# this file only, not the wrapper. A failing pre-hook aborts the operation.
set -euo pipefail

# NOTE: nothing calls this yet. `setup` and `setup_tui` are the template's
# own wrappers (config parsing + host detection), which appendix A places in
# the "do not build yet" column. The hook is here so the seam exists when
# that wrapper arrives; until then it is inert by absence of a caller, not
# by accident.
