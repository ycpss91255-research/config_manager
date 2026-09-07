#!/usr/bin/env bash
#
# 覆蓋率審計 lint。`doc/TEST-PLAN.md` 的「模組 → 測試介面」表與磁碟上的
# `src/**/*.py`、`script/**/*.sh` 對不起來就失敗。
#
# 那張表自己立了一條規則：
#
#   **新增一個模組或一支腳本時，這裡要一起加一列**——沒有一列的檔案，
#   既不算被覆蓋，也不算刻意留空。
#
# 在這支腳本之前沒有任何工具擋著它。設計 §0.4 說「所有規範必須可由工具檢查——
# 無法自動檢查的規範等同不存在」，所以依它自己的規則，那條規則先前並不存在。
# 它也確實兩次沒有被遵守：#104 加了 `api/errors` 與 `api/session` 沒加列；
# #113 把腳本那半補齊之後，#150 的 `script/release.sh` 又讓數字從 32 漂到 33。
#
# **這支 lint 檢查的是「有沒有一列」，不是「那一列寫得對不對」。** 模組對到哪個
# 測試介面是人寫下的意圖，任何產生器都做不出來（不變式 9 說文件裡該放的正是這種
# 東西）。可以推導的只有「哪些檔案存在」，所以工具只管集合對不對得起來。
#
# 五條規則，全部擋（fail）：
#
#   R1  表讀得出來        —— 區段在、表在、每一列的第一欄有一個反引號路徑
#   R2  沒有漏列          —— 每個 .py 與 .sh 都被至少一列對到
#   R3  沒有對不到的列    —— 對不到任何檔案的列，狀態欄要以「未落地」開頭
#   R4  數量不手抄        —— 這一節裡不得出現「<數字>個」或「<數字>支」
#   R5  未落地要真的未落地 —— 狀態欄以「未落地」開頭的列，不得對到任何檔案
#
# R3 與 R5 是同一條規範的兩半，而這張表兩個方向都漂過。文件自己寫下了「狀態」欄
# 存在的理由——未落地的模組，其測試介面是**預定的落點**，不是既有的證據——所以
# R3 放行對不到檔案的預定落點（擋它等於逼人把預定的落點刪掉），R5 擋下相反的那個
# 方向：`api/session` 已經落地了還標著「未落地」，把已經有的證據講成沒有（#117）。
#
# 「部分落地」不算宣告未落地：判定看的是狀態欄**開頭**那幾個字，不是裡面有沒有
# 出現過「未落地」。一半落地一半沒有的模組要講得出是哪一半，而那句話裡一定會提到
# 還沒落地的那半。
#
# R4 是不變式 9 那一條：可推導的數字不進文件。R2 與 R3 一旦守住，集合層級的宣稱
# 就已經被檢查了，再抄一個數字進去只是多一個會過期的副本。**「一個」「一支」不算**
# ——那是不定冠詞，表下面那句「新增一個模組或一支腳本時」正是這樣寫的。
#
# **R4 的代價，寫明免得日後以為它很精準**：它只看「數字後面接個或支」，不看那個
# 數字數的是不是檔案。所以「兩個方向」「三個選項」這種與樹無關的數量也會被擋下，
# 處置是把那句話改寫掉。反過來做——列一張「檔案／腳本／模組／例外」的名詞白名單
# ——會漏掉沒想到的那個名詞，而漏掉的正是下一次漂掉的那個。這一節只有一張表加
# 幾段散文，改寫一句話比漏一次便宜，所以選鈍的那一邊。
#
# **不進 `script/check_file.sh`。** 那支只跑「一個檔案就能判定」的檢查，而這一支
# 要同時讀那份文件與整棵樹——單獨看一個檔案回答不了它。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

readonly PLAN='doc/TEST-PLAN.md'
readonly HEADING='### 模組 → 測試介面'
readonly PACKAGE='src/config_manager'

