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
# test/bats/system/entrypoint_smoke.bats 是不同的東西——那是建置期的煙霧測試，在
# Dockerfile 的 runtime-test 階段對建好的映像跑，問的是「裝進去了嗎」。這裡問的是行為，
# 而且問的對象是工作目錄裡的那份腳本，所以它屬於整合層、由 script/test.sh 執行。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  ENTRYPOINT="${REPO_ROOT}/script/entrypoint.sh"
  WORK="$(mktemp -d)"
  export CM_ROLE=backend
  # 對齊 runtime 映像的 ENV（Dockerfile:172）。entrypoint 不自己推算它——那是
  # 映像的職責——所以規格要把它給進來，否則測到的是一個產品裡不存在的環境。
  export PYTHONPATH="${REPO_ROOT}/src"
}

teardown() {
  rm -rf "${WORK}"
}

# 能通過 load() 的最小清單檔，內容同設計文件 §4.3 的範例。
write_minimal_list() {
  cat >"$1/config-list.toml" <<'TOML'
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
TOML
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

@test "空目錄初始化時一併種下最小清單檔，且 load 解析得過" {
  # compose 的 config_repo 是具名 volume，第一次 up 時是空的。沒有種子檔的話
  # 「清單檔不存在 → 失敗」會讓全新安裝根本起不來——而一個什麼都還沒納管的
  # 系統，清單應該是空的，不是開不起來（#66）。
  mkdir -p "${WORK}/repo"

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ -f "${WORK}/repo/config-list.toml" ]

  # 種子檔的合法性由 core 的 load 判定，不由這支規格自己重寫一套 TOML 檢查。
  run python -c "
import sys
from config_manager.core.config_list import load
config_list = load(open('${WORK}/repo/config-list.toml', encoding='utf-8').read())
sys.exit(0 if config_list.files == [] else 1)
"
  [ "${status}" -eq 0 ]
}

@test "種下的清單檔已被提交，不是留在工作區未追蹤" {
  # 留成未追蹤檔的話，io/git.record 的第一次 git add -A 才會把它掃進某一筆
  # 使用者變更裡，那筆紀錄就說了謊——它宣稱的改動不是它真正含的東西。
  mkdir -p "${WORK}/repo"

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ -z "$(git -C "${WORK}/repo" status --porcelain)" ]
}

@test "已是有效 git repo 且清單檔就緒時直接繼續，不重新初始化" {
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  local before
  before="$(git -C "${WORK}" rev-parse --git-dir)"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ "$(git -C "${WORK}" rev-parse --git-dir)" = "${before}" ]
}

@test "repo 已存在但清單檔不見時失敗，原因指名該路徑" {
  # 這不是首次啟動——首次啟動時已經種下了。所以是有人刪了它，或掛錯路徑。
  git init --quiet --initial-branch=main "${WORK}"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"config-list.toml"* ]]
}

@test "清單檔壞掉時在 exec 之前就失敗" {
  # 前置檢查的意義是「不啟動服務」。清單檔壞掉卻讓 exec 跑起來的話，錯誤會延到
  # 之後某個請求才爆——離現場最遠的地方（不變式 2）。
  git init --quiet --initial-branch=main "${WORK}"
  printf 'list_version = [unclosed\n' >"${WORK}/config-list.toml"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"config-list.toml"* ]]
}

@test "來源內容不存在時在 exec 之前就失敗，訊息指名該條目" {
  git init --quiet --initial-branch=main "${WORK}"
  cat >"${WORK}/config-list.toml" <<'TOML'
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = []
TOML

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"navigation-params@amr01-mfz3k9q1"* ]]
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
