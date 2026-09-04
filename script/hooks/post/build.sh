#!/usr/bin/env bash
# post-build hook：在 `just docker build` 之後執行，$1 是建置的階段。
#
# 跑 runtime 的 smoke 階段。runtime-test 是 FROM runtime，並在它自己的建置過程中
# 執行 bats 規格，所以這支 hook 跑到結尾就代表可部署的產物真的起得來——而不只是
# 編得過。
set -euo pipefail

stage="${1:-devel}"
[[ "${stage}" == "runtime" ]] || exit 0

printf 'post-build: running runtime smoke stage\n'
CM_STAGE="runtime-test" CM_TAG="runtime-test" docker compose build backend
