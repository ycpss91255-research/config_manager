#!/usr/bin/env bats
#
# script/pr_binding.sh —— 共用的綁定判定（T19、#407 調整需求第 5 點）。
#
# 每個 PR 都要用關閉關鍵字綁一張本 repo 還沒完成的 issue，而 checkpoints、single-task、merge-gate
# 與 merge job 認的是同一張。測的是公開行為：一份真的 git repo（PR 範圍與 base 的歷史）與 PATH 上的
# 假 gh 回的 PR 與 issue 進去，看結束碼、最後一行印的綁定與它指名了什麼。

load gh_fixture

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  BINDING="${REPO_ROOT}/script/pr_binding.sh"
  WORK="$(cd -- "$(mktemp -d)" && pwd -P)"
  export GITHUB_REPOSITORY=acme/widgets
  export GIT_AUTHOR_NAME=spec GIT_AUTHOR_EMAIL=spec@example.com
  export GIT_COMMITTER_NAME=spec GIT_COMMITTER_EMAIL=spec@example.com
  gh_fixture_setup
  REPO="${WORK}/repo"
  git init -q -b main "${REPO}"
  git -C "${REPO}" commit -q --allow-empty -m "chore(base): 起點"
  cd "${REPO}" || return 1
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

# PR 範圍：base 之後，一筆筆以參數給的訊息提交。
pr_commits() {
  BASE="$(git rev-parse HEAD)"
  git switch -q -c feat/x
  local message
  for message in "$@"; do
    git commit -q --allow-empty -m "${message}"
  done
}

# 一個合規的 PR #1：描述綁 #7、GitHub 的關閉參照也是 #7、#7 開著。
compliant() {
  fixture_pr 1 "feat(x): 一則變更（#7）" "Closes #7" 7
  fixture_issue 7 "feat(x): 一則變更" "- [x] 第一條 — feat(x): 一則變更（#7）"
}

@test "描述只綁一張開著的 issue、GitHub 的關閉參照也是它時通過，最後一行印出那一張" {
  compliant
  pr_commits "feat(x): 一則變更（#7）"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
  [ "${lines[-1]}" = "pr_binding: 綁定 #7" ]
}

@test "描述寫 Closes: #N 這種帶冒號的寫法照樣通過" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "Closes: #7"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "描述寫 CLOSES #N 這種大寫的寫法照樣通過" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "CLOSES #7"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "描述沒有關閉關鍵字時失敗，指名 PR 並寫出先開 issue 的下一步" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "只是改個錯字，背景見 #7"; d["closingIssuesReferences"] = []'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"PR #1"* ]]
  [[ "${output}" == *"Closes #"* ]]
}

@test "描述只有 Discloses #N 這類不該命中的字時失敗" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "Discloses #7"; d["closingIssuesReferences"] = []'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
}

@test "描述解析出兩張 issue 時失敗，列出那兩張" {
  compliant
  fixture_issue 8 "feat(x): 另一件事" ""
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "Closes #7\nFixes #8"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"#7"*"#8"* ]]
}

@test "關閉參照指到別的 repo 時失敗，號碼和本 repo 某張 issue 相同也一樣" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "Closes other/repo#7"; d["closingIssuesReferences"] = []'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"other/repo"* ]]
}

@test "關閉參照指到 PR 時失敗" {
  fixture_pr 1 "feat(x): 一則變更" "Closes #9" 9
  fixture_issue 9 "feat(x): 一個 PR" ""
  fixture_set "${FIX}/issue-9.json" 'd["url"] = "https://github.com/acme/widgets/pull/9"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"#9 是 PR"* ]]
}

@test "關閉參照指到不存在的編號時失敗，不當成沒有被完成" {
  fixture_pr 1 "feat(x): 一則變更" "Closes #99" 99
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"#99"* ]]
}

@test "關鍵字後面接的是 PR 留言的網址時失敗" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "Closes #7\nCloses https://github.com/acme/widgets/pull/3#issuecomment-1"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"網址"* ]]
}

