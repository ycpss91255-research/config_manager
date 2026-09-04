#!/usr/bin/env bash
#
# commit 訊息 lint。檢查這條分支新增的 commit，不檢查歷史。
#
# 規則取自 ycpss91255-docker/base，以它最近 200 筆 commit 取樣得出——因為
# 「對齊 base」只有在「base 實際上怎麼做」被寫在工具讀得到的地方時才檢查得了：
#
#   type      fix 63 / feat 38 / refactor 31 / docs 26 / test 15 /
#             chore 14 / ci 7 / perf 6                       -> 八種 type，fail
#   scope     200 筆中 181 筆有                              -> 缺少則 warn
#   主旨      200 筆中 188 筆首字小寫                        -> 不檢查
#   長度      中位數 90、最長 153                            -> 不檢查
#   issue     200 筆中 118 筆帶 (closes #N) / (refs #N)      -> 不檢查
#
# 大小寫不檢查，而這一行以前宣稱它有檢查。ADR-00000028 把主旨改成中文，中文沒有
# 大小寫，於是這條規則對本 repo 之後寫的每一筆 commit 都恆真——一個永遠不會觸發的
# 檢查。那句過期的宣稱本身就是缺陷：#70 把「主旨首字大寫 -> warn」列進「需要補
# 測試的規則」，就是從這裡抄過去的，而那條規則根本不存在。
#
# fail／warn 的分界比照 ADR lint（§0.5）：明確的擋，是訊號的印出來。缺 scope 通常
# 代表這筆 commit 動到太多東西，但有時它確實橫跨整個 repo——所以它出現在輸出裡，
# 但不擋合併。
#
# 長度刻意不檢查。base 的標題中位數 90 字、最長 153 字；傳統的 50 字上限會擋掉
# 這支 lint 想對齊的那個 repo 的絕大多數 commit。base 寫的是陳述句，說明現在什麼
# 成立（"base owns the orchestrator, the repo owns its bringup"），不是列舉改動的
# 祈使句（"add orchestrator"）。那就是它的風格，加長度上限會安靜地跟它打架。
#
# 語言是本 repo 自己的規則，不是 base 的（ADR-00000028）。base 用英文，是因為讀它
# 的人是取用該模板的任何人；本 repo 不是 base 的 downstream，紀錄一律以中文書寫。
# 所以這一行給機器讀的那一半——type 與 scope——維持 base 定義的形式，給人讀的那一半
# 用這裡的人在用的語言寫。
#
# 歷史不在檢查範圍。只看 origin/main..HEAD——也就是一條分支提出的那些 commit。既有
# commit 早於這條規則，重寫它們意味著對別人手上的分支強制推送。
set -euo pipefail

readonly TYPES="feat|fix|docs|refactor|test|chore|ci|perf"

usage() {
  cat <<'USAGE'
Usage: script/lint_commit.sh [<base-ref>]

  <base-ref>  Commits after this ref are checked (default: origin/main).

Rules, derived from ycpss91255-docker/base:

  fail  type must be one of: feat fix docs refactor test chore ci perf
  fail  the "type(scope): " prefix must be present and well-formed
  fail  subject must not end with a period
  fail  subject must contain Chinese (ADR-00000028); type/scope stay English
  warn  scope should be present -- type(scope): not bare type:

Not checked: title length, issue references. See this file's header for why.
USAGE
}

