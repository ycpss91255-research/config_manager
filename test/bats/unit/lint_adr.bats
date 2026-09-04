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
    [[ "${omit}" == "Alternatives" ]] || printf '## Alternatives\n\n內容\n\n'
    [[ "${omit}" == "Consequences" ]] || printf '## Consequences\n\n內容\n'
  } >"${path}"
}

# 把某份 ADR 的 Status 換成別的值。
set_status() {
  local path="$1" value="$2"
  sed -e "s/\*\*Status:\*\* Accepted/**Status:** ${value}/" "${path}" >"${ADRS}/tmp"
  mv "${ADRS}/tmp" "${path}"
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

@test "缺少 Decision 會失敗，並指名是哪一段" {
  write_adr "${ADRS}/00000001-first.md" "Decision"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Decision"* ]]
}

@test "缺少 Context 會失敗，並指名是哪一段" {
  write_adr "${ADRS}/00000001-first.md" "Context"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Context"* ]]
}

@test "缺少 Consequences 會失敗，並指名是哪一段" {
  write_adr "${ADRS}/00000001-first.md" "Consequences"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Consequences"* ]]
}

@test "Status 值不在允許集合內會失敗" {
  write_adr "${ADRS}/00000001-first.md"
  set_status "${ADRS}/00000001-first.md" "WIP"

  run "${LINT}" "${ADRS}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Status"* ]]
}

@test "Status 為 Superseded by ADR-NNNNNNNN 是合法值" {
  # 取代一份 ADR 不是刪掉它——ADR-00000005 的同一條理由：紀錄只增不改。
  # 這個值長得和 Accepted 完全不同，正則要真的容得下它。
  write_adr "${ADRS}/00000001-first.md"
  set_status "${ADRS}/00000001-first.md" "Superseded by ADR-00000002"
  write_adr "${ADRS}/00000002-second.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -eq 0 ]
}

@test "缺少 Alternatives 只警告，不擋" {
  # 被否決的選項是 ADR 最有價值的一段，但少了它不足以擋下合併——
  # 與 lint_commit 的缺 scope 同一種 fail/warn 切分。
  write_adr "${ADRS}/00000001-first.md" "Alternatives"

  run "${LINT}" "${ADRS}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"WARN"* ]]
  [[ "${output}" == *"Alternatives"* ]]
}

@test "編號跳號只警告，並指名缺的是哪一號" {
  # 跳號可能是刻意的（草稿被丟棄），也可能是撞號後有人改錯邊，
  # 分不出來所以不擋，但要說出來。
  write_adr "${ADRS}/00000001-first.md"
  write_adr "${ADRS}/00000003-third.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"WARN"* ]]
  [[ "${output}" == *"00000002"* ]]
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

@test "目錄不存在時大聲失敗，不回報通過" {
  # 「檢查不了」不是「沒有東西要檢查」。目錄改名或掛載錯而回報通過，就是一次
  # 什麼都沒檢查的執行被讀成綠燈（#115）。
  run "${LINT}" "${ADRS}/nowhere"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"下一步"* ]]
}

@test "README.md 不被當成 ADR" {
  write_adr "${ADRS}/00000001-first.md"
  printf '# Architecture Decision Records\n' >"${ADRS}/README.md"

  run "${LINT}" "${ADRS}"
  [ "${status}" -eq 0 ]
}
