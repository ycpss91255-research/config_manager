#!/usr/bin/env bats
#
# script/coverage_badge.sh — 把 coverage_gate.sh --summary 寫出的 tsv 畫成
# doc/badge/coverage.svg（T19、#354）。
#
# 這份 SVG 進版控、隨 milestone 收尾更新，發版時要拿來和實測比對。所以它的內容只能
# 由「版本＋四層的數字」決定：同樣的輸入畫出同樣的位元組，不帶日期、時間或任何會
# 自己變動的東西——否則比對永遠對不上，或者更糟，對得上的只是巧合。
#
# 預期的 SVG 寫死在這裡，不由這支腳本再算一次：用同一套算法產生預期值的規格，
# 永遠與被測的程式碼一致（套套邏輯）。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  BADGE="${REPO_ROOT}/script/coverage_badge.sh"
  WORK="$(mktemp -d)"
}

teardown() {
  rm -rf "${WORK}"
}

# 一份 tsv：層名、執行過的行、總行數、下限、判定，以 tab 分開。
write_tsv() {
  printf '%s\n' "$@" | tr '|' '\t' >"${WORK}/coverage.tsv"
}

# v0.9.0 收尾前某一次實測的數字（CI run 43582443320）。
measured_tsv() {
  write_tsv 'core|2216|2256|85|pass' 'io|1472|1538|85|pass' 'api|1676|1754|85|pass' 'web|3588|3778|85|pass'
}

@test "四層與版本畫成的 SVG 與預期逐位元組相同" {
  measured_tsv
  cat >"${WORK}/expected.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="458" height="20"
     role="img" aria-label="coverage: core 98.2% / io 95.7% / api 95.6% / web 95.0% (v0.9.0)">
  <title>coverage: core 98.2% / io 95.7% / api 95.6% / web 95.0% (v0.9.0)</title>
  <rect width="68" height="20" fill="#555"/>
  <rect x="68" width="390" height="20" fill="#4c1"/>
  <g font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"
     fill="#fff" text-anchor="middle">
    <text x="34" y="14">coverage</text>
    <text x="263" y="14">core 98.2% / io 95.7% / api 95.6% / web 95.0% (v0.9.0)</text>
  </g>
</svg>
SVG
  "${BADGE}" v0.9.0 "${WORK}/coverage.tsv" >"${WORK}/actual.svg"
  cmp "${WORK}/expected.svg" "${WORK}/actual.svg"
}

@test "有一層低於下限時，數值那一塊是紅色" {
  write_tsv 'core|80|100|85|below' 'io|90|100|85|pass' 'api|90|100|85|pass' 'web|90|100|85|pass'
  "${BADGE}" v0.9.0 "${WORK}/coverage.tsv" >"${WORK}/actual.svg"
  grep -qF '<rect x="68" width="390" height="20" fill="#e05d44"/>' "${WORK}/actual.svg"
}

@test "--unmeasured 畫出灰色的 not measured 與版本" {
  cat >"${WORK}/expected.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" width="227" height="20"
     role="img" aria-label="coverage: not measured (v0.9.0)">
  <title>coverage: not measured (v0.9.0)</title>
  <rect width="68" height="20" fill="#555"/>
  <rect x="68" width="159" height="20" fill="#9f9f9f"/>
  <g font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"
     fill="#fff" text-anchor="middle">
    <text x="34" y="14">coverage</text>
    <text x="147" y="14">not measured (v0.9.0)</text>
  </g>
</svg>
SVG
  "${BADGE}" --unmeasured v0.9.0 >"${WORK}/actual.svg"
  cmp "${WORK}/expected.svg" "${WORK}/actual.svg"
}

@test "有一層沒量到時以 1 結束，並指名那一層" {
  write_tsv 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|90|100|85|pass' 'web|-|-|85|unmeasured'
  run "${BADGE}" v0.9.0 "${WORK}/coverage.tsv"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"web 沒有被量到"* ]]
}

@test "有一層沒量到時不印 SVG——沒量完整的數字不畫成 badge" {
  write_tsv 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|90|100|85|pass' 'web|-|-|85|unmeasured'
  "${BADGE}" v0.9.0 "${WORK}/coverage.tsv" >"${WORK}/actual.svg" 2>/dev/null || true
  [ ! -s "${WORK}/actual.svg" ]
}

@test "tsv 少一層時以 2 結束，並指名少了哪一層" {
  write_tsv 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|90|100|85|pass'
  run "${BADGE}" v0.9.0 "${WORK}/coverage.tsv"
  [ "${status}" -eq 2 ]
  [[ "${output}" == *"少了 web"* ]]
}

@test "tsv 多出一個不認得的層時以 2 結束，並指名那一行" {
  write_tsv 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|90|100|85|pass' 'web|90|100|85|pass' 'cli|90|100|85|pass'
  run "${BADGE}" v0.9.0 "${WORK}/coverage.tsv"
  [ "${status}" -eq 2 ]
  [[ "${output}" == *"第 5 行"* ]]
}

@test "tsv 有一行欄位數不對時以 2 結束，並指名那一行" {
  write_tsv 'core|90|100|85|pass' 'io|90|100|85' 'api|90|100|85|pass' 'web|90|100|85|pass'
  run "${BADGE}" v0.9.0 "${WORK}/coverage.tsv"
  [ "${status}" -eq 2 ]
  [[ "${output}" == *"第 2 行"* ]]
}

@test "版本不是 vX.Y.Z 時以 2 結束，不畫 badge" {
  # badge 標的是 milestone 的版本；rc 是那一版的候選，不是另一個版本。
  measured_tsv
  run "${BADGE}" v0.9.0-rc1 "${WORK}/coverage.tsv"
  [ "${status}" -eq 2 ]
}
