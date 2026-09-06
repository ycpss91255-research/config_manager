#!/usr/bin/env bash
#
# 驗收條件的帳本檢查：PR 引用的每一張 issue 都必須做完，而且做完這件事要有證據。
#
# 規則有四條，全部擋（fail）：
#   1. 被引用的 issue 沒有任何勾選行       —— 帳本不存在，什麼都沒得對
#   2. issue 還有未勾的 `- [ ]`          —— 都完成後才能開 PR
#   3. 勾起來的項目那一行沒有 commit 主旨 —— 只勾不記，帳本只是一個聲明
#   4. 記的主旨在 git 歷史裡找不到        —— 隨手編一句話等於沒記
#
# 記主旨不記 SHA，因為 **SHA 不是穩定識別碼**：rebase 會改寫它，squash 合併會讓它
# 從歷史裡消失。而這個 repo 規定推送前 rebase、分支保護要求與 base 同步、合併一律
# squash——三條加起來，每個 PR 都必然經歷至少一次 SHA 改寫。第一版綁在 SHA 上，
# 結果是每次合併前都得手動重填一輪帳本，而那個手動步驟沒有東西擋著它被填錯（#129）。
#
# 主旨在 rebase 後不變，squash 之後仍留在 main 的歷史裡，而且人讀得懂。
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
#
# 一條敷衍的 `- [x] 做完了 — <主旨>` 兩條規則都過。這道門擋得住的是**帳本存在
# 且每一條都指得到一個 commit**，不是**驗收條件寫得對**。後者需要人。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/lint_checkpoints.sh <pr 編號> [<base ref>]

  <pr 編號>   要檢查的 pull request
  <base ref>  本 PR 的範圍是 <base ref>..HEAD，預設 origin/main

  fail  被引用的 issue 還有未勾的驗收條件
  fail  勾起來的驗收條件那一行沒有 commit 主旨
  fail  記下的主旨在 git 歷史裡找不到

  勾選行的格式：- [x] 條件文字 — <commit 主旨>
  主旨要與 git log 的某一筆逐字相同。它在本 PR 或 main 的歷史裡都算數——
  跨 PR 的 issue，早先那幾條的 commit 已經在 main 上了。

  只認 GitHub 的關閉關鍵字（closes / fixes / resolves）。PR 描述裡順帶提到
  「見 #117」是引用，不是交付承諾。

PR 沒有引用任何 issue 時通過，並把這件事說出來——不是每個改動都有 issue，
但「沒有」應該是看得見的，不是安靜的。

這道門擋不住的：它確認得了「有一筆真的存在的 commit 被記在那一行」，
確認不了那筆 commit 真的實作了那條驗收條件。那需要人看。
USAGE
}

# 條件文字與 commit 主旨之間的分隔。全形破折號，與 repo 其餘文字一致。
readonly _SEPARATOR=' — '

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
  # 只認關閉關鍵字。裸的 #NNN 是引用，不是交付承諾——ch0 那個 PR 就是因為描述裡
  # 寫了「見 #117」而被誤擋（#129）。關鍵字集合與 GitHub 自己認的一致。
  mapfile -t issues < <(printf '%s\n' "${pr_body}" |
    grep -oiE '(close[sd]?|fix(e[sd])?|resolve[sd]?)[[:space:]]+#[0-9]+' |
    grep -oE '[0-9]+' | sort -u)

  if ((${#issues[@]} == 0)); then
    printf 'lint_checkpoints: PR #%s 沒有引用任何 issue；沒有驗收條件要對\n' "${pull}"
    return 0
  fi

  # 本 PR 的主旨，加上 main 的歷史。後者讓跨 PR 的 issue 過得了：早先那幾條驗收
  # 條件的 commit 已經在 main 上，它們不會出現在 base..HEAD 裡。
  local -a subjects=()
  mapfile -t subjects < <(git log --format=%s "${base}..HEAD"; git log --format=%s "${base}")

  local failures=0 issue
  for issue in "${issues[@]}"; do
    _check_issue "${issue}" failures
  done

  printf 'lint_checkpoints: PR #%s 引用 %d 張 issue -- %d 個問題\n' \
    "${pull}" "${#issues[@]}" "${failures}"
  ((failures == 0))
}

# shellcheck disable=SC2178  # subjects 是 main 的區域陣列，這裡以名稱引用
_check_issue() {
  local issue="$1"
  local -n counter="$2"

  local body
  body="$(gh issue view "${issue}" --json body --jq .body)"

  # 規則 1：被引用的 issue 至少要有一個勾選行。
  # 沒有勾選框的 issue 三條後續規則全部跑不到——零個勾選框時既有檢查都不進來，
  # 而「兩個都不成立」被當成「沒有問題」。帳本可以是空的（#158）。
  local total_checkboxes
  total_checkboxes="$(printf '%s\n' "${body}" | grep -cE '^[[:space:]]*- \[(x| )\]' || true)"
  if ((total_checkboxes == 0)); then
    printf 'FAIL #%s  issue 內文沒有任何勾選框——帳本不存在\n' "${issue}" >&2
    printf '      在 issue 內文寫下驗收條件並勾起來，不是把 closes 拿掉\n' >&2
    counter=$((counter + 1))
    return 0
  fi

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

    if [[ "${text}" != *"${_SEPARATOR}"* ]]; then
      printf 'FAIL #%s  勾起來了但沒有記 commit：%s\n' "${issue}" "${text}" >&2
      printf '      在該行後面加上「 — <commit 主旨>」。只勾不記，帳本只是一個聲明\n' >&2
      counter=$((counter + 1))
      continue
    fi

    candidate="${text##*"${_SEPARATOR}"}"
    if ! _known_subject "${candidate}"; then
      printf 'FAIL #%s  記下的主旨在 git 歷史裡找不到：%s\n' "${issue}" "${candidate}" >&2
      printf '      主旨要與 git log 的某一筆逐字相同（本 PR 或 main 的歷史都算）\n' >&2
      counter=$((counter + 1))
    fi
  done < <(printf '%s\n' "${body}" | grep -E '^[[:space:]]*- \[x\]' || true)
}

_known_subject() {
  local candidate="$1" subject
  for subject in "${subjects[@]}"; do
    [[ "${subject}" == "${candidate}" ]] && return 0
  done
  return 1
}

main "$@"
