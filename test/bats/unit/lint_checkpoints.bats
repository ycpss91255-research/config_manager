#!/usr/bin/env bats
#
# script/lint_checkpoints.sh —— 驗收條件的帳本要對得起來。
#
# 規則：PR 引用的每一張 issue，其驗收條件必須全部勾完，且每個勾起來的項目必須
# 在同一行記下一個 commit SHA，而那個 SHA 必須真的在這個 PR 的 commit 範圍內。
#
# 依 §0.4「無法自動檢查的規範等同不存在」——這個流程要成立就得有一支工具擋著，
# 否則它只是一段沒有人會在半夜想起來的約定。
#
# 這支 lint 與其餘四支不同：它需要 GitHub API。gh 在規格裡以 PATH 上的假指令替換，
# 所以規格不需要網路，也不會因為某張真的 issue 被改動而轉紅。
#
# #407 起對的是共用綁定判定（script/pr_binding.sh）回傳的那一張 issue：每個 PR 都要用關閉關鍵字
# 綁一張，沒綁就失敗（不再「沒有引用 issue 時通過」）。假 gh 是 gh_fixture.bash 那一支，回 JSON。

load gh_fixture

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_checkpoints.sh"
  WORK="$(mktemp -d)"
  export GITHUB_REPOSITORY=acme/widgets
  gh_fixture_setup
  # 真實的 SHA 由這裡供應：規格造一個臨時 repo，讓 SHA 是真的存在的。
  cd "${WORK}" || return 1
  git init -q .
  git config user.name spec
  git config user.email spec@example.invalid
  git commit -q --allow-empty -m "chore(base): 起點"
  BASE="$(git rev-parse HEAD)"
  SUBJECT="feat(x): 一則變更"
  git commit -q --allow-empty -m "${SUBJECT}"
  SHA="$(git rev-parse HEAD)"
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

# 假 gh 的 PR #1 描述綁 #42（GitHub 的關閉參照也是 #42），#42 的內文是給的帳本。
stub_gh() {
  local issue_body="$1"
  fixture_pr 1 "feat(x): 一則變更（#42）" "closes #42" 42
  fixture_issue 42 "feat(x): 一則變更" "${issue_body}"
}

@test "驗收條件全部勾完且各自帶著本 PR 範圍內的 SHA 時通過" {
  stub_gh "- [x] 第一條 — ${SUBJECT}"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "還有未勾的驗收條件時擋下，並指名是哪一張 issue" {
  stub_gh "- [x] 第一條 — ${SUBJECT}
- [ ] 第二條"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#42"* ]]
}

@test "還有未勾的驗收條件時，下一步是勾完才 gh pr ready（#407）" {
  stub_gh "- [x] 第一條 — ${SUBJECT}
- [ ] 第二條"

  run "${LINT}" 1 "${BASE}"
  [[ "${output}" == *"gh pr ready"* ]]
}

@test "勾起來但沒有記 commit 的項目被擋下" {
  # 「完成」與「有東西證明它完成」是兩件事。只勾不記，帳本就只是一個聲明。
  stub_gh "- [x] 第一條"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"第一條"* ]]
}

@test "記的主旨在 git 歷史裡找不到時被擋下" {
  # 隨手編一句話就能過關的話，這個檢查只是在確認「有寫東西」。
  stub_gh "- [x] 第一條 — feat(x): 這筆 commit 不存在"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"這筆 commit 不存在"* ]]
}

@test "rebase 改寫 SHA 之後帳本仍然成立" {
  # 這個 repo 規定推送前 rebase、分支保護要求與 base 同步——每個 PR 都必然經歷至少一次
  # SHA 改寫（#129）。主旨不會變。
  stub_gh "- [x] 第一條 — ${SUBJECT}"
  git commit -q --amend --no-edit --allow-empty   # SHA 變了，主旨沒變

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "主旨在 main 的歷史裡而不在本 PR 範圍內時通過" {
  # 跨 PR 的 issue：前幾條驗收條件由更早的 PR 完成，那些 commit 已經在 main 上。
  stub_gh "- [x] 第一條 — chore(base): 起點"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "順帶提及的 issue 編號不算本 PR 要交付的" {
  # PR 描述裡寫「見 #117」是引用，不是承諾。只認 GitHub 的關閉關鍵字。
  stub_gh "- [x] 第一條 — ${SUBJECT}"
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "closes #42\n背景見 #117"'
  fixture_issue 117 "另一件事" "- [ ] 還沒做"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "PR 描述沒有關閉關鍵字時失敗，指名 PR，並寫出下一步（#407）" {
  # 每個 PR 都要綁 issue（#407 定案 8）。原本這裡是「沒有引用任何 issue 時通過，但說出來」。
  stub_gh "- [x] 第一條 — ${SUBJECT}"
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "只是改個錯字"; d["closingIssuesReferences"] = []'

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"PR #1"* ]]
  [[ "${output}" == *"下一步"* ]]
}

@test "被引用的 issue 沒有任何勾選框時擋下，訊息指名 issue 並說明下一步" {
  # 一張空帳本的 issue 配一個 closes 的 PR，不應該直接合併。
  # 帳本可以是空的——這是 #158 要修的。
  stub_gh "這張 issue 只是描述一件事，沒有驗收條件。"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#42"* ]]
  [[ "${output}" == *"勾選"* ]]
  [[ "${output}" == *"驗收條件"* ]]
}

@test "描述綁兩張 issue 時擋下：帳本只對共用綁定判定回傳的那一張（#407）" {
  stub_gh "- [x] 第一條 — ${SUBJECT}"
  fixture_issue 43 "另一件事" "- [x] 第一條 — ${SUBJECT}"
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "closes #42\ncloses #43"'

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
}

@test "大寫 [X] 勾選的條目也要記可查到的 commit（#254）" {
  # GitHub 把 [x] 與 [X] 都算已勾、畫面一模一樣。先前正規式只認小寫 x，`- [X]` 勾的條目
  # 完全不進 R3/R4，可不留任何 commit 證據就「勾完」。第一行給合法勾選讓帳本存在。
  stub_gh "- [x] 第一條 — ${SUBJECT}
- [X] 第二條 — feat(x): 這筆 commit 不存在"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"這筆 commit 不存在"* ]]
}

@test "星號／加號項目符號的未勾條件也算未勾（#254）" {
  # GitHub 把 `* [ ]`／`+ [ ]` 都算合法的未勾任務項。先前正規式只認 `-`，於是這類未完成
  # 的驗收條件讓 R2 失效、照樣能開 PR。第一行給合法勾選讓帳本存在。
  stub_gh "- [x] 第一條 — ${SUBJECT}
* [ ] 第二條還沒做"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
}

@test "印出讀到的識別：PR 的 updatedAt 與描述的 sha256、issue 的編號與 updatedAt（#407）" {
  stub_gh "- [x] 第一條 — ${SUBJECT}"
  body_sha="$(printf '%s' "closes #42" | sha256sum | cut -d' ' -f1)"

  run "${LINT}" 1 "${BASE}"
  [[ "${output}" == *"updatedAt=2026-11-21T00:00:00Z"*"body_sha256=${body_sha}"* ]]
  [[ "${output}" == *"識別 issue #42 updatedAt=2026-11-21T00:00:00Z"* ]]
}
