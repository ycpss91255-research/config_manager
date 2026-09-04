#!/usr/bin/env bash
#
# ADR lint。檢查 doc/adr/ 的檔名、編號與結構。
#
# 規則就是 ADR-00000017 記下、doc/adr/README.md 敘明的那些。fail／warn 的分界比照
# 該文件（設計 §0.5）：明確的擋，是訊號但有時合理的只警告。
#
#   檔名            須符合 NNNNNNNN-<slug>.md（8 位數補零）           -> fail
#   重號            兩份 ADR 共用同一個編號                           -> fail
#   > 服務：        必須有這條回指                                    -> fail
#   段落            ## Context / ## Decision / ## Consequences        -> 每缺一段 fail
#   Status          Accepted | Rejected | Superseded by ADR-NNNNNNNN  -> 其餘 fail
#   跳號            連續編號中間缺一個                                -> warn
#   ## Alternatives 缺少                                              -> warn
#
# README.md 是唯一豁免的非 ADR 檔。每則訊息都指名是哪個檔案、哪一項。以 bash 3.2
# 可執行的寫法撰寫，好讓 macOS 內建的 shell 也跑得動。
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: script/lint_adr.sh [<adr-dir>]

  <adr-dir>  Directory of ADR files to check (default: doc/adr).

  fail  filename must match NNNNNNNN-<slug>.md
  fail  duplicate ADR number
  fail  missing "> 服務：" backref
  fail  missing ## Context / ## Decision / ## Consequences
  fail  Status not in: Accepted | Rejected | Superseded by ADR-NNNNNNNN
  warn  a gap in the numbering
  warn  missing ## Alternatives
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac
  local dir="${1:-doc/adr}"

  if [[ ! -d "${dir}" ]]; then
    # 「檢查不了」不是「沒有東西要檢查」。目錄不見了（改名、掛載錯、簽出不完整）
    # 而回報通過，就是一次什麼都沒檢查的執行被讀成綠燈（#115）。
    printf 'lint_adr: %s 不是目錄，ADR 無從檢查\n' "${dir}" >&2
    printf '         下一步：確認 doc/adr/ 存在，或以第一個參數指定正確的目錄\n' >&2
    return 1
  fi

  local failures=0 warnings=0 count=0
  local -a entries=() # 每份命名合規的 ADR 一筆 "NNNNNNNN basename"，供重號／跳號用

  local path base num section st
  for path in "${dir}"/*.md; do
    [[ -e "${path}" ]] || continue
    base="$(basename "${path}")"
    [[ "${base}" == "README.md" ]] && continue

    if [[ ! "${base}" =~ ^[0-9]{8}-.+\.md$ ]]; then
      printf 'FAIL %s  filename must match NNNNNNNN-<slug>.md\n' "${base}" >&2
      failures=$((failures + 1))
      continue
    fi

    count=$((count + 1))
    num="${base:0:8}"
    entries+=("${num} ${base}")

    if ! grep -q '^> 服務' "${path}"; then
      printf 'FAIL %s  missing "> 服務：" backref\n' "${base}" >&2
      failures=$((failures + 1))
    fi

    for section in Context Decision Consequences; do
      if ! grep -qx "## ${section}" "${path}"; then
        printf 'FAIL %s  missing "## %s" section\n' "${base}" "${section}" >&2
        failures=$((failures + 1))
      fi
    done

    st=""
    if grep -q '^- \*\*Status:\*\*' "${path}"; then
      st="$(grep -m1 '^- \*\*Status:\*\*' "${path}" |
        sed -E 's/^- \*\*Status:\*\*[[:space:]]*//; s/[[:space:]]*$//')"
    fi
    if [[ ! "${st}" =~ ^(Accepted|Rejected|Superseded\ by\ ADR-[0-9]{8})$ ]]; then
      printf 'FAIL %s  Status "%s" not in: Accepted | Rejected | Superseded by ADR-NNNNNNNN\n' \
        "${base}" "${st}" >&2
      failures=$((failures + 1))
    fi

    if ! grep -qx '## Alternatives' "${path}"; then
      printf 'WARN %s  missing "## Alternatives" section\n' "${base}" >&2
      warnings=$((warnings + 1))
    fi
  done

  if ((count > 0)); then
    # 重號 -> fail；把共用該編號的每個檔案都指名出來。
    local dup files
    while IFS= read -r dup; do
      files="$(printf '%s\n' "${entries[@]}" | awk -v n="${dup}" '$1 == n { printf "%s ", $2 }')"
      printf 'FAIL duplicate number %s: %s\n' "${dup}" "${files}" >&2
      failures=$((failures + 1))
    done < <(printf '%s\n' "${entries[@]}" | awk '{ print $1 }' | sort | uniq -d)

    # 連續編號中間跳號 -> warn。
    local first last n expect
    first="$(printf '%s\n' "${entries[@]}" | awk '{ print $1 }' | sort -u | head -1)"
    last="$(printf '%s\n' "${entries[@]}" | awk '{ print $1 }' | sort -u | tail -1)"
    for ((n = 10#${first}; n <= 10#${last}; n++)); do
      expect="$(printf '%08d' "${n}")"
      if ! printf '%s\n' "${entries[@]}" | awk -v e="${expect}" '$1 == e { f = 1 } END { exit !f }'; then
        printf 'WARN missing number %s (gap in the sequence)\n' "${expect}" >&2
        warnings=$((warnings + 1))
      fi
    done
  fi

  printf 'lint_adr: %d ADR(s) in %s -- %d failure(s), %d warning(s)\n' \
    "${count}" "${dir}" "${failures}" "${warnings}"
  ((failures == 0))
}

main "$@"
