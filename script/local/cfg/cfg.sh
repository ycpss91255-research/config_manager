#!/usr/bin/env bash
#
# Forwards `just cfg <verb>` to the CLI, which is an HTTP client for the
# same endpoints the browser uses (ADR-00000009).
#
# The CLI does not exist yet -- it arrives with the API in v0.1.0. Until it
# does, this fails loudly and says so. A placeholder that failed with a
# ModuleNotFoundError would be a worse version of the same message, and one
# that succeeded silently would be the failure mode invariant 2 exists to
# prevent.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
readonly REPO_ROOT

if [[ ! -f "${REPO_ROOT}/src/config_manager/api/cli.py" ]]; then
  printf 'cfg: the CLI does not exist yet.\n' >&2
  printf 'cfg: it is an API client, so it arrives with the API in v0.1.0.\n' >&2
  printf 'cfg: see https://github.com/ycpss91255-research/config_manager/milestone/1\n' >&2
  exit 1
fi

cd "${REPO_ROOT}"
exec python -m config_manager.api.cli "$@"
