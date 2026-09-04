#!/usr/bin/env bats
#
# script/lint_paths.sh — 擋下只有大小寫不同的追蹤路徑。
#
# Linux 分大小寫，這種路徑在 CI 上是兩筆、永遠是綠的；macOS 與 Windows 不分，
# 兩者是同一筆，其中一個贏走簽出、另一個就此不在磁碟上。本 repo 真的發生過：
# 檔案 Dockerfile 與目錄 dockerfile/ 相爭，macOS 上根本簽不出 Dockerfile，
# just test 停在一句既不說原因也不說解法的 hadolint 訊息（#81 修掉）。
#
# 這些規格在容器（Linux）裡跑，所以造得出撞名——那正是 macOS 上造不出來的狀態。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_paths.sh"
  WORK="$(mktemp -d)"
  cd "${WORK}" || return 1
  git init -q .
  git config user.name "spec"
  git config user.email "spec@example.invalid"
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

@test "沒有撞名的 repo 通過" {
  mkdir -p src docker
  printf 'x\n' >src/main.py
  printf 'y\n' >docker/Dockerfile.test-tools
  printf 'z\n' >Dockerfile
  git add -A

  run "${LINT}"
  [ "${status}" -eq 0 ]
}

@test "檔案與目錄只有大小寫不同會被擋下，並同時指名兩者" {
  mkdir -p dockerfile
  printf 'y\n' >dockerfile/Dockerfile.test-tools
  printf 'z\n' >Dockerfile
  git add -A

  run "${LINT}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"Dockerfile"* ]]
  [[ "${output}" == *"dockerfile"* ]]
}

@test "兩個只有大小寫不同的檔案會被擋下" {
  printf 'a\n' >README.md
  printf 'b\n' >readme.md
  git add -A

  run "${LINT}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"README.md"* ]]
  [[ "${output}" == *"readme.md"* ]]
}

@test "深層目錄的撞名也抓得到" {
  mkdir -p a/Config a/config
  printf '1\n' >a/Config/x.txt
  printf '2\n' >a/config/y.txt
  git add -A

  run "${LINT}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"a/Config"* ]]
  [[ "${output}" == *"a/config"* ]]
}

@test "不在 git repo 裡時大聲失敗，訊息指名是哪一支 lint" {
  # 原本會以 git 的 128 中止——大聲，但訊息是 git 的原文，不說是哪一支 lint
  # 在抱怨，也不說下一步（#115）。
  local outside
  outside="$(mktemp -d)"
  cd "${outside}" || return 1

  run "${LINT}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"lint_paths"* ]]
  [[ "${output}" == *"下一步"* ]]

  cd / || true
  rm -rf "${outside}"
}

@test "沒有任何追蹤檔案時不報錯" {
  run "${LINT}"
  [ "${status}" -eq 0 ]
}
