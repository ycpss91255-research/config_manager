#!/usr/bin/env bash
#
# 一個 tag 的驗收報表，以及「這個 tag 發不發得出去」的判定。
#
# 本機跑得出來的報表，證明的是「**某個人在某個時間點的某份程式碼上**跑過」。release
# 上的報表證明的是「**這個 tag 指的這份程式碼**跑出來是這樣」——那才複驗得了：任何人
# checkout 那個 tag 重跑，該得到同一份結論（#150）。所以報表由 CI 在 tag 被推上來時
# 產生，不由人手動附上去：一份手動產生再上傳的檔案，與「宣稱有檢查其實沒檢查」是同
# 一種東西——沒有東西保證那份檔案是這份程式碼跑出來的。
#
# ## rc 的紅燈是它要記錄的東西
#
# rc 的用途是記錄「現在走到哪」：rc1 可以是紅的，rc2 轉綠。**擋下紅色的 rc 等於讓 N
# 無法遞增**，那就失去 rc 的意義了。所以報表未通過時 release 仍然建立，紅綠寫在標題
# 上，一眼看得到。
#
# ## 報表未通過與報表壞掉是兩件事
#
# acceptance.sh 的 1 是「這一版還沒做完」，2 是「這份報表本身不能信」（對照表指到
# 不存在的規格、milestone 不存在、對照表解析不了）。前者正是 rc 要記錄的東西；
# 後者連 rc 都不建——一個附著壞掉報表的 release，與一份手寫的「已通過」表格一樣
# 沒有證據力。兩者混成同一種處置，就分不出「還沒做完」與「沒有人在驗」。
#
# ## 為什麼 gh 在容器外
#
# 檢查本身在容器裡：這支腳本呼叫 script/acceptance.sh，那支自己轉進映像（ADR-00000027）。
# 這支只做容器裡做不到的事——呼叫 gh——與 CI 的 checkpoints job 同一種形狀：跑在映像
# 之外，因為它只需要 gh 與 git，而容器裡沒有 token。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

# 報表由誰產生可以被覆寫，這樣這支腳本自己也測得到：規格餵給它一份替身報表與一個
# 指定的結束碼，看它建不建 release、標題寫什麼。少了這個，唯一能驗證這道閘門的方法
# 是真的弄紅一條檢查點再發一次 release——而那會在 GitHub 上留下對外可見的東西。
# 與 acceptance.sh 的 CM_ACCEPTANCE_MAP 同一個先例。
readonly ACCEPTANCE="${CM_RELEASE_ACCEPTANCE:-${REPO_ROOT}/script/acceptance.sh}"

usage() {
  cat <<'USAGE'
Usage: script/release.sh <tag>

  <tag>  vX.Y.Z-rcN（候選）或 vX.Y.Z（正式）

在 <tag> 指的這份程式碼上執行 script/acceptance.sh，並以那份報表建立 GitHub release。

  rc tag    不論報表綠紅都建立 release，紅綠寫在標題上
  兩者皆是  報表本身壞掉（對照表對不上）時不建立 release

  exit 0   release 建好了
  exit 2   用法錯誤、tag 格式認不得、缺 gh，或報表本身壞掉

  CM_RELEASE_ACCEPTANCE  改用這個指令產生報表（供 test/bats/unit/release.bats 使用）

報表的逐條判定進 release notes，判定寫在第一行。notes 寫在目前目錄下的
release-notes-<tag>.md。
USAGE
}

# vX.Y.Z 與 vX.Y.Z-rcN 兩種形狀，其餘一律擋下。設成 MILESTONE 與 RC 兩個值：
# 前者是要驗的那一版，後者空的時候代表這是正式 tag。
_parse_tag() {
  local tag="$1"
  local pattern='^(v[0-9]+\.[0-9]+\.[0-9]+)(-rc([1-9][0-9]*))?$'

  if [[ ! "${tag}" =~ ${pattern} ]]; then
    printf 'release: 認不得的 tag 格式：%s\n' "${tag}" >&2
    printf 'release: 只認 vX.Y.Z（正式）與 vX.Y.Z-rcN（候選）兩種形狀\n' >&2
    printf 'release: 下一步：改用 vX.Y.Z-rcN 的形狀重推這個 tag\n' >&2
    return 2
  fi

  MILESTONE="${BASH_REMATCH[1]}"
  RC="${BASH_REMATCH[3]:-}"
}

