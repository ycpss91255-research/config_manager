#!/usr/bin/env bash
#
# 驗收條件的帳本檢查：PR 綁定的那一張 issue 必須做完，而且做完這件事要有證據。
#
# 綁的是哪一張，由共用的綁定判定（script/pr_binding.sh，#407）決定：每個 PR 都要用關閉關鍵字
# 綁一張本 repo 還沒完成的 issue，沒綁或綁定不成立就失敗——原本「沒有引用 issue 時通過」的例外
# 隨著「只開 PR」的兩種例外一起取消（#407 定案 8）。
#
# 規則有四條，全部擋（fail）：
#   1. 綁定的 issue 沒有任何勾選行       —— 帳本不存在，什麼都沒得對
#   2. issue 還有未勾的 `- [ ]`          —— 都完成後才 gh pr ready（草稿可以先開）
#   3. 勾起來的項目那一行沒有 commit 主旨 —— 只勾不記，帳本只是一個聲明
#   4. 記的主旨在 git 歷史裡找不到        —— 隨手編一句話等於沒記
#
# 記主旨不記 SHA，因為 **SHA 不是穩定識別碼**：rebase 會改寫它。而這個 repo 規定推送前
# rebase、分支要與 base 同步才合併——每個 PR 都必然經歷至少一次 SHA 改寫（#407 之前合併一律
# squash，SHA 還會從歷史裡消失）。第一版綁在 SHA 上，結果是每次合併前都得手動重填一輪帳本，
# 而那個手動步驟沒有東西擋著它被填錯（#129）。
#
# 主旨在 rebase 後不變，以 merge commit 合併之後仍留在 main 的歷史裡，而且人讀得懂。
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

  fail  綁定判定（script/pr_binding.sh）不成立：描述沒有關閉關鍵字、綁了兩張以上、指到別處……
  fail  綁定的 issue 沒有勾選框，或還有未勾的驗收條件
  fail  勾起來的驗收條件那一行沒有 commit 主旨
  fail  記下的主旨在 git 歷史裡找不到

  勾選行的格式：- [x] 條件文字 — <commit 主旨>
  主旨要與 git log 的某一筆逐字相同。它在本 PR 或 main 的歷史裡都算數——
  跨 PR 的 issue，早先那幾條的 commit 已經在 main 上了。

  只認 GitHub 的關閉關鍵字（closes / fixes / resolves）。PR 描述裡順帶提到
  「見 #117」是引用，不是交付承諾。

每個 PR 都要綁一張 issue（#407）：描述沒有關閉關鍵字時失敗，並寫出下一步。
每一次執行都印出讀到的識別：PR 的 updatedAt 與描述的 sha256、issue 的編號與 updatedAt。

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
  local here
  here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

  # 綁的是哪一張由共用判定決定（#407）。它的識別與原因照原樣印出：同一輪的幾道檢查可能讀在改綁的
  # 前後，這一道的紅燈要說得出它自己讀到的是什麼。
  local binding code=0
  binding="$("${here}/pr_binding.sh" "${pull}" "${base}")" || code=$?
  printf '%s\n' "${binding}"
  if ((code != 0)); then
    printf 'lint_checkpoints: PR #%s 的綁定判定不成立（原因在上面），沒有帳本可以對\n' "${pull}" >&2
    printf '      下一步：照上面綁定判定的下一步改——每個 PR 都要用關閉關鍵字綁一張 issue（#407）\n' >&2
    return 1
  fi
  local issue="${binding##*綁定 #}"

  # 本 PR 的主旨，加上 main 的歷史。後者讓跨 PR 的 issue 過得了：早先那幾條驗收
  # 條件的 commit 已經在 main 上，它們不會出現在 base..HEAD 裡。
  local -a subjects=()
  mapfile -t subjects < <(git log --format=%s "${base}..HEAD"; git log --format=%s "${base}")

  local failures=0
  _check_issue "${issue}" failures

  printf 'lint_checkpoints: PR #%s 綁定 #%s -- %d 個問題\n' "${pull}" "${issue}" "${failures}"
  ((failures == 0))
}