usage() {
  cat <<'USAGE'
Usage: script/lint_coverage_audit.sh [<repo root>]

  <repo root>  Tree to audit (default: this repo).

  fail  the "模組 → 測試介面" table cannot be read (missing file, missing
        section, or a row whose first cell holds no `backticked path`)
  fail  a src/**/*.py or script/**/*.sh file that no row matches
  fail  a row that matches no file while its 狀態 column does not start 未落地
  fail  a row whose 狀態 column starts 未落地 while the file is already there
  fail  a hand-copied count ("<數字>個" / "<數字>支") inside that section

Row patterns are paths rooted at src/config_manager/, or at the repo root when
they start with script/. A pattern with no .py / .sh / trailing-slash ending
means .py. {a,b} expands, * matches within one segment, **/ matches any depth.
A pattern that ends in neither .py nor .sh is outside the audited set: it is
exempt from the empty-row rule and covers nothing.

This checks that every file has a row -- not that the row says the right test
interface. Which interface observes a module is authored intent; no generator
produces it (invariant 9).
USAGE
}

# 「模組 → 測試介面」那一節的內容，不含標題本身，到下一個 ### 為止。
_section() {
  awk -v heading="${HEADING}" '
    $0 == heading { inside = 1; next }
    inside && /^### / { exit }
    inside { print }
  ' "$1"
}

# 該節第一張表的資料列（跳過表頭與分隔列）。
_rows() {
  awk '
    /^\|/ { seen += 1; if (seen > 2) print; next }
    seen > 0 { exit }
  '
}

# 一列的第 n 欄。以 | 切開後，第 n 欄是第 n+1 個欄位。
_cell() {
  awk -v n="$1" -F'|' '{ print $(n + 1) }'
}

# 第一欄裡那一個反引號路徑；沒有就回非零。
_pattern() {
  local cell="$1"
  [[ "${cell}" == *'`'*'`'* ]] || return 1
  local rest="${cell#*\`}"
  printf '%s' "${rest%%\`*}"
}

# glob 樣式轉成 ERE。{a,b} → (a|b)、**/ → 任意深度、* → 單一路徑段之內。
_regex() {
  local pattern="$1" out='' index=0 char brace=0
  while ((index < ${#pattern})); do
    char="${pattern:index:1}"
    case "${char}" in
      '{')
        out+='('
        brace=1
        ;;
      '}')
        out+=')'
        brace=0
        ;;
      ',')
        if ((brace)); then out+='|'; else out+=','; fi
        ;;
      '*')
        if [[ "${pattern:index:3}" == '**/' ]]; then
          out+='(.*/)?'
          index=$((index + 2))
        else
          out+='[^/]*'
        fi
        ;;
      '.') out+='\.' ;;
      *) out+="${char}" ;;
    esac
    index=$((index + 1))
  done
  printf '%s' "${out}"
}

