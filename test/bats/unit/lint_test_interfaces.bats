#!/usr/bin/env bats
#
# script/lint_test_interfaces.sh —— 新的測試介面不得與它的第一批測試同一個 commit。
#
# 兩條規則，各有一個會觸發它的案例：
#   規則 A（order）：某個 commit 在 doc/TEST-PLAN.md 新增一個 `### T<N> —`／`### A<N> —`
#                    標題，就不得同時動到 test/ 底下的檔案。不需要 GitHub API。
#   規則 B（agreed）：新增的 `### T<N>` 段落必須有 `#<issue>` 回指，且該 issue 的
#                     createdAt 早於加入這個標題的那個 commit 的 author date。gh 在
#                     規格裡以 PATH 上的假指令替換，所以規格不需要網路。
#
# 只認**新增標題**，不認既有段落的修改：在既有介面上加一條行為（設計 §3.7 管介面、
# 不管每一條行為）不受這條規則約束。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_test_interfaces.sh"
  WORK="$(mktemp -d)"
  STUB="${WORK}/bin"
  mkdir -p "${STUB}"
  PATH="${STUB}:${PATH}"
  export PATH
  cd "${WORK}" || return 1
  git init -q .
  git config user.name spec
  git config user.email spec@example.invalid
  mkdir -p doc test
  printf '### T1 — 既有介面\n\n一些內容。\n' >doc/TEST-PLAN.md
  git add doc/TEST-PLAN.md
  git commit -q -m "chore(base): 起點"
  BASE="$(git rev-parse HEAD)"
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

# ── 規則 A：順序 ──────────────────────────────────────────────────────────

@test "規則 A：新增介面標題又同時動 test/ 的 commit 被擋下，訊息指名介面編號" {
  printf '### T1 — 既有介面\n\n一些內容。\n\n### T5 — 新介面\n\n來由 #42。\n' >doc/TEST-PLAN.md
  printf 'ok\n' >test/new_spec.bats
  git add -A
  git commit -q -m "feat(x): 新介面與它的規格"

  run "${LINT}" order "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"T5"* ]]
}

@test "規則 A：只新增介面標題、不動 test/ 的 commit 通過" {
  printf '### T1 — 既有介面\n\n一些內容。\n\n### T5 — 新介面\n\n來由 #42。\n' >doc/TEST-PLAN.md
  git add -A
  git commit -q -m "docs(test-plan): 新介面段落"

  run "${LINT}" order "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "規則 A：只動 test/、不新增介面標題的 commit 通過" {
  printf 'ok\n' >test/new_spec.bats
  git add -A
  git commit -q -m "test(x): 新規格"

  run "${LINT}" order "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "規則 A：在既有介面加一條行為（動 test/ 但沒新增標題）通過" {
  printf '### T1 — 既有介面\n\n一些內容。\n多一條行為。\n' >doc/TEST-PLAN.md
  printf 'ok\n' >test/t1_more.bats
  git add -A
  git commit -q -m "test(x): T1 多一條行為"

  run "${LINT}" order "${BASE}"
  [ "${status}" -eq 0 ]
}

# ── 規則 B：事先議定 ──────────────────────────────────────────────────────

# 假 gh：issue view --json createdAt 回一個時間戳。
stub_gh_created() {
  local created="$1"
  cat >"${STUB}/gh" <<STUB
#!/usr/bin/env bash
printf '%s' '${created}'
STUB
  chmod +x "${STUB}/gh"
}

@test "規則 B：新介面回指的 issue 建立時間早於該 commit 時通過" {
  stub_gh_created "2020-01-01T00:00:00Z"
  printf '### T1 — 既有介面\n\n### T5 — 新介面\n\n來由 #42。\n' >doc/TEST-PLAN.md
  git add -A
  GIT_AUTHOR_DATE="2020-06-01T00:00:00Z" GIT_COMMITTER_DATE="2020-06-01T00:00:00Z" \
    git commit -q -m "docs(test-plan): 新介面段落"

  run "${LINT}" agreed "${BASE}"
  [ "${status}" -eq 0 ]
}

@test "規則 B：新介面回指的 issue 建立時間晚於該 commit 時被擋下" {
  stub_gh_created "2020-12-01T00:00:00Z"
  printf '### T1 — 既有介面\n\n### T5 — 新介面\n\n來由 #42。\n' >doc/TEST-PLAN.md
  git add -A
  GIT_AUTHOR_DATE="2020-06-01T00:00:00Z" GIT_COMMITTER_DATE="2020-06-01T00:00:00Z" \
    git commit -q -m "docs(test-plan): 新介面段落"

  run "${LINT}" agreed "${BASE}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"T5"* ]]
}

@test "規則 B：新介面段落沒有任何 issue 回指時被擋下" {
  stub_gh_created "2020-01-01T00:00:00Z"
  printf '### T1 — 既有介面\n\n### T5 — 新介面\n\n沒有回指。\n' >doc/TEST-PLAN.md
  git add -A
  git commit -q -m "docs(test-plan): 新介面段落"

  run "${LINT}" agreed "${BASE}"
  [ "${status}" -ne 0 ]
}

@test "用法說明可取得" {
  run "${LINT}" --help
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"lint_test_interfaces"* ]]
}