# shellcheck disable=SC2178  # subjects 是 main 的區域陣列，這裡以名稱引用
_check_issue() {
  local issue="$1"
  local -n counter="$2"

  local raw body
  if ! raw="$(gh issue view "${issue}" --json body,updatedAt)"; then
    printf 'FAIL #%s  讀不到 issue 的內文，帳本沒有被檢查\n' "${issue}" >&2
    printf '      下一步：確認這個 job 有 GH_TOKEN 與 issues: read 權限，重跑這個 job\n' >&2
    counter=$((counter + 1))
    return 0
  fi
  body="$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("body") or "")' <<<"${raw}")"
  printf 'lint_checkpoints: 識別 issue #%s updatedAt=%s\n' "${issue}" \
    "$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("updatedAt"))' <<<"${raw}")"

  # 勾選框比對放寬到 GitHub task-list 的全部合法形狀：項目符號 -／*／+ 皆可、勾選字元
  # x／X 皆為已勾（#254）。先前四處都寫死「- 加小寫 x」，於是 `- [X]`（大寫，畫面與 `- [x]`
  # 一模一樣）勾的條目不進 R3/R4（可不留 commit 證據就「勾完」），`* [ ]`／`+ [ ]` 的未勾
  # 條目讓 R2 失效（未完成也能開 PR）——帳本閘門被自己最想防的「看似在檢查、其實沒有」擊穿。
  #
  # 規則 1：被引用的 issue 至少要有一個勾選行。
  # 沒有勾選框的 issue 三條後續規則全部跑不到——零個勾選框時既有檢查都不進來，
  # 而「兩個都不成立」被當成「沒有問題」。帳本可以是空的（#158）。
  local total_checkboxes
  total_checkboxes="$(printf '%s\n' "${body}" | grep -cE '^[[:space:]]*[-*+] \[([xX]| )\]' || true)"
  if ((total_checkboxes == 0)); then
    printf 'FAIL #%s  issue 內文沒有任何勾選框——帳本不存在\n' "${issue}" >&2
    printf '      下一步：在 issue 內文寫下驗收條件並勾起來，不是把 closes 拿掉\n' >&2
    counter=$((counter + 1))
    return 0
  fi

  local unchecked
  unchecked="$(printf '%s\n' "${body}" | grep -cE '^[[:space:]]*[-*+] \[ \]' || true)"
  if ((unchecked > 0)); then
    printf 'FAIL #%s  還有 %s 條驗收條件沒有勾起來\n' "${issue}" "${unchecked}" >&2
    printf '      下一步：都完成後才 gh pr ready（草稿可以先開）。做不完就把這張 issue 拆小\n' >&2
    counter=$((counter + 1))
  fi

  local line text candidate
  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    text="$(printf '%s' "${line}" | sed -E 's/^[[:space:]]*[-*+] \[[xX]\][[:space:]]*//')"

    if [[ "${text}" != *"${_SEPARATOR}"* ]]; then
      printf 'FAIL #%s  勾起來了但沒有記 commit：%s\n' "${issue}" "${text}" >&2
      printf '      下一步：在該行後面加上「 — <commit 主旨>」。只勾不記，帳本只是一個聲明\n' >&2
      counter=$((counter + 1))
      continue
    fi

    candidate="${text##*"${_SEPARATOR}"}"
    if ! _known_subject "${candidate}"; then
      printf 'FAIL #%s  記下的主旨在 git 歷史裡找不到：%s\n' "${issue}" "${candidate}" >&2
      printf '      下一步：主旨要與 git log 的某一筆逐字相同（本 PR 或 main 的歷史都算）\n' >&2
      counter=$((counter + 1))
    fi
  done < <(printf '%s\n' "${body}" | grep -E '^[[:space:]]*[-*+] \[[xX]\]' || true)
}

_known_subject() {
  local candidate="$1" subject
  for subject in "${subjects[@]}"; do
    [[ "${subject}" == "${candidate}" ]] && return 0
  done
  return 1
}

main "$@"
