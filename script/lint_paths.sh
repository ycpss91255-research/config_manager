#!/usr/bin/env bash
#
# 路徑 lint。兩個被追蹤的路徑若只差在字母大小寫就失敗。
#
# Linux 區分大小寫，所以 `Dockerfile` 與 `dockerfile/` 在那裡是兩筆，CI 永遠不會
# 察覺。macOS 與 Windows 不區分大小寫：兩個名字是同一筆，其中一個贏得簽出，另一個
# 就是不在磁碟上。這個失敗既無聲又令人困惑——`git status` 回報一筆沒有人做過的刪除，
# 而需要那個檔案的工具死在一則既不指出成因也不指出解法的訊息上。
#
# 這不是假設：本 repo 就出過這一對，在 macOS 上根目錄的 Dockerfile 根本簽不出來，
# 於是 `just test` 停在 `hadolint: Dockerfile: does not exist`，而 CI 一路是綠的。
#
# CI 跑在 Linux 上，所以沒有任何動態檢查抓得到這件事——撞名只能從被追蹤的路徑本身
# 讀出來。目錄前綴也算：名為 `Dockerfile` 的檔案會和名為 `dockerfile/` 的目錄相撞，
# 即使那個目錄從來不會出現在 `git ls-files` 裡。
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: script/lint_paths.sh

  fail  two tracked paths differ only by letter case (they collide on a
        case-insensitive filesystem: macOS, Windows)

Directory prefixes are included, so a file and a directory whose names differ
only by case are caught too.
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  # 每個被追蹤的路徑，加上它的每一段目錄前綴，去重。
  local paths
  paths="$(git ls-files |
    awk -F/ '{ p = ""; for (i = 1; i <= NF; i++) { p = (i == 1 ? $i : p "/" $i); print p } }' |
    sort -u)"

  if [[ -z "${paths}" ]]; then
    printf 'lint_paths: no tracked paths; nothing to check.\n'
    return 0
  fi

  local failures=0 key originals
  while IFS= read -r key; do
    [[ -n "${key}" ]] || continue
    originals="$(printf '%s\n' "${paths}" | awk -v k="${key}" 'tolower($0) == k { printf "%s ", $0 }')"
    printf 'FAIL  these paths differ only by case: %s\n' "${originals}" >&2
    printf '      on macOS/Windows only one of them exists; the rest vanish from the checkout\n' >&2
    failures=$((failures + 1))
  done < <(printf '%s\n' "${paths}" | awk '{ print tolower($0) }' | sort | uniq -d)

  local count
  count="$(printf '%s\n' "${paths}" | grep -c .)"
  printf 'lint_paths: %d tracked path(s) checked -- %d collision(s)\n' "${count}" "${failures}"
  ((failures == 0))
}

main "$@"
