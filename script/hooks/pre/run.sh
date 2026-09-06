#!/usr/bin/env bash
# pre-run hook：在 `just docker run` 之前執行。
#
# 確認 config-repo 的掛載點在**主機上**存在。bind-mount 的來源不存在時，Docker
# 會靜默地建一個 root 所有的空目錄，而第一個症狀是寫出過程深處的權限錯誤——在
# 時間與位置上都離成因很遠。在這裡就失敗，能把錯誤放在真正出問題的東西旁邊。
set -euo pipefail

TARGET_ROOT="${CM_TARGET_ROOT:-/opt/robot/config}"

if [[ ! -d "${TARGET_ROOT}" ]]; then
  printf 'pre-run: 目標位置的根目錄 %s 在主機上不存在。\n' "${TARGET_ROOT}" >&2
  printf 'pre-run: 否則 Docker 會用 root 建出那個目錄，之後的寫出會失敗。\n' >&2
  printf 'pre-run: 下一步：先建起來（sudo mkdir -p %s），或設 CM_TARGET_ROOT 指到別處\n' "${TARGET_ROOT}" >&2
  exit 1
fi
