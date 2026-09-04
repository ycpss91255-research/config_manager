#!/usr/bin/env bash
#
# 驗收條件的帳本檢查：PR 引用的每一張 issue 都必須做完，而且做完這件事要有證據。
#
# 規則有三條，全部擋（fail）：
#   1. issue 還有未勾的 `- [ ]`         —— 都完成後才能開 PR
#   2. 勾起來的項目那一行沒有 commit SHA —— 只勾不記，帳本只是一個聲明
#   3. 記的 SHA 不在這個 PR 的範圍內      —— 隨手貼別處的 SHA 等於沒記
#
# 為什麼要有這支：§0.4 寫著「所有規範必須可由工具檢查——無法自動檢查的規範等同
# 不存在」。這個流程若只寫在 CLAUDE.md，它就是一段沒有人會在半夜想起來的約定。
#
# 它與其餘四支 lint 不同：**需要 GitHub API**，所以不進 script/test.sh
# （那支在容器裡跑，容器沒有 token），而是 CI 的一個獨立 job，與 commit-message
# 同一種形狀——跑在映像之外，因為它只需要 gh 與 git。
#
# 擋不住的那一件事，寫在這裡免得日後以為它被驗過：**工具只能確認「有一個屬於本
# PR 的 SHA 被記在那一行」，不能確認那個 commit 真的實作了那條驗收條件。**
# 那需要人看。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/lint_checkpoints.sh <pr 編號> [<base ref>]

  <pr 編號>   要檢查的 pull request
  <base ref>  SHA 的比對範圍是 <base ref>..HEAD，預設 origin/main

  fail  被引用的 issue 還有未勾的驗收條件
  fail  勾起來的驗收條件那一行沒有 commit SHA
  fail  記下的 SHA 不在這個 PR 的 commit 範圍內

  勾選行的格式：- [x] 條件文字 — <sha>
  <sha> 是 7 到 40 個十六進位字元，短寫即可。

PR 沒有引用任何 issue 時通過，並把這件事說出來——不是每個改動都有 issue，
但「沒有」應該是看得見的，不是安靜的。

紀錄的是分支上的 SHA。squash 合併之後那個 SHA 不會存在於 main，這是刻意的：
檢查在 PR 階段執行，那時分支還在。合併後要回溯，走 PR 本身。
USAGE
}

# 勾選行裡的 SHA：7 到 40 個十六進位字元，前後要有邊界，才不會把條件文字裡的
# 十六進位字串誤判成 SHA。
readonly _SHA_RE='(^|[^0-9a-fA-F])[0-9a-fA-F]{7,40}([^0-9a-fA-F]|$)'

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  if [[ $# -lt 1 ]]; then
    usage >&2
    return 2
  fi

  local pull="$1"
  local base="${2:-origin/main}"

  local pr_body
  pr_body="$(gh pr view "${pull}" --json body --jq .body)"

  local -a issues=()
  mapfile -t issues < <(printf '%s\n' "${pr_body}" | grep -oE '#[0-9]+' | tr -d '#' | sort -u)

  if ((${#issues[@]} == 0)); then
    printf 'lint_checkpoints: PR #%s 沒有引用任何 issue；沒有驗收條件要對\n' "${pull}"
    return 0
  fi

  local -a range=()
  mapfile -t range < <(git rev-list "${base}..HEAD")

  local failures=0 issue
  for issue in "${issues[@]}"; do
    _check_issue "${issue}" failures
  done

  printf 'lint_checkpoints: PR #%s 引用 %d 張 issue -- %d 個問題\n' \
    "${pull}" "${#issues[@]}" "${failures}"
  ((failures == 0))
}

# shellcheck disable=SC2178  # range 是 main 的區域陣列，這裡以名稱引用
_check_issue() {
  local issue="$1"
  local -n counter="$2"

  local body
  body="$(gh issue view "${issue}" --json body --jq .body)"

  local unchecked
  unchecked="$(printf '%s\n' "${body}" | grep -cE '^[[:space:]]*- \[ \]' || true)"
  if ((unchecked > 0)); then
    printf 'FAIL #%s  還有 %s 條驗收條件沒有勾起來\n' "${issue}" "${unchecked}" >&2
    printf '      都完成後才能開 PR。做不完就把這張 issue 拆小\n' >&2
    counter=$((counter + 1))
  fi

  local line text candidate
  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    text="$(printf '%s' "${line}" | sed -E 's/^[[:space:]]*- \[x\][[:space:]]*//')"

    if ! printf '%s' "${line}" | grep -qE "${_SHA_RE}"; then
      printf 'FAIL #%s  勾起來了但沒有記 commit：%s\n' "${issue}" "${text}" >&2
      printf '      在該行後面加上 — <sha>。只勾不記，帳本只是一個聲明\n' >&2
      counter=$((counter + 1))
      continue
    fi

    candidate="$(printf '%s' "${line}" | grep -oE '[0-9a-fA-F]{7,40}' | tail -1)"
    if ! _in_range "${candidate}"; then
      printf 'FAIL #%s  記下的 %s 不在這個 PR 的 commit 範圍內\n' "${issue}" "${candidate}" >&2
      printf '      別處的 SHA 貼過來也會被記下，但它證明不了這條做完了\n' >&2
      counter=$((counter + 1))
    fi
  done < <(printf '%s\n' "${body}" | grep -E '^[[:space:]]*- \[x\]' || true)
}

_in_range() {
  local candidate="$1" sha
  for sha in "${range[@]}"; do
    [[ "${sha}" == "${candidate}"* ]] && return 0
  done
  return 1
}

main "$@"