main() {
  case "${1:-}" in -h|--help) usage; return 0 ;; esac
  local base="${1:-origin/main}"

  if ! git rev-parse --verify --quiet "${base}" >/dev/null; then
    printf 'lint_commit: base ref %s does not exist; nothing to check.\n' "${base}"
    return 0
  fi

  # --no-merges：合併提交的訊息是產生的，不是人寫的。CI 在 pull request 上簽出
  # refs/pull/N/merge，所以 HEAD 是一筆合成的 "Merge <sha> into <sha>"，任何慣例
  # 都不可能符合它——而把 main 併進分支的那種真實合併，同樣不是作者的文字。
  local -a shas=()
  mapfile -t shas < <(git rev-list --no-merges "${base}..HEAD")
  if (( ${#shas[@]} == 0 )); then
    printf 'lint_commit: no commits after %s; nothing to check.\n' "${base}"
    return 0
  fi

  local failures=0 warnings=0 sha subject short
  for sha in "${shas[@]}"; do
    subject="$(git log -1 --format=%s "${sha}")"
    short="${sha:0:7}"

    # squash 合併會補上 " (#123)"；比對前先去掉，這樣一筆已合併的 commit 在
    # 分支上被重新檢查時才不會被規則絆倒。
    local body="${subject% (#[0-9]*)}"

    if [[ ! "${body}" =~ ^(${TYPES})(\([A-Za-z0-9._/,-]+\))?:\  ]]; then
      printf 'FAIL %s  %s\n' "${short}" "${subject}" >&2
      if [[ "${body}" =~ ^([A-Za-z]+)(\(.*\))?: ]]; then
        printf '     type %s is not one of: %s\n' "${BASH_REMATCH[1]}" "${TYPES//|/ }" >&2
      else
        printf '     missing the "type(scope): " prefix\n' >&2
      fi
      printf '     rewrite as: <type>(<scope>): <lowercase sentence saying what is now true>\n' >&2
      failures=$(( failures + 1 ))
      continue
    fi

    # 兩種句號都擋：這條規則早於 ADR-00000028，當時主旨是英文，句號只有 ASCII
    # 那一個。現在主旨是中文，人實際打出來的是 U+3002——只檢查 "." 等於這條規則
    # 對本 repo 真正在寫的語言完全沒有生效。
    if [[ "${body}" == *. || "${body}" == *"。" ]]; then
      printf 'FAIL %s  %s\n' "${short}" "${subject}" >&2
      printf '     subject ends with a period (. or 。); drop it\n' >&2
      failures=$(( failures + 1 ))
      continue
    fi

    # 給人讀的那一半是中文；type 與 scope 維持 base 定義的形式，因為那兩個是
    # 給工具讀的（ADR-00000028）。
    # 這裡測的是「有沒有非 ASCII 位元組」，不是 CJK 字元範圍：grep -P 是 GNU 專屬
    # （macOS 的 BSD grep 直接拒收 -P，於是這個檢查在那裡靜默地誤判，對一個正確的
    # 中文主旨說它沒有中文），而 [一-鿿] 這種 UTF-8 字面範圍在映像的 C.UTF-8 locale
    # 下會被 GNU grep 拒絕（"Invalid collation character"）。LC_ALL=C 把它變成兩種
    # grep 都同意的位元組測試：全英文的主旨沒有任何高於 0x7E 的位元組，中文的有。
    if ! printf '%s' "${body#*: }" | LC_ALL=C grep -q '[^ -~]'; then
      printf 'FAIL %s  %s\n' "${short}" "${subject}" >&2
      printf '     subject has no Chinese. This repo keeps its record in Chinese;\n' >&2
      printf '     only the type(scope) prefix stays as base defines it.\n' >&2
      printf '     e.g. feat(core): 清單檔載入時攔截未知欄位\n' >&2
      failures=$(( failures + 1 ))
      continue
    fi

    if [[ ! "${body}" =~ ^(${TYPES})\( ]]; then
      printf 'WARN %s  %s\n' "${short}" "${subject}" >&2
      printf '     no scope. base carries one on 181 of its 200 most recent commits;\n' >&2
      printf '     a commit with no nameable scope often touches too much\n' >&2
      warnings=$(( warnings + 1 ))
    fi

  done

  printf 'lint_commit: %d commit(s) after %s -- %d failure(s), %d warning(s)\n' \
    "${#shas[@]}" "${base}" "${failures}" "${warnings}"
  (( failures == 0 ))
}

main "$@"
