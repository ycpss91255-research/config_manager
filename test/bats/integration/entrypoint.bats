#!/usr/bin/env bats
#
# script/entrypoint.sh — 啟動前置檢查（測試介面 T15）。
#
# 這一層測的是「掛載進來的 config-repo 長什麼樣，entrypoint 怎麼反應」。用真實的
# 目錄與真實的 git，因為要驗的正是它對檔案系統狀態的判斷。
#
# entrypoint 的最後一行是 exec "$@"，所以規格餵它一個 true：前置檢查過了就結束碼 0，
# 沒過就在 exec 之前 die。
#
# test/bats/smoke/entrypoint.bats 是不同的東西——那是建置期的煙霧測試，在 Dockerfile
# 的 runtime-test 階段跑（Dockerfile:194），問的是「裝進去了嗎」。這裡問的是行為。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  ENTRYPOINT="${REPO_ROOT}/script/entrypoint.sh"
  WORK="$(mktemp -d)"
  export CM_ROLE=backend
}

teardown() {
  rm -rf "${WORK}"
}

@test "掛載非空但沒有 .git 會失敗，不自動初始化" {
  # 這不是首次啟動——那裡有沒人審過的檔案。#86 之後 io/git.record 的第一步是
  # git add -A，自動 git init 等於把它們默默收編進第一次提交，而掛錯路徑會
  # 因此看起來像啟動成功（不變式 2 禁止的靜默成功）。
  printf 'not ours\n' >"${WORK}/stray.txt"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [ ! -e "${WORK}/.git" ]
}

@test "空目錄被初始化並繼續，訊息帶解析後的絕對路徑" {
  # 空目錄是首次啟動的合法狀態。掛錯到一個空目錄與真正的首次啟動，從容器內部
  # 分不出來——所以路徑要印成解析後的絕對形式，那是操作者唯一能認的東西。
  # 餵一條繞路的路徑，斷言印出來的不是原樣回吐。
  mkdir -p "${WORK}/repo" "${WORK}/other"

  CM_CONFIG_REPO="${WORK}/other/../repo" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"${WORK}/repo"* ]]
  [ -d "${WORK}/repo/.git" ]
}

@test "已是有效 git repo 時直接繼續，不重新初始化" {
  git init --quiet --initial-branch=main "${WORK}"
  local before
  before="$(git -C "${WORK}" rev-parse --git-dir)"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ "$(git -C "${WORK}" rev-parse --git-dir)" = "${before}" ]
}

@test "掛載不存在會失敗，原因指名該路徑" {
  CM_CONFIG_REPO="${WORK}/nowhere" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"${WORK}/nowhere"* ]]
}

@test "有 .git 但不是有效 git repo 會失敗" {
  printf 'not a gitfile\n' >"${WORK}/.git"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
}

@test "CM_CONFIG_REPO 未設定會失敗" {
  unset CM_CONFIG_REPO

  run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"CM_CONFIG_REPO"* ]]
}