_require_gh() {
  if command -v gh >/dev/null 2>&1; then
    return 0
  fi
  printf 'release: gh 不在 PATH 上，release 建不起來\n' >&2
  printf 'release: 一支建不了 release 卻回 0 的腳本，與一次成功的發布長得一模一樣\n' >&2
  printf 'release: 下一步：安裝 gh（https://cli.github.com），或改在有 gh 的 CI job 上執行\n' >&2
  return 2
}

_write_notes() {
  local notes="$1" verdict="$2" tag="$3" milestone="$4" commit="$5" body="$6"

  {
    # 第一行就是判定。一個要往下捲三頁才看得出紅綠的 release，與一個沒有標示的
    # release 沒有差別。
    printf '## 驗收：%s %s\n\n' "${milestone}" "${verdict}"
    printf '%s\n\n' "$(printf '%s\n' "${body}" | grep -F '未涵蓋' | tail -n 1)"
    printf '這份報表由 CI 在 tag `%s` 指的 commit `%s` 上執行 `script/acceptance.sh %s`\n' \
      "${tag}" "${commit}" "${milestone}"
    printf '產生，不是任何人手動貼上來的。\n\n'
    printf '複驗：`git checkout %s && ./script/acceptance.sh %s`\n\n' "${tag}" "${milestone}"
    # 逐條判定原樣進 notes。只寫一句「未通過」的 release notes，讀者還是得自己
    # 去別的地方找是哪一條——而那正是這份報表要取代的東西。
    printf '```text\n%s\n```\n' "${body}"
  } >"${notes}"
}

_release() {
  local tag="$1"
  local MILESTONE RC
  _parse_tag "${tag}"
  _require_gh

  # 報表要說得出它是在哪一份程式碼上跑出來的，否則它與一份本機跑出來的輸出沒有
  # 差別——而那正是這份報表要取代的東西。
  local commit
  commit="$(git rev-parse --short HEAD)"

  local body code=0
  body="$("${ACCEPTANCE}" "${MILESTONE}" 2>&1)" || code=$?

  # 2 說的是「這份報表不能信」，不是「這一版還沒做完」。沒有報表就沒有東西可以放進
  # release，rc 也一樣——rc 記錄的是走到哪，不是「有沒有跑過」。
  if ((code != 0 && code != 1)); then
    printf '%s\n' "${body}" >&2
    printf 'release: %s 的驗收報表產不出來（acceptance.sh 回 %d），%s 不建立 release\n' \
      "${MILESTONE}" "${code}" "${tag}" >&2
    printf 'release: 那不是「未通過」，是根本沒有報表——一個附著壞掉報表的 release 證明不了東西\n' >&2
    printf 'release: 下一步：修好 doc/acceptance-checkpoints.toml 的參照，重推這個 tag\n' >&2
    return 2
  fi

  local verdict='通過'
  if ((code != 0)); then
    verdict='未通過'
  fi

  local notes="${PWD}/release-notes-${tag}.md"
  _write_notes "${notes}" "${verdict}" "${tag}" "${MILESTONE}" "${commit}" "${body}"

  local -a create=(gh release create "${tag}"
    --title "${tag} — 驗收${verdict}"
    --notes-file "${notes}")
  if [[ -n "${RC}" ]]; then
    # rc 不是正式版本。標成正式版本的 rc 會出現在「最新版本」上。
    create+=(--prerelease)
  fi

  "${create[@]}"
  printf 'release: 已建立 %s（驗收%s）\n' "${tag}" "${verdict}"
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  if [[ $# -ne 1 ]]; then
    usage >&2
    return 2
  fi

  _release "$1"
}

main "$@"
