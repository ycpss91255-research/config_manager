#!/usr/bin/env bash
#
# 把一份備份還原成 config-repo（#52）。**服務要先停，設定庫要是空的。**
#
# 這支做兩件 CLI 自己做不到或不該做的事：確認服務真的停了（容器裡看不見 compose），以及把主機上
# 的備份檔接進去。其餘的規矩在 CLI（`config_manager.api.cli restore`）裡：先驗證備份還原得出來
# 才碰設定庫；設定庫有任何東西就拒絕、不清空；中途失敗把設定庫清回空的。
#
# **它不會替你停服務，也不會替你移走既有的設定庫。** 那兩件事會讓資料離線或消失，要人自己決定。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

usage() {
  cat <<'USAGE'
用法：script/restore.sh <備份檔>

把 <備份檔>（script/backup.sh 做出來的 .bundle）還原成 config-repo。

還原之前：
  1. 服務要先停（docker compose stop）。還在跑就拒絕。
  2. 設定庫要是空的。裡面有東西就拒絕，不覆蓋、不清空。

還原的只有設定庫。目標檔案不會被動到：啟動服務後系統重新比對，缺的顯示未部署、
被改過的顯示偏離，由人在頁面上處置。完整步驟見 doc/DEPLOYMENT.md 的「備份與還原」。
USAGE
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    return 0
  fi
  if (( $# != 1 )); then
    printf 'restore.sh: 要給一個備份檔，收到 %d 個參數。下一步：script/restore.sh <備份檔>\n' "$#" >&2
    exit 2
  fi
  if [[ ! -f "$1" ]]; then
    printf 'restore.sh: 備份檔 %s 不存在或不是一般檔案。下一步：確認路徑，備份檔是 script/backup.sh 做出來的 .bundle\n' "$1" >&2
    exit 2
  fi
  local bundle name
  bundle="$(cd -- "$(dirname -- "$1")" && pwd -P)/$(basename -- "$1")"
  name="$(basename -- "$1")"

  cd "${REPO_ROOT}"
  # 還原寫進去的那一份，跑著的服務不會重新讀；而它還握著舊的那一份在寫。所以先確認兩個服務都停了。
  local running
  running="$(docker compose ps --status running --quiet backend frontend)"
  if [[ -n "${running}" ]]; then
    printf 'restore.sh: 服務還在跑，不還原。下一步：先停掉（docker compose stop），確認要還原到的設定庫是空的，再重跑\n' >&2
    exit 1
  fi

  # 這一次**不**換身分：還原出來的設定庫要屬於服務的使用者，否則服務起來之後寫不進去。
  # 備份檔唯讀掛進去——還原不該有任何機會改到它。
  docker compose run --rm --no-deps \
    --volume "${bundle}:/restore/${name}:ro" \
    --entrypoint python \
    backend -m config_manager.api.cli restore "/restore/${name}"

  printf 'restore.sh: 設定庫已還原。下一步：啟動服務（docker compose up -d），到頁面按「檢查差異」重新比對目標狀態\n'
}

main "$@"
