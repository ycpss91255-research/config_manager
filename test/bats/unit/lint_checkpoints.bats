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

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_checkpoints.sh"
  WORK="$(mktemp -d)"
  STUB="${WORK}/bin"
  mkdir -p "${STUB}"
  PATH="${STUB}:${PATH}"
  export PATH
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

# 造一個假的 gh：第一次呼叫回 PR 內文，之後回 issue 內文。
stub_gh() {
  local pr_body="$1" issue_body="$2"
  cat >"${STUB}/gh" <<STUB
#!/usr/bin/env bash
case "\$1 \$2" in
  "pr view") printf '%s' '${pr_body}' ;;
  "issue view") printf '%s' '${issue_body}' ;;
  *) exit 1 ;;
esac
STUB
  chmod +x "${STUB}/gh"
}

@test "驗收條件全部勾完且各自帶著本 PR 範圍內的 SHA 時通過" {
  stub_gh "closes #42" "- [x] 第一條 — ${SUBJECT}"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "還有未勾的驗收條件時擋下，並指名是哪一張 issue" {
  stub_gh "closes #42" "- [x] 第一條 — ${SUBJECT}
- [ ] 第二條"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#42"* ]]
}

@test "勾起來但沒有記 commit 的項目被擋下" {
  # 「完成」與「有東西證明它完成」是兩件事。只勾不記，帳本就只是一個聲明。
  stub_gh "closes #42" "- [x] 第一條"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"第一條"* ]]
}

@test "記的主旨在 git 歷史裡找不到時被擋下" {
  # 隨手編一句話就能過關的話，這個檢查只是在確認「有寫東西」。
  stub_gh "closes #42" "- [x] 第一條 — feat(x): 這筆 commit 不存在"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"這筆 commit 不存在"* ]]
}

@test "rebase 改寫 SHA 之後帳本仍然成立" {
  # 這個 repo 規定推送前 rebase、分支保護要求與 base 同步、合併一律 squash——
  # 三條加起來，每個 PR 都必然經歷至少一次 SHA 改寫（#129）。主旨不會變。
  stub_gh "closes #42" "- [x] 第一條 — ${SUBJECT}"
  git commit -q --amend --no-edit --allow-empty   # SHA 變了，主旨沒變

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "主旨在 main 的歷史裡而不在本 PR 範圍內時通過" {
  # 跨 PR 的 issue：前幾條驗收條件由更早的 PR 完成，那些 commit 已經在 main 上。
  stub_gh "closes #42" "- [x] 第一條 — chore(base): 起點"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "順帶提及的 issue 編號不算本 PR 要交付的" {
  # PR 描述裡寫「見 #117」是引用，不是承諾。只認 GitHub 的關閉關鍵字。
  stub_gh "修正一個問題，背景見 #117" "- [ ] 還沒做"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"沒有引用"* ]]
}

@test "PR 沒有引用任何 issue 時通過，但說出來" {
  stub_gh "只是改個錯字" ""

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"沒有引用"* ]]
}

@test "沒有驗收條件的 issue 通過" {
  # 不是每張 issue 都帶勾選框；沒有框就沒有要對的帳。
  stub_gh "closes #42" "這張 issue 只是描述一件事，沒有驗收條件。"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "一個 PR 引用多張 issue 時，每一張都要對" {
  # 假 gh 對兩張 issue 回同一份內文，其中有未勾的項目。
  stub_gh "closes #42
closes #43" "- [ ] 還沒做"

  run "${LINT}" 1 "${BASE}"
  [ "${status}" -ne 0 ]
}
