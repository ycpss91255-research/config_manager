#!/usr/bin/env bash
#
# 可攜性 lint。shell 腳本用到只有 GNU 工具接受的選項就失敗。
#
# 這些腳本跑在兩個地方：CI 與檢查映像是帶 GNU coreutils 的 Linux，而貢獻者的機器
# 可能是帶 BSD 工具的 macOS。GNU 專屬的選項在 CI 上會永遠是綠的，只在 macOS 上壞掉
# ——而且不一定壞得大聲。`grep -P` 在這裡就是這樣：BSD grep 拒收該選項，lint_commit
# 的中文主旨檢查因此失效，還回報一個正確的中文主旨「沒有中文」。那是錯的答案，
# 不是錯誤。
#
# 這份清單來自實測，不是來自記憶。底下每一個選項都在 macOS 上跑過、看著它失敗。
# 另有三個看起來很像的候選實測後**沒有**失敗——`readlink -f`、`sort -V`、`xargs -r`
# ——所以刻意不列。要往這份清單加東西之前，先重新實測。
#
# 這個檔案把自己排除在掃描之外：它是唯一必須把違規構造原樣拼出來的腳本，規則裡與
# usage 文字裡都要。
set -euo pipefail

SELF="$(basename "${BASH_SOURCE[0]}")"
readonly SELF

usage() {
  cat <<'USAGE'
Usage: script/lint_portability.sh [<dir>]

  <dir>  Directory of shell scripts to check (default: script).

  fail  an option only GNU tools accept, in a non-comment line

Measured on macOS as failing with BSD tools: grep with -P, sed -i without a
backup suffix, find with -printf, stat with -c, date with -d, base64 with -w,
head with a negative -n, cp with --parents.
USAGE
}

# 名稱<TAB>延伸正規式。正規式刻意錨定在「指令名稱後面接空白」，這樣這張表本身
# 不會比對到自己。
_rules() {
  cat <<'RULES'
grep -P	(^|[^[:alnum:]_-])grep[[:space:]]+-[[:alnum:]]*P
find -printf	(^|[^[:alnum:]_-])find[[:space:]].*[[:space:]]-printf
stat -c	(^|[^[:alnum:]_-])stat[[:space:]]+-[[:alnum:]]*c
date -d	(^|[^[:alnum:]_-])date[[:space:]]+-d[[:space:]]
base64 -w	(^|[^[:alnum:]_-])base64[[:space:]]+-[[:alnum:]]*w
head -n -	(^|[^[:alnum:]_-])head[[:space:]]+-n[[:space:]]+-[0-9]
cp --parents	(^|[^[:alnum:]_-])cp[[:space:]].*--parents
RULES
}

# 非註解行，各自保留原本的行號。
_code_lines() {
  awk '!/^[[:space:]]*#/ { printf "%d:%s\n", NR, $0 }' "$1"
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac
  local dir="${1:-script}"

  if [[ ! -d "${dir}" ]]; then
    # 同 #115：目錄不見了而回報通過，等於一次什麼都沒檢查的執行被讀成綠燈。
    printf 'lint_portability: %s 不是目錄，shell 腳本無從檢查\n' "${dir}" >&2
    printf '                 下一步：確認 script/ 存在，或以第一個參數指定正確的目錄\n' >&2
    return 1
  fi

  local -a files=()
  mapfile -t files < <(find "${dir}" -name '*.sh' -type f | sort)
  if ((${#files[@]} == 0)); then
    printf 'lint_portability: no shell scripts in %s; nothing to check.\n' "${dir}"
    return 0
  fi

  local failures=0 file code name regex hits
  for file in "${files[@]}"; do
    [[ "$(basename "${file}")" == "${SELF}" ]] && continue
    code="$(_code_lines "${file}")"

    while IFS=$'\t' read -r name regex; do
      [[ -n "${name}" ]] || continue
      hits="$(printf '%s\n' "${code}" | grep -E "${regex}" || true)"
      [[ -n "${hits}" ]] || continue
      while IFS= read -r hit; do
        printf 'FAIL  %s:%s  uses %s, which BSD tools reject\n' \
          "${file}" "${hit%%:*}" "${name}" >&2
        failures=$((failures + 1))
      done <<<"${hits}"
    done < <(_rules)

    # sed -i 自成一條規則：GNU 不吃備份字尾，BSD 一定要有，而 `sed -i ''` 是兩邊
    # 都接受的寫法——所以只有沒帶字尾的那種形式才失敗。
    hits="$(printf '%s\n' "${code}" |
      grep -E "(^|[^[:alnum:]_-])sed[[:space:]]+-[[:alnum:]]*i" |
      grep -vE "sed[[:space:]]+-i[[:space:]]+(''|\"\")" || true)"
    if [[ -n "${hits}" ]]; then
      while IFS= read -r hit; do
        printf 'FAIL  %s:%s  uses sed -i with no backup suffix, which BSD rejects\n' \
          "${file}" "${hit%%:*}" >&2
        printf "      write it as: sed -i '' ...\n" >&2
        failures=$((failures + 1))
      done <<<"${hits}"
    fi
  done

  printf 'lint_portability: %d script(s) in %s -- %d violation(s)\n' \
    "${#files[@]}" "${dir}" "${failures}"
  ((failures == 0))
}

main "$@"
