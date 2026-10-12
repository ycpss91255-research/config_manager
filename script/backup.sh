#!/usr/bin/env bash
#
# 把 config-repo 備份成一個檔案，放進主機上的一個資料夾（#52）。**服務不必停。**
#
# 備份的是設定庫裡**已提交**的全部：納管清單、來源複本、白名單、完整歷史。**這不是整機備份**
# ——目標檔案（機器上實際被管理的那些設定檔）不在裡面；還沒進版的草稿也不在。
#
# 真正做事的是容器裡的 CLI（`config_manager.api.cli backup`）：打包、然後立刻把做出來的檔案
# 還原一次確認它還原得出來。這支只負責把主機的資料夾接進去。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

usage() {
  cat <<'USAGE'
用法：script/backup.sh <目的地資料夾>

把 config-repo 備份成一個 .bundle 檔，放進 <目的地資料夾>（不存在會建）。服務不必停。
備份的是已提交的清單、來源、白名單與歷史；不含目標檔案，也不含還沒進版的草稿。

還原用 script/restore.sh。完整步驟見 doc/DEPLOYMENT.md 的「備份與還原」。
USAGE
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    return 0
  fi
  if (( $# != 1 )); then
    printf 'backup.sh: 要給一個目的地資料夾，收到 %d 個參數。下一步：script/backup.sh <目的地資料夾>\n' "$#" >&2
    exit 2
  fi

  # 先在主機上建好。交給 docker 建的話，那個資料夾是 root 的，呼叫者之後動不了自己的備份。
  mkdir -p "$1"
  local destination
  destination="$(cd -- "$1" && pwd -P)"

  cd "${REPO_ROOT}"
  # 以**呼叫者**的身分跑，備份檔在主機上才是呼叫者的。那個身分不是設定庫的擁有者，git 預設會
  # 拒絕讀別人的 repo（safe.directory），所以明講這一份可以讀；它在容器裡也沒有家目錄，HOME 另給。
  # --entrypoint 換掉：這裡要的是一道指令，不是把服務的啟動檢查再跑一遍。
  docker compose run --rm --no-deps \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env GIT_CONFIG_COUNT=1 \
    --env GIT_CONFIG_KEY_0=safe.directory \
    --env 'GIT_CONFIG_VALUE_0=*' \
    --volume "${destination}:/backup" \
    --entrypoint python \
    backend -m config_manager.api.cli backup /backup
}

main "$@"
