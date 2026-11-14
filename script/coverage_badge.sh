#!/usr/bin/env bash
#
# 把 coverage_gate.sh --summary 寫出的 coverage.tsv 畫成一份 SVG badge：四層
# （core、io、api、web）各自的覆蓋率，加上這些數字屬於哪一版（#354）。
#
# 這份 SVG 進版控（doc/badge/coverage.svg），README 以相對路徑引用它：不靠任何外部
# 服務，私有 repo 也顯示得出來。它**隨 milestone 收尾更新，不隨每個 PR 更新**
# （維護者 11/14 在 #354 的定案，對齊 ycpss91255-docker/base 的 ADR-00000008）：
# 每次合併都改一個數字，等於每個 PR 都要多一個只動 badge 的 commit，而且多個 PR
# 會在同一個檔案上互相衝突。所以 badge 上寫的是「vX.Y.Z 的覆蓋率」，不是「main 現在的
# 覆蓋率」——少了版本，一個舊數字看起來就像現在的數字。
#
# ## 為什麼輸出只由輸入決定
#
# 發版時要把這份 SVG 與那一版的實測比對。比對靠的是「同樣的數字畫出同樣的位元組」：
# 輸出裡只要有一樣東西會自己變（時間、亂數、主機名），比對就永遠對不上；更糟的是
# 對得上的時候也只是巧合。所以這裡不讀時鐘、不讀環境，寬度以字數推算而不量字型。
#
# ## 為什麼一位小數，而門檻不看它
#
# badge 是給人看的摘要。門檻由 coverage_gate.sh 以原始數值判，與這裡顯示幾位小數無關：
# 84.96% 在這裡顯示成 85.0%，但 tsv 上它的判定是 below，badge 是紅的，CI 也照樣擋。
#
# ## 沒量完整的數字不畫
#
# 任何一層是 unmeasured 就以 1 結束、什麼都不印。三層的數字加一個空格，看起來像一份
# 完整的 badge——而那正是 coverage_gate.sh 把「報告不存在」與「覆蓋率是 0」分開的理由。
# 還沒有屬於某一版的數字時（那一版還沒收尾），用 --unmeasured 明說，不留一個舊數字。
set -euo pipefail

readonly AREAS=(core io api web)

usage() {
  cat <<'USAGE'
用法：script/coverage_badge.sh <vX.Y.Z> <coverage.tsv>
      script/coverage_badge.sh --unmeasured <vX.Y.Z>

  <vX.Y.Z>        這些數字屬於哪一版（milestone 的版本，不是 rc）
  <coverage.tsv>  script/coverage_gate.sh --summary 寫出的那一份；CI 上是 coverage job
                  的 artifact coverage-summary 裡的 coverage.tsv

在 stdout 印出 SVG。四層都達標是綠色，有一層未達是紅色。

  --unmeasured <vX.Y.Z>  印出灰色的 not measured (vX.Y.Z)：那一版還沒有實測數字

milestone 收尾時更新 badge：
  ./script/coverage_badge.sh v0.9.0 coverage.tsv > doc/badge/coverage.svg

  exit 0   印出了 SVG
  exit 1   有一層沒有被量到，沒有印 SVG
  exit 2   用法錯誤、版本格式不對，或 tsv 的格式不對（少一層、多一層、欄位不對）
USAGE
}

_die_usage() {
  printf 'coverage_badge: %s\n' "$1" >&2
  printf 'coverage_badge: 下一步：照 --help 的用法重下指令\n' >&2
  exit 2
}