# 一列的樣式解析成 repo 根目錄底下的完整路徑樣式。副檔名沒寫就是 .py——表上的
# 模組列寫的是模組路徑（`core/state`），不是檔名。
_full_pattern() {
  local pattern="$1"
  case "${pattern}" in
    *.py | *.sh | */) ;;
    *) pattern="${pattern}.py" ;;
  esac
  case "${pattern}" in
    script/*) printf '%s' "${pattern}" ;;
    *) printf '%s/%s' "${PACKAGE}" "${pattern}" ;;
  esac
}

# 狀態欄是不是在宣告「這個模組還沒落地」。看的是開頭，不是有沒有出現過那三個字
# ——「部分落地：……階段未落地（#33）」講的是一半，不是全部。
_declares_pending() {
  local status="$1"
  status="${status#"${status%%[![:space:]]*}"}"
  [[ "${status}" == 未落地* ]]
}

# 受稽核的檔案：src 底下的 .py 與 script 底下的 .sh，路徑相對於 repo 根目錄。
_audited_files() {
  local -a roots=()
  if [[ -d src ]]; then roots+=(src); fi
  if [[ -d script ]]; then roots+=(script); fi
  if ((${#roots[@]} == 0)); then return 0; fi
  find "${roots[@]}" -type f \( -name '*.py' -o -name '*.sh' \) | sort
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  local root
  root="$(cd -- "${1:-${REPO_ROOT}}" 2>/dev/null && pwd -P)" || {
    printf 'lint_coverage_audit: 目錄 %s 不存在，沒有東西可以稽核。下一步：給一個 repo 根目錄\n' \
      "${1:-${REPO_ROOT}}" >&2
    return 1
  }
  cd "${root}"

  if [[ ! -f "${PLAN}" ]]; then
    printf 'lint_coverage_audit: %s 底下沒有 %s，覆蓋率審計表無從比對\n' "${root}" "${PLAN}" >&2
    printf '                     下一步：確認給的是 repo 根目錄\n' >&2
    return 1
  fi

  local section
  section="$(_section "${PLAN}")"
  if [[ -z "${section}" ]]; then
    printf 'lint_coverage_audit: %s 裡找不到「%s」這一節\n' "${PLAN}" "${HEADING}" >&2
    printf '                     下一步：標題要與這個字串逐字相同，這支 lint 以它定位那張表\n' >&2
    return 1
  fi

  local -a rows=()
  mapfile -t rows < <(printf '%s\n' "${section}" | _rows)

  local -a files=()
  mapfile -t files < <(_audited_files)

  local failures=0
  # 每個檔案被幾列對到。索引與 files 對齊。
  local -a covered=()
  local index
  for ((index = 0; index < ${#files[@]}; index++)); do covered[index]=0; done

  local row cell pattern status full regex hits matched
  for row in "${rows[@]}"; do
    cell="$(printf '%s\n' "${row}" | _cell 1)"
    if ! pattern="$(_pattern "${cell}")" || [[ -z "${pattern}" ]]; then
      printf 'FAIL 這一列的第一欄沒有反引號包起來的路徑，讀不出它指哪個檔案：%s\n' \
        "${row}" >&2
      printf '     下一步：第一欄寫成 `core/state` 或 `script/test.sh` 這種形式\n' >&2
      failures=$((failures + 1))
      continue
    fi

    status="$(printf '%s\n' "${row}" | _cell 3)"
    full="$(_full_pattern "${pattern}")"
    regex="$(_regex "${full}")"

    hits=0
    matched=''
    for ((index = 0; index < ${#files[@]}; index++)); do
      if [[ "${files[index]}" =~ ^${regex}$ ]]; then
        covered[index]=1
        hits=$((hits + 1))
        [[ -n "${matched}" ]] || matched="${files[index]}"
      fi
    done

    # 稽核範圍是 .py 與 .sh。`web/` 那一列指的是 HTML 入口，不在範圍內，所以它
    # 對不到檔案不是失準——它本來就不該對到。
    case "${full}" in *.py | *.sh) ;; *) continue ;; esac

    if ((hits == 0)) && ! _declares_pending "${status}"; then
      printf 'FAIL `%s` 這一列對不到任何檔案，狀態卻不是以「未落地」開頭：%s\n' "${pattern}" "${row}" >&2
      printf '     下一步：檔案已經不在就刪掉這一列；還沒落地就把狀態寫成「未落地（#issue）」\n' >&2
      failures=$((failures + 1))
    fi

    if ((hits > 0)) && _declares_pending "${status}"; then
      printf 'FAIL `%s` 這一列標著「未落地」，但 %s 已經在了\n' "${pattern}" "${matched}" >&2
      printf '     下一步：全部落地就把狀態改成「已落地」；只落地一半就寫成「部分落地：<哪一半>已落地；<另一半>未落地（#issue）」\n' >&2
      printf '     把已經有的證據講成沒有，與把沒有的講成有一樣是說謊（不變式 2）\n' >&2
      failures=$((failures + 1))
    fi
  done

  for ((index = 0; index < ${#files[@]}; index++)); do
    if ((covered[index] == 0)); then
      printf 'FAIL %s 在覆蓋率審計表上沒有一列，既不算被覆蓋，也不算刻意留空\n' "${files[index]}" >&2
      printf '     下一步：在 %s 的「%s」表加一列，寫它落在哪個測試介面\n' "${PLAN}" "${HEADING}" >&2
      failures=$((failures + 1))
    fi
  done

  local counted
  while IFS= read -r counted; do
    [[ -n "${counted}" ]] || continue
    printf 'FAIL 這一節手抄了一個可推導的數量：%s\n' "${counted}" >&2
    printf '     下一步：拿掉那個數字。集合對不對得起來由這支 lint 檢查，文件裡再抄一份\n' >&2
    printf '     只會多一個會過期的副本（不變式 9）。「一個」「一支」這種不定冠詞不在此限\n' >&2
    failures=$((failures + 1))
  done < <(printf '%s\n' "${section}" |
    grep -E '([0-9]+|二|三|四|五|六|七|八|九|十|兩)[[:space:]]*(支|個)' || true)

  printf 'lint_coverage_audit: %d 列、%d 個受稽核檔案 -- %d 個問題\n' \
    "${#rows[@]}" "${#files[@]}" "${failures}"
  ((failures == 0))
}

main "$@"
