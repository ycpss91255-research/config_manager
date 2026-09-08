#!/usr/bin/env bash
#
# 推導內容 lint。文件裡抄了一份可從樹推導出來的東西就失敗。
#
# 不變式 9：「可從樹推導出來的數字或清單，在被閱讀時計算，不存進一份需要有人手動
# 維持一致的追蹤檔案。文件裡放的是任何產生器都做不出來的東西：意圖、理由、以及
# 一個東西為何是這個形狀。」
#
# markdown 沒辦法在被閱讀時計算，所以對它而言「在被閱讀時計算」只有一個實作方式：
# **把那份副本拿掉，指向真正的來源。** 這支腳本擋的就是副本被寫回去。
#
# 為什麼要有它：`README.md` 的「目前狀態」抄了 `doc/TEST-PLAN.md` 的測試介面清單，
# 然後過期——`io/` 與 `api/` 各有四個與兩個模組已經合併，那一節還寫著「尚未開始」
# （#98）。同一份檔案裡另外四處同類的數字，其中三處也已經不對。它們每一處都曾經
# 是對的；問題不是有人寫錯，是**沒有任何東西會在它們變錯的那一刻出聲**（§0.4）。
#
# 三條規則，全部擋（fail）：
#
#   R1  可推導的數量詞組  —— 「<數字> 份 ADR」「<數字> 個測試介面」這一類
#   R2  下一個 ADR 編號    —— 寫成字面編號（`00000029`），而它由現有的最大編號決定
#   R3  測試介面／驗收旅程的代號 —— `T<N>`／`A<N>` 一出現，就是在複製那份對照表
#
# 外加一條不算規則的守門：要檢查的檔案不在時**大聲失敗**。一支因為檔案不在而回 0
# 的 lint，與「檢查過了、乾淨」在輸出上分不出來（不變式 2）。
#
# **R1 與 R2 是詞組白名單，不是通則，代價寫在這裡。** 通則（「數字後面接量詞就擋」）
# 在散文裡的誤報率太高——README 講「兩個服務」「四次代價」「三類東西」，那些都不是
# 可推導的數量，而一支噴一堆誤報的 lint 會被關掉，被關掉的 lint 等於不存在。代價是
# **白名單會漏**：一種沒被想到的數量詞組寫進去不會被擋。要補就往 `_rules` 加一列，
# 那張表是資料，加一列不必改邏輯。
#
# R3 相反，它是通則而且很鈍：任何 `T<數字>`／`A<數字>` 都擋。那兩種代號只在
# `doc/TEST-PLAN.md` 有定義，別的文件提到它們就是在複製一份對照——**而那正是 #98
# 抓到的那一句**。README 現在一個都沒有，所以這條鈍規則在這裡不花成本。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

usage() {
  cat <<'USAGE'
用法：script/lint_derived.sh [<檔案>]

  <檔案>  要檢查的 markdown 文件（預設：README.md）。

  fail  手抄一份樹本身就答得出來的數量（「22 份決策紀錄」、
        「18 個測試介面」、「54 個 issue」……）
  fail  把下一個 ADR 編號寫成字面值（「從 `00000029` 續接」）
  fail  出現測試介面或驗收旅程的代號（T<N>／A<N>）——那份對照只在
        doc/TEST-PLAN.md，別的地方都不該有
  fail  檔案不存在（一支對不存在的檔案也通過的 lint，與真的檢查過分不出來）

Markdown 在讀取時算不出任何東西，所以不變式 9 在這裡只有一種實作：把抄本刪掉，
指回來源。這擋住那份抄本被寫回去。

R1 與 R2 是一張詞組表，不是通則：「數字 + 量詞」會連「兩個服務」「四次代價」
一起擋，而一支亂叫的 lint 會被關掉。代價是這張表可能漏掉某種寫法——去 _rules
補一列。
USAGE
}

# 詞組<TAB>延伸正規式<TAB>該去哪裡看。資料，不是邏輯：補一種寫法就加一列。
_rules() {
  cat <<'RULES'
ADR 份數	([0-9]+|二|三|四|五|六|七|八|九|十)[[:space:]]*份[[:space:]]*(ADR|決策紀錄)	數 doc/adr/ 底下的檔案
下一個 ADR 編號	從[[:space:]]*`?0[0-9]{7}`?[[:space:]]*續接	接在 doc/adr/ 現有的最大編號之後
測試介面個數	([0-9]+|二|三|四|五|六|七|八|九|十)[[:space:]]*個[[:space:]]*測試介面	doc/TEST-PLAN.md 的 ### T<N> 段落
驗收旅程個數	([0-9]+|二|三|四|五|六|七|八|九|十)[[:space:]]*個[[:space:]]*驗收旅程	doc/TEST-PLAN.md 的 ### A<N> 段落
issue 張數	([0-9]+|二|三|四|五|六|七|八|九|十)[[:space:]]*(個|張)[[:space:]]*issue	GitHub Issues
RULES
}

# 測試介面與驗收旅程的代號。前後不接英數字，免得把 ADR-00000024 或 UTF-8 讀成代號。
readonly CODE_PATTERN='(^|[^0-9A-Za-z])[TA][0-9]+([^0-9A-Za-z]|$)'

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  local target="${1:-${REPO_ROOT}/README.md}"

  if [[ ! -f "${target}" ]]; then
    printf 'lint_derived: %s 不存在，所以什麼都沒有被檢查\n' "${target}" >&2
    printf '              下一步：給一個存在的 markdown 檔，或修掉呼叫它的那一行\n' >&2
    return 1
  fi

  local failures=0

  local name pattern source line
  while IFS=$'\t' read -r name pattern source; do
    [[ -n "${name}" ]] || continue
    while IFS= read -r line; do
      [[ -n "${line}" ]] || continue
      printf 'FAIL 抄了一份可推導的「%s」——%s:%s\n' "${name}" "${target}" "${line}" >&2
      printf '     下一步：拿掉那個數字，改成指向來源——%s\n' "${source}" >&2
      failures=$((failures + 1))
    done < <(grep -nE "${pattern}" "${target}" || true)
  done < <(_rules)

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    printf 'FAIL 提到測試介面或驗收旅程的代號——%s:%s\n' "${target}" "${line}" >&2
    printf '     下一步：拿掉它，改成指向 doc/TEST-PLAN.md 的「覆蓋率審計」\n' >&2
    printf '     哪個模組落在哪個介面、哪一層落地到哪裡，那張表是唯一的一份\n' >&2
    failures=$((failures + 1))
  done < <(grep -nE "${CODE_PATTERN}" "${target}" || true)

  printf 'lint_derived: %s -- %d 個問題\n' "${target}" "${failures}"
  ((failures == 0))
}

main "$@"
