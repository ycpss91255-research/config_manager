#!/usr/bin/env bats
#
# script/lint_merge_gate.sh —— 合併前的 TDD red 與 milestone 順序（T19、#407 調整需求第 6 點）。
#
# 測的是公開行為：一份真的 git repo（給綁定判定讀 PR 範圍）與 PATH 上的假 gh（gh_fixture.bash）回的
# PR、issue、milestone 與替身的執行紀錄（run、attempt、jobs、job 紀錄）進去，看結束碼與它指名了什麼。

load gh_fixture

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  GATE="${REPO_ROOT}/script/lint_merge_gate.sh"
  WORK="$(cd -- "$(mktemp -d)" && pwd -P)"
  export GITHUB_REPOSITORY=acme/widgets
  export GIT_AUTHOR_NAME=spec GIT_AUTHOR_EMAIL=spec@example.com
  export GIT_COMMITTER_NAME=spec GIT_COMMITTER_EMAIL=spec@example.com
  gh_fixture_setup
  git init -q -b main "${WORK}/repo"
  cd "${WORK}/repo" || return 1
  git commit -q --allow-empty -m "chore(base): 起點"
  BASE="$(git rev-parse HEAD)"
  git commit -q --allow-empty -m "feat(x): 一則變更（#7）"
  fixture_issue 7 "feat(x): 一則變更" ""
  tdd_body "https://github.com/acme/widgets/actions/runs/100"
  fixture_red_run 100
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

# PR #1 綁 #7，`## TDD red` 段落是給的內容。
tdd_body() {
  fixture_pr 1 "feat(x): 一則變更（#7）" "Closes #7

## TDD red

$1

## 驗證
其他內容" 7
}

gate() {
  run "${GATE}" 1 "${BASE}"
}

# ── 失敗 ──────────────────────────────────────────────────────────────────

@test "PR 描述沒有 ## TDD red 段落時失敗" {
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "Closes #7"'

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"TDD red"* ]]
}

@test "段落裡沒有本 repo 的執行連結、也沒有「不適用」時失敗" {
  tdd_body "之後補"

  gate
  [ "${status}" -eq 1 ]
}

@test "只有別的 repo 的執行連結時失敗" {
  tdd_body "https://github.com/other/repo/actions/runs/100"

  gate
  [ "${status}" -eq 1 ]
}

@test "連結的執行沒有任何一次 attempt 是 failure 時失敗" {
  fixture_red_run 100 attempts=success

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"failure"* ]]
}

@test "連結的執行不是這個 PR 的：同一個分支名稱、別的 PR 的執行不算" {
  fixture_red_run 100 prs=2

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"PR #1"* ]]
}

@test "執行的 pull_requests 沒有這個 PR（例如 fork 帶不到）時失敗" {
  fixture_red_run 100 prs=

  gate
  [ "${status}" -eq 1 ]
}

@test "連結的是功能分支上手動觸發的執行時失敗" {
  fixture_red_run 100 event=workflow_dispatch prs=

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"workflow_dispatch"* ]]
}

@test "連結的不是 .github/workflows/ci.yaml 的執行時失敗" {
  fixture_red_run 100 path=.github/workflows/release.yaml

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"release.yaml"* ]]
}

@test "失敗的只有流程檢查、沒有跑規格的 job 時失敗" {
  fixture_red_run 100 job=checkpoints steps=check

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"checkpoints"* ]]
}

@test "失敗在 build job 時失敗：建映像與 bats 拆不開，不列入" {
  fixture_red_run 100 job=build steps=build

  gate
  [ "${status}" -eq 1 ]
}

@test "失敗只發生在準備步驟（prepare），specs 沒有失敗時失敗" {
  fixture_red_run 100 failing=prepare

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"prepare"* ]]
}

@test "進了 specs 步驟、但紀錄裡沒有規格層級的失敗標記時失敗" {
  fixture_red_run 100 marker=

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"失敗標記"* ]]
}

@test "規格層級的失敗標記只出現在 specs 以外的步驟時不算" {
  fixture_red_run 100 steps=specs,prepare failing=specs
  # specs 是第 0 段時間，把那一步的紀錄換成在 prepare（第 1 段）裡印出 not ok。
  printf '2026-11-21T00:01:30.0000000Z not ok 3 某一條新規格\n' >"$(api_file repos/acme/widgets/actions/jobs/1001/logs)"

  gate
  [ "${status}" -eq 1 ]
}

