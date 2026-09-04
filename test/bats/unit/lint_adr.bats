#!/usr/bin/env bats
#
# script/lint_adr.sh — ADR 的檔名、編號與結構檢查（ADR-00000017）。
#
# 這是本 repo 第一支腳本層級的規格。在它之前，四支 lint 都沒有任何自動測試，
# 而其中一支已經實際出過事：lint_commit 在 BSD grep 上對正確的中文主旨回報
# 「沒有中文」——守門自己給了錯的答案，沒有東西擋得住。
#
# 測的是公開行為：拿一個目錄餵給腳本，看它的結束碼與訊息，不碰內部實作。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_adr.sh"
  ADRS="$(mktemp -d)"
}

teardown() {
  rm -rf "${ADRS}"
}

# 寫一份結構完整的 ADR。第二個參數若給了段落名，就略過那個段落。
write_adr() {
  local path="$1" omit="${2:-}"
  {
    printf '# 一句話標題\n\n'
    printf '> 服務：不變式 2\n\n'
    printf -- '- **Date:** 2026-09-04\n'
    printf -- '- **Status:** Accepted\n\n'
    [[ "${omit}" == "Context" ]] || printf '## Context\n\n內容\n\n'
    [[ "${omit}" == "Decision" ]] || printf '## Decision\n\n內容\n\n'
    printf '## Alternatives\n\n內容\n\n'
    [[ "${omit}" == "Consequences" ]] || printf '## Consequences\n\n內容\n'
  } >"${path}"
}

@test "結構完整的 ADR 目錄通過" {
  write_adr "${ADRS}/00000001-first.md"
  write_adr "${ADRS}/00000002-second.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -eq 0 ]
}

@test "缺少 > 服務 回指會失敗，並指名該檔" {
  write_adr "${ADRS}/00000001-first.md"
  # 拿掉服務那一行
  grep -v '^> 服務' "${ADRS}/00000001-first.md" >"${ADRS}/tmp" && mv "${ADRS}/tmp" "${ADRS}/00000001-first.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"00000001-first.md"* ]]
  [[ "${output}" == *"服務"* ]]
}

@test "缺少必要段落會失敗，並指名是哪一段" {
  write_adr "${ADRS}/00000001-first.md" "Decision"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Decision"* ]]
}

@test "Status 值不在允許集合內會失敗" {
  write_adr "${ADRS}/00000001-first.md"
  sed -e 's/\*\*Status:\*\* Accepted/**Status:** WIP/' "${ADRS}/00000001-first.md" >"${ADRS}/tmp"
  mv "${ADRS}/tmp" "${ADRS}/00000001-first.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Status"* ]]
}

@test "檔名不符 NNNNNNNN-slug.md 會失敗" {
  write_adr "${ADRS}/not-an-adr.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"not-an-adr.md"* ]]
}

@test "重號會失敗，並同時指名兩個檔" {
  write_adr "${ADRS}/00000001-first.md"
  write_adr "${ADRS}/00000001-again.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"00000001-first.md"* ]]
  [[ "${output}" == *"00000001-again.md"* ]]
}

@test "README.md 不被當成 ADR" {
  write_adr "${ADRS}/00000001-first.md"
  printf '# Architecture Decision Records\n' >"${ADRS}/README.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -eq 0 ]
}
