#!/usr/bin/env bash
# pre-run hook. Runs before `just docker run`.
#
# Confirm the config-repo mount point exists on the HOST. Docker silently
# creates a root-owned empty directory when a bind-mount source is missing,
# and the first symptom is a permission error deep inside an apply -- far in
# both time and place from the cause. Failing here puts the error next to
# the thing that is actually wrong.
set -euo pipefail

TARGET_ROOT="${CM_TARGET_ROOT:-/opt/robot/config}"

if [[ ! -d "${TARGET_ROOT}" ]]; then
  printf 'pre-run: target root %s does not exist on the host.\n' "${TARGET_ROOT}" >&2
  printf 'pre-run: create it first (sudo mkdir -p %s), or set CM_TARGET_ROOT.\n' "${TARGET_ROOT}" >&2
  printf 'pre-run: Docker would otherwise create it root-owned and apply would fail later.\n' >&2
  exit 1
fi