@test "那一輪跑規格的 job 沒有 specs 步驟時失敗" {
  fixture_red_run 100 steps="Run ./script/test.sh --part bats" failing="Run ./script/test.sh --part bats"

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"specs"* ]]
}

@test "讀不到 attempt 的 jobs（403）時失敗並指名" {
  fixture_red_run 100 jobs_fail="HTTP 403: Resource not accessible by integration"

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"讀不到"* ]]
}

@test "讀不到執行紀錄本身時失敗" {
  printf 'HTTP 403: Resource not accessible by integration\n' >"$(api_file repos/acme/widgets/actions/runs/100).fail"

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"讀不到執行紀錄"* ]]
}

@test "寫「不適用」但改到 doc/ 以外、又不是 .md 的檔時失敗，指名那個檔" {
  tdd_body "不適用：只改說明"
  fixture_files doc/TEST-PLAN.md script/test.sh

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"script/test.sh"* ]]
}

@test "「不適用」後面的理由是空的時失敗" {
  tdd_body "不適用："
  fixture_files doc/TEST-PLAN.md

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"理由"* ]]
}

@test "綁定 issue 的 milestone 前面還有沒關閉的 milestone 時失敗，列出那些 milestone" {
  fixture_milestones "v0.9.0:open" "v0.10.0:open"
  fixture_in_milestone 7 2 v0.10.0

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"v0.9.0"* ]]
}

@test "有 milestone 的標題不是 vX.Y.Z 時失敗並指名" {
  fixture_milestones "backlog:open"
  fixture_in_milestone 7 1 backlog

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"backlog"* ]]
}

@test "綁定判定不成立時失敗，印出綁定判定自己的原因" {
  fixture_set "${FIX}/pr-1.json" 'd["closingIssuesReferences"] = []'

  gate
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"綁定判定不成立"* ]]
}

# ── 通過 ──────────────────────────────────────────────────────────────────

@test "失敗在 bats 的 specs 步驟、紀錄有 not ok 的這個 PR 的執行通過" {
  gate
  [ "${status}" -eq 0 ]
}

@test "較早一次 attempt 失敗在 api (1) 的 specs、之後重跑成綠的執行通過" {
  fixture_red_run 100 attempts=failure,success job="api (1)" marker="FAILED test/pytest/system/test_api.py::test_x - assert 1 == 2"

  gate
  [ "${status}" -eq 0 ]
}

@test "段落有多個連結時，至少一個合格就通過，每個連結的判定都印出來" {
  fixture_red_run 200 event=workflow_dispatch prs=
  tdd_body "https://github.com/acme/widgets/actions/runs/200
https://github.com/acme/widgets/actions/runs/100"

  gate
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"runs/200"*"不算"* ]]
}

@test "純文件 PR 寫「不適用：<理由>」時通過（含 .agents/skills/ 底下的 .md）" {
  tdd_body "不適用：只改說明文件"
  fixture_files doc/TEST-PLAN.md README.md .agents/skills/tdd/SKILL.md

  gate
  [ "${status}" -eq 0 ]
}

@test "綁定的 issue 沒有 milestone 時不檢查順序，其餘條件符合就通過" {
  fixture_milestones "v0.9.0:open" "v0.10.0:open"

  gate
  [ "${status}" -eq 0 ]
}

@test "前面最後一個較早的 milestone 剛關閉時通過" {
  fixture_milestones "v0.9.0:closed" "v0.10.0:open"
  fixture_in_milestone 7 2 v0.10.0

  gate
  [ "${status}" -eq 0 ]
}

@test "版本號逐段比數字：v0.10.0 還開著不擋 v0.9.0 的 PR" {
  fixture_milestones "v0.9.0:open" "v0.10.0:open"
  fixture_in_milestone 7 1 v0.9.0

  gate
  [ "${status}" -eq 0 ]
}

@test "印出讀到的識別：milestone 的 ID、標題與開關狀態，以及 head sha" {
  fixture_milestones "v0.8.0:closed" "v0.9.0:open" "v0.10.0:open"
  fixture_in_milestone 7 2 v0.9.0

  gate
  [[ "${output}" == *"id=902 number=2 title=v0.9.0 state=open"* ]]
  [[ "${output}" == *"id=901 number=1 title=v0.8.0 state=closed"* ]]
  [[ "${output}" == *"識別 head 0000000000000000000000000000000000000000"* ]]
}
