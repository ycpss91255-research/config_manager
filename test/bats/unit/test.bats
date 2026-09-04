#!/usr/bin/env bats
#
# script/test.sh — 「缺工具不得靜默通過」的保證（#72）。
#
# 這是五支守門腳本裡唯一真的付出過代價的一支：本機沒有 hadolint，lint 靜默跳過，
# 每次本機都報乾淨而 CI 連紅六次（ADR-00000027 的第二列、CHANGELOG 2026-09-04）。
# 修法是「缺工具即中止」，但那之後沒有任何規格釘住它——#63 關閉時是靠人工逐條複驗
# 才確認行為成立的，那本身就是「沒有自動化保證」的證據。這份補上那一條。
#
# 測的是公開行為：餵一組受控的 PATH 與環境變數進去，看結束碼與訊息，不碰腳本內部。
#
# **「拿掉一支工具」只能靠重建 PATH。** 腳本以 command -v 判定工具在不在，而 command
# 是 shell 內建，沒有覆寫的餘地。所以每個案例自己組一個只放指定工具的目錄當 PATH，
# 沒被連進去的就是缺席——docker 也在其中：沒有任何案例可以真的去建映像。
#
# **子行程一律以 env -i 啟動。** 腳本讀四個環境變數，而規格自己就跑在檢查映像裡
# （CM_IN_TEST_IMAGE=1 由映像設定），不清乾淨的話「不在映像內」的案例造不出來。

# 連真工具進來，不放假的：哪些檢查工具在場是映像的保證（ADR-00000027），
# 不該由規格自己捏造一個。
link_tool() {
  local src
  src="$(command -v "$1")"
  ln -sf "${src}" "${BIN}/$1"
}

# 以受控環境呼叫守門腳本。VAR=VAL 先給，之後是腳本路徑與它的參數。
gate() {
  run env -i PATH="${BIN}" "$@"
}

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  SCRIPT="${REPO_ROOT}/script/test.sh"
  WORK="$(mktemp -d)"
  BIN="${WORK}/bin"
  mkdir -p "${BIN}"

  # 這四支與被測的保證無關，是腳本自己跑起來就要的：shebang 找 bash、REPO_ROOT
  # 的計算用 dirname、列舉 shell 腳本用 find 與 sort。少連一支，紅的會是別的東西。
  link_tool bash
  link_tool dirname
  link_tool find
  link_tool sort
}

teardown() {
  rm -rf "${WORK}"
}

@test "缺一支檢查工具時 lint 中止，並指名該工具與安裝方式" {
  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --lint hadolint

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"hadolint"* ]]
  [[ "${output}" == *"github.com/hadolint/hadolint/releases"* ]]
}

@test "一次列出全部缺席的檢查工具，且只列缺的那些" {
  link_tool ruff
  link_tool mypy
  link_tool pylint
  link_tool shellcheck

  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --lint

  [[ "${output}" == *"hadolint"* ]]
  [[ "${output}" == *"actionlint"* ]]
  [[ "${output}" != *"ruff"* ]]
}

@test "CM_LINT_ALLOW_MISSING=1 讓缺工具的執行以 0 結束" {
  gate CM_IN_TEST_IMAGE=1 CM_LINT_ALLOW_MISSING=1 "${SCRIPT}" --lint hadolint

  [ "${status}" -eq 0 ]
}

@test "降級之後的訊息仍指名哪一項沒跑" {
  gate CM_IN_TEST_IMAGE=1 CM_LINT_ALLOW_MISSING=1 "${SCRIPT}" --lint hadolint

  [[ "${output}" == *"hadolint"* ]]
  [[ "${output}" == *"did NOT run"* ]]
}

@test "降級之後缺席的 shellcheck 不被呼叫，結束碼仍為 0" {
  # 這條與上面那條走的是不同的分支形狀：hadolint 是 `require_tool && 執行`，
  # shellcheck 是 `if require_tool; then 列檔案再執行; fi`。少了守門，缺席的工具
  # 會被真的呼叫下去，結束碼變成 127——那正是「缺工具靜默通過」的反面失敗。
  gate CM_IN_TEST_IMAGE=1 CM_LINT_ALLOW_MISSING=1 "${SCRIPT}" --lint shellcheck

  [ "${status}" -eq 0 ]
  [[ "${output}" != *"command not found"* ]]
}

@test "bats 缺席時 bats 規格不被靜默跳過" {
  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --file "${WORK}/nowhere.bats"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"apt-get install bats"* ]]
}

@test "已在檢查映像內時就地執行，不轉進容器" {
  # 以一個不存在的 linter 當探針：它便宜、必定走到分派判斷之後，而且回的碼
  # （2，unknown linter）與 docker 缺席時的碼（1）分得開。
  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --lint no-such-linter

  [ "${status}" -eq 2 ]
  [[ "${output}" != *"docker"* ]]
}

@test "CM_TEST_LOCAL=1 時就地執行，不轉進容器" {
  gate CM_TEST_LOCAL=1 "${SCRIPT}" --lint no-such-linter

  [ "${status}" -eq 2 ]
  [[ "${output}" != *"docker"* ]]
}

@test "兩者皆未設定時轉進容器，docker 缺席就指出逃生口" {
  gate "${SCRIPT}" --lint no-such-linter

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"docker is not installed"* ]]
  [[ "${output}" == *"CM_TEST_LOCAL=1"* ]]
}
