#!/usr/bin/env bats
#
# script/lint_derived.sh — 文件不得複製一份可從樹推導的東西（不變式 9）。
#
# `README.md` 的「目前狀態」把 `doc/TEST-PLAN.md` 的測試介面清單抄了一份，然後過期
# （#98）。同一份檔案裡還有四處同類的數字，其中三處也已經不對：ADR 份數、下一個 ADR
# 編號、測試介面與驗收旅程的個數、issue 張數。README 沒辦法在被閱讀時計算，所以處置
# 是把它們拿掉、指向來源——而「拿掉」這件事要有東西擋著，否則它只是這一次的清理。
#
# 觀察位置是命令列：餵一個 markdown 檔進去，看結束碼與訊息。與 T19 其餘守門腳本相同。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_derived.sh"
  WORK="$(mktemp -d)"
  DOC="${WORK}/README.md"
}

teardown() {
  rm -rf "${WORK}"
}

_doc() {
  cat >"${DOC}"
}

@test "沒有複製任何可推導內容的文件通過" {
  _doc <<'DOC'
# config_manager

各層與各支腳本的落地狀態見 `doc/TEST-PLAN.md` 的「覆蓋率審計」。
決策紀錄在 `doc/adr/`，格式見 ADR-00000001。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -eq 0 ]
}

@test "抄一份 ADR 份數會被擋下，訊息說該去哪裡數" {
  _doc <<'DOC'
4. **`doc/adr/`** — 22 份決策紀錄：為什麼不是別的樣子
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"22 份決策紀錄"* ]]
  [[ "${output}" == *"doc/adr/"* ]]
}

@test "寫成「份 ADR」的份數同樣被擋下" {
  _doc <<'DOC'
目前有 29 份 ADR。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"29 份 ADR"* ]]
}

@test "把下一個 ADR 編號寫成字面編號會被擋下，訊息指向現有的最大編號" {
  _doc <<'DOC'
有新決策就在 `doc/adr/` 加新號（從 `00000029` 續接）。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"00000029"* ]]
  [[ "${output}" == *"最大編號"* ]]
}

@test "引用某一份 ADR 不算把下一個編號抄下來" {
  _doc <<'DOC'
唯一的服務定義來源（ADR-00000024），單一頂層套件見 ADR-00000026。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -eq 0 ]
}

@test "抄一份測試介面個數會被擋下，訊息指向 TEST-PLAN" {
  _doc <<'DOC'
逐項確認 `doc/TEST-PLAN.md` 的 18 個測試介面。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"18 個測試介面"* ]]
  [[ "${output}" == *"doc/TEST-PLAN.md"* ]]
}

@test "抄一份驗收旅程個數會被擋下" {
  _doc <<'DOC'
以及 6 個驗收旅程。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"6 個驗收旅程"* ]]
}

@test "抄一份 issue 張數會被擋下，訊息指向 issue tracker" {
  _doc <<'DOC'
54 個 issue 依能力矩陣逐項拆分。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"54 個 issue"* ]]
  [[ "${output}" == *"GitHub Issues"* ]]
}

@test "測試介面代號出現在文件裡會被擋下，訊息說那份對照在哪" {
  _doc <<'DOC'
v0.1.0 的 T1（清單檔載入與寫回）與 T5（身分推導）已通過。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"T1"* ]]
  [[ "${output}" == *"覆蓋率審計"* ]]
}

@test "驗收旅程代號出現在文件裡會被擋下" {
  _doc <<'DOC'
依序完成 A1 至 A4。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"A1"* ]]
}

@test "散文裡的數量與大寫字母不被誤擋" {
  _doc <<'DOC'
兩個服務：backend 與 frontend。這個 repo 已經為此付過四次代價。
三類東西只做前兩類。UTF-8 與 IPv4 不是代號。
DOC

  run "${LINT}" "${DOC}"
  [ "${status}" -eq 0 ]
}

@test "要檢查的檔案不在時大聲失敗，不回報乾淨" {
  run "${LINT}" "${WORK}/NOPE.md"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"NOPE.md"* ]]
  [[ "${output}" == *"下一步"* ]]
}
