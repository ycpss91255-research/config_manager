#!/usr/bin/env bats
#
# script/lint_commit.sh — commit 訊息檢查（ADR-00000025、ADR-00000028）。
#
# 這支是四支守門裡唯一真的出過事的：它用了 GNU 專屬的 grep -P，在 BSD grep 上
# 整條中文檢查失效，對一個正確的中文主旨回報「沒有中文」——給的是錯的答案，
# 而不是錯誤（#80 修掉）。當時沒有任何規格擋得住，這份就是補上那道防護。
#
# 測的是公開行為：在一個臨時 repo 裡造出 commit，餵 base ref 進去，看結束碼與訊息。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_commit.sh"
  WORK="$(mktemp -d)"
  cd "${WORK}" || return 1
  git init -q .
  git config user.name "spec"
  git config user.email "spec@example.invalid"
  git commit -q --allow-empty -m "chore(base): 起點"
  BASE="$(git rev-parse HEAD)"
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

commit_subject() {
  git commit -q --allow-empty -m "$1"
}

@test "合規的中文主旨通過" {
  commit_subject "feat(core): 清單檔載入時攔截未知欄位"

  run "${LINT}" "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "英文主旨被擋下（#80 的回歸防護）" {
  commit_subject "feat(core): reject unknown fields when the list loads"

  run "${LINT}" "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Chinese"* ]]
}

@test "不在允許集合內的 type 被擋下" {
  commit_subject "wip(core): 還沒寫完"

  run "${LINT}" "${BASE}"
  [ "${status}" -ne 0 ]
}

@test "缺少 type(scope): 前綴被擋下" {
  commit_subject "清單檔載入時攔截未知欄位"

  run "${LINT}" "${BASE}"
  [ "${status}" -ne 0 ]
}

@test "主旨以句號結尾被擋下" {
  commit_subject "feat(core): 清單檔載入時攔截未知欄位。"

  run "${LINT}" "${BASE}"
  [ "${status}" -ne 0 ]
}

@test "缺 scope 只警告，不擋" {
  commit_subject "feat: 清單檔載入時攔截未知欄位"

  run "${LINT}" "${BASE}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"WARN"* ]]
}

@test "多個違規全部回報，不是只報第一個" {
  commit_subject "feat(core): first english subject"
  commit_subject "feat(core): second english subject"

  run "${LINT}" "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"first english subject"* ]]
  [[ "${output}" == *"second english subject"* ]]
}

@test "base 之後沒有 commit 時不報錯" {
  run "${LINT}" "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "squash 合併補上的 (#123) 尾綴不影響判定" {
  commit_subject "feat(core): 清單檔載入時攔截未知欄位 (#123)"

  run "${LINT}" "${BASE}"
  [ "${status}" -eq 0 ]
}
