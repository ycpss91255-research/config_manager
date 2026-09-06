#!/usr/bin/env bash
#
# 建置映像。預設 `devel` 階段，`--stage` 可換成別的。
#
# 介面提供的每個操作都有 CLI 對等（設計原則 N-5），而這條規則從容器操作就開始：
# 這裡沒有任何一件事是「只有記得某串 docker compose 咒語才做得到」。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
# shellcheck source=hooks/dispatch.sh
source "${REPO_ROOT}/script/hooks/dispatch.sh"

usage() {
  cat <<'USAGE'
用法：script/build.sh [--stage <name>] [--no-cache]

  --stage <name>  要建置的 Dockerfile 階段（預設 devel）。
                  sys | devel-base | devel | runtime | runtime-test
  --no-cache      不使用層快取重新建置。
USAGE
}

main() {
  local stage="devel"
  local -a extra=()

  while (( $# )); do
    case "$1" in
      --stage) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; stage="$1" ;;
      --no-cache) extra+=("--no-cache") ;;
      -h|--help) usage; return 0 ;;
      *) printf 'build.sh: 不認得的參數 %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
  done

  cd "${REPO_ROOT}"
  run_hook pre build "${stage}"
  CM_STAGE="${stage}" CM_TAG="${stage}" \
    docker compose build "${extra[@]}"
  run_hook post build "${stage}"
}

main "$@"