@test "GitHub 的關閉參照多出描述以外的 issue 時失敗，指名多出的那張" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["closingIssuesReferences"].append(dict(d["closingIssuesReferences"][0], number=8))'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"acme/widgets#8"* ]]
}

@test "GitHub 的關閉參照和描述指的不是同一張時失敗" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["closingIssuesReferences"][0]["number"] = 8'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
}

@test "PR 標題帶關閉參照時失敗，指名標題" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["title"] = "feat(x): 一則變更 fixes #7"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"標題"* ]]
}

@test "PR 的某個 commit 訊息用關閉關鍵字指到綁定以外的 issue 時失敗，指名那個 commit" {
  compliant
  pr_commits "feat(x): 一則變更" "fix(x): 順手修一個

Fixes #8"
  short="$(git rev-parse --short=7 HEAD)"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"commit ${short}"* ]]
}

@test "PR 的某個 commit 訊息用關閉關鍵字指到別的 repo 時失敗" {
  compliant
  pr_commits "fix(x): 順手修一個

Fixes other/repo#7"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
}

@test "commit 訊息的關閉參照指到綁定的那一張時不算多出的" {
  compliant
  pr_commits "feat(x): 一則變更

Closes #7"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "PR 的 base 不是 main 時失敗，下一步寫一律開到 main" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["baseRefName"] = "develop"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"一律開到 main"* ]]
}

@test "綁定的 issue 已關閉時失敗，下一步寫另開 issue" {
  compliant
  fixture_set "${FIX}/issue-7.json" 'd["state"] = "CLOSED"'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"另開 issue"* ]]
}

@test "綁定的 issue 還開著、但已有 merge job 的完成留言時失敗，指名完成它的 PR" {
  compliant
  fixture_set "${FIX}/issue-7.json" 'd["comments"] = [{"author": {"login": "github-actions"}, "body": "由 PR #5 合併完成。"}]'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"PR #5"* ]]
}

@test "網頁合併完成、之後 issue 被重開，第二個 PR 綁它仍失敗" {
  compliant
  fixture_set "${FIX}/issue-7.json" 'd["comments"] = [{"author": {"login": "ycpss91255"}, "body": "由 PR #5 合併完成（網頁合併）"}]'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"PR #5"* ]]
}

@test "base 的歷史裡有 merge job 的合併紀錄寫了這張時失敗，issue 還開著也一樣" {
  compliant
  git commit -q --allow-empty -m "Merge pull request #5" -m "合併紀錄：issue #7；head $(printf 'a%.0s' {1..40})；run 123"
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"已由"* ]]
}

@test "讀不到 PR 時失敗，不放行" {
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"讀不到 PR #1"* ]]
}

@test "讀不到綁定的 issue（查詢失敗）時失敗，不當成沒有被完成" {
  compliant
  printf 'HTTP 403: Resource not accessible by integration\n' >"${FIX}/issue-7.json.fail"
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"讀不到 #7"* ]]
}

@test "fork 的 PR 照同一套判定" {
  compliant
  fixture_set "${FIX}/pr-1.json" 'd["isCrossRepository"] = True'
  pr_commits "feat(x): 一則變更"

  run "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "印出讀到的識別：PR 的 updatedAt 與描述的 sha256、issue 的編號與 updatedAt" {
  compliant
  pr_commits "feat(x): 一則變更"
  body_sha="$(printf '%s' "Closes #7" | sha256sum | cut -d' ' -f1)"

  run "${BINDING}" 1 "${BASE}"
  [[ "${output}" == *"識別 PR #1 updatedAt=2026-11-21T00:00:00Z"*"body_sha256=${body_sha}"* ]]
  [[ "${output}" == *"識別 issue #7 updatedAt=2026-11-21T00:00:00Z"* ]]
}

@test "沒設 GITHUB_REPOSITORY 時以 2 結束" {
  compliant
  pr_commits "feat(x): 一則變更"

  run env -u GITHUB_REPOSITORY "${BINDING}" 1 "${BASE}"
  [ "${status}" -eq 2 ]
}