_check_version() {
  if [[ ! "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf 'coverage_badge: 版本 %s 不是 vX.Y.Z\n' "$1" >&2
    printf 'coverage_badge: 下一步：寫 milestone 的版本，例如 v0.9.0；rc 是那一版的候選，不是另一個版本\n' >&2
    exit 2
  fi
}

# 一塊 flat 樣式的 badge：左邊灰底寫 coverage，右邊是數值。11px 的 DejaVu Sans 平均
# 一個字不到 7px，每字算 7 再加 12 的邊，寧可寬一點，字不會被切掉。
_render() {
  local label="coverage" value="$1" colour="$2"
  local label_width=$((${#label} * 7 + 12))
  local value_width=$((${#value} * 7 + 12))
  local width=$((label_width + value_width))
  cat <<SVG
<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="20"
     role="img" aria-label="${label}: ${value}">
  <title>${label}: ${value}</title>
  <rect width="${label_width}" height="20" fill="#555"/>
  <rect x="${label_width}" width="${value_width}" height="20" fill="${colour}"/>
  <g font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"
     fill="#fff" text-anchor="middle">
    <text x="$((label_width / 2))" y="14">${label}</text>
    <text x="$((label_width + value_width / 2))" y="14">${value}</text>
  </g>
</svg>
SVG
}

_badge() {
  local version="$1" tsv="$2"
  _check_version "${version}"
  if [[ ! -r "${tsv}" ]]; then
    printf 'coverage_badge: 讀不到 %s\n' "${tsv}" >&2
    printf 'coverage_badge: 下一步：給 coverage_gate.sh --summary 寫出的 coverage.tsv；CI 上在 coverage job 的 artifact coverage-summary 裡\n' >&2
    exit 2
  fi

  local -A covered=() total=() state=()
  local line number=0 name c t s
  local -a fields
  while IFS= read -r line || [[ -n "${line}" ]]; do
    number=$((number + 1))
    IFS=$'\t' read -r -a fields <<<"${line}"
    if ((${#fields[@]} != 5)); then
      printf 'coverage_badge: %s 第 %d 行有 %d 個欄位，要 5 個\n' "${tsv}" "${number}" "${#fields[@]}" >&2
      printf 'coverage_badge: 下一步：每行是 層名、執行過的行、總行數、下限、判定，以 tab 分開；重跑 coverage_gate.sh --summary 產生\n' >&2
      exit 2
    fi
    # 第 4 欄（下限）不讀：顏色看的是 coverage_gate.sh 已經判好的第 5 欄，不在這裡重判一次。
    name="${fields[0]}" c="${fields[1]}" t="${fields[2]}" s="${fields[4]}"
    if [[ ! " ${AREAS[*]} " == *" ${name} "* ]]; then
      printf 'coverage_badge: %s 第 %d 行的層名 %s 不認得\n' "${tsv}" "${number}" "${name}" >&2
      printf 'coverage_badge: 下一步：只認 core、io、api、web 四層；重跑 coverage_gate.sh --summary 產生\n' >&2
      exit 2
    fi
    if [[ -n "${state[${name}]:-}" ]]; then
      printf 'coverage_badge: %s 第 %d 行的 %s 重複了\n' "${tsv}" "${number}" "${name}" >&2
      printf 'coverage_badge: 下一步：每層只能有一行；重跑 coverage_gate.sh --summary 產生\n' >&2
      exit 2
    fi
    case "${s}" in
      pass | below)
        if [[ ! "${c}" =~ ^[0-9]+$ || ! "${t}" =~ ^[1-9][0-9]*$ ]]; then
          printf 'coverage_badge: %s 第 %d 行的行數 %s/%s 不是正整數\n' "${tsv}" "${number}" "${c}" "${t}" >&2
          printf 'coverage_badge: 下一步：重跑 coverage_gate.sh --summary 產生，不要手改 tsv\n' >&2
          exit 2
        fi
        ;;
      unmeasured) ;;
      *)
        printf 'coverage_badge: %s 第 %d 行的判定 %s 不認得\n' "${tsv}" "${number}" "${s}" >&2
        printf 'coverage_badge: 下一步：判定只有 pass、below、unmeasured；重跑 coverage_gate.sh --summary 產生\n' >&2
        exit 2
        ;;
    esac
    covered[${name}]="${c}" total[${name}]="${t}" state[${name}]="${s}"
  done <"${tsv}"

  local -a missing=() unmeasured=()
  for name in "${AREAS[@]}"; do
    if [[ -z "${state[${name}]:-}" ]]; then
      missing+=("${name}")
    elif [[ "${state[${name}]}" == unmeasured ]]; then
      unmeasured+=("${name}")
    fi
  done
  if ((${#missing[@]} > 0)); then
    printf 'coverage_badge: %s 少了 %s\n' "${tsv}" "${missing[*]}" >&2
    printf 'coverage_badge: 下一步：四層都要有一行；重跑 coverage_gate.sh --summary 產生\n' >&2
    exit 2
  fi
  if ((${#unmeasured[@]} > 0)); then
    printf 'coverage_badge: %s 沒有被量到，不畫 badge\n' "${unmeasured[*]}" >&2
    printf 'coverage_badge: 下一步：先讓 coverage job 四層都量到；那一版還沒有實測數字時改用 --unmeasured %s\n' "${version}" >&2
    exit 1
  fi

  local value="" colour="#4c1" percent
  for name in "${AREAS[@]}"; do
    percent="$(awk -v c="${covered[${name}]}" -v t="${total[${name}]}" 'BEGIN { printf "%.1f", 100 * c / t }')"
    value+="${value:+ / }${name} ${percent}%"
    if [[ "${state[${name}]}" == below ]]; then
      colour="#e05d44"
    fi
  done
  _render "${value} (${version})" "${colour}"
}

main() {
  case "${1:-}" in
    -h | --help)
      usage
      return 0
      ;;
    --unmeasured)
      [[ $# -eq 2 ]] || _die_usage "--unmeasured 後面要接一個版本"
      _check_version "$2"
      _render "not measured ($2)" "#9f9f9f"
      return 0
      ;;
  esac
  [[ $# -eq 2 ]] || _die_usage "要兩個參數：版本與 coverage.tsv"
  _badge "$1" "$2"
}

main "$@"
