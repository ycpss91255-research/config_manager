#!/usr/bin/env bash
# post-build hook. Runs after `just docker build`, with the built stage as $1.
#
# Run the runtime smoke stage. runtime-test is FROM runtime and executes the
# bats specs during its own build, so reaching the end of this hook means the
# deployable artifact came up -- not merely that it compiled.
set -euo pipefail

stage="${1:-devel}"
[[ "${stage}" == "runtime" ]] || exit 0

printf 'post-build: running runtime smoke stage\n'
CM_STAGE="runtime-test" CM_TAG="runtime-test" docker compose build backend
