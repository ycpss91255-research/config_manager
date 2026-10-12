#!/usr/bin/env bash
#
# 建置映像。沒給 `--stage` 就照 compose 檔的預設：現場那一份（compose.yaml）是 `runtime`，
# 疊了 compose.dev.yaml（`just docker dev build`）是 `devel`。`--stage` 可指名別的。
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

  --stage <name>  要建置的 Dockerfile 階段。沒給就照 compose 檔的預設
                  （compose.yaml 是 runtime；疊了 compose.dev.yaml 是 devel）。
                  sys | devel-base | devel | runtime | runtime-test
  --no-cache      不使用層快取重新建置。
USAGE
}

main() {
  local stage=""
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
  if [[ -n "${stage}" ]]; then
    export CM_STAGE="${stage}" CM_TAG="${stage}"
  fi
  # 鉤子要知道實際建的是哪個階段。沒指名的時候問 compose 自己（映像的 tag 就是階段名），
  # 不在這裡再抄一次預設值——抄的那一份會在 compose 檔改預設時安靜地過期。
  stage="${stage:-$(docker compose config --images | head -n 1 | cut -d: -f2)}"
  run_hook pre build "${stage}"
  docker compose build "${extra[@]}"
  run_hook post build "${stage}"
}

main "$@"
