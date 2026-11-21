#!/usr/bin/env bats
#
# script/lint_single_task.sh —— 標題任務編號限制（T19、#351）。
#
# 數的是 commit 主旨、PR 標題與關聯 issue 標題裡的任務編號（F 項與 #編號），不證明內容只有一個
# 任務。本機 hook 只做不必讀 issue 的部分；要讀 PR 與 issue 的部分在 CI（`pr` 模式），讀不到就失敗。
#
# 測的是公開行為：主旨、訊息檔、一份真的 repo 與 pre-push 的標準輸入，或 PATH 上的假 gh 回的
# PR 與 issue 進去，看結束碼與它指名了什麼。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_single_task.sh"
  WORK="$(cd -- "$(mktemp -d)" && pwd -P)"
  STUB="${WORK}/bin"
  mkdir -p "${STUB}"
  export GIT_AUTHOR_NAME=spec GIT_AUTHOR_EMAIL=spec@example.com
  export GIT_COMMITTER_NAME=spec GIT_COMMITTER_EMAIL=spec@example.com
  # pr 模式的綁定數由共用的綁定判定（script/pr_binding.sh，#407）判，它要知道哪個是本 repo。
  export GITHUB_REPOSITORY=acme/widgets
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

json_str() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"
}

# 假 gh：PR 回 ${STUB}/pr.json，issue N 回 ${STUB}/issue-N.json；沒有那張 issue 就失敗。
# 關閉參照只給編號（例如 '{"number": 41}'），repo 補成 acme/widgets；PR 開到 main（#407 的綁定判定要讀）。
stub_gh() {
  local title="$1" body="$2" closing="$3"
  python3 -c '
import json, sys
path, title, body, closing = sys.argv[1:]
items = [dict(item, repository={"name": "widgets", "owner": {"login": "acme"}}) for item in json.loads(closing)]
json.dump({"title": title, "body": body, "baseRefName": "main", "updatedAt": "2026-11-21T00:00:00Z",
           "closingIssuesReferences": items}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
' "${STUB}/pr.json" "${title}" "${body}" "[${closing}]"
  cat >"${STUB}/gh" <<STUB
#!/usr/bin/env bash
case "\$1 \$2" in
  "pr view") cat "${STUB}/pr.json" ;;
  "issue view")
    file="${STUB}/issue-\$3.json"
    [[ -f "\${file}" ]] || { echo "GraphQL: Could not resolve to an issue (\$3)" >&2; exit 1; }
    cat "\${file}"
    ;;
  *) exit 1 ;;
esac
STUB
  chmod +x "${STUB}/gh"
}

# issue <編號> <標題> [內文]：一張開著、沒被完成過的 issue。
issue() {
  printf '{"number": %s, "title": %s, "body": %s, "state": "OPEN", "url": "https://github.com/acme/widgets/issues/%s", "updatedAt": "2026-11-21T00:00:00Z", "comments": []}' \
    "$1" "$(json_str "$2")" "$(json_str "${3:-}")" "$1" >"${STUB}/issue-$1.json"
}

# 一份 PR 的 repo：起點之後，一筆筆以參數給的主旨提交。
pr_repo() {
  REPO="${WORK}/repo"
  git init -q -b main "${REPO}"
  git -C "${REPO}" commit -q --allow-empty -m "chore(base): 起點"
  BASE="$(git -C "${REPO}" rev-parse HEAD)"
  local subject
  for subject in "$@"; do
    git -C "${REPO}" commit -q --allow-empty -m "${subject}"
  done
  cd "${REPO}" || return 1
}

pr() {
  run env PATH="${STUB}:${PATH}" "${LINT}" pr 7 "${BASE}"
}

# ── 主旨與標題的數法 ────────────────────────────────────────────────────

@test "只有一個 F 項與一個 # 編號的主旨通過" {
  run "${LINT}" subject "fix(web): 回滾失敗後保留警示（F36、#345）"

  [ "${status}" -eq 0 ]
}

@test "兩個不同的 F 項被擋下，指名數到的編號並提示拆分" {
  run "${LINT}" subject "fix(web): 回滾恢復步驟與收起上一個結果（F37、F38）"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"F37"*"F38"* ]]
  [[ "${output}" == *"拆"* ]]
}

@test "中間隔著文字、不是列舉寫法的兩個 F 項也算兩個" {
  run "${LINT}" subject "fix(web): 回滾恢復步驟（F37）與收起上一個結果（F38）"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"F37"*"F38"* ]]
}

@test "中間隔著文字、不是列舉寫法的兩個 # 編號也算兩個" {
  run "${LINT}" subject "docs(readme): 一件事（#41）與另一件事（#42）"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#41"*"#42"* ]]
}

@test "範圍寫法一律算多個（F33～F35、F33-35）" {
  local subject
  for subject in "fix(web): 三個問題（F33～F35）" "fix(web): 三個問題（F33-35）"; do
    run "${LINT}" subject "${subject}"
    [ "${status}" -ne 0 ]
  done
}

@test "兩個不同的 # 編號被擋下" {
  run "${LINT}" subject "docs(readme): 兩件事一起改（#1、#2）"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#1"*"#2"* ]]
}

@test "同一個編號出現兩次算一個" {
  run "${LINT}" subject "fix(web): F36 的回滾警示，修正 F36（#345）"

  [ "${status}" -eq 0 ]
}

@test "全形數字與全形＃先轉成半形再數" {
  run "${LINT}" subject "docs(readme): 兩件事（＃３５０、＃３５１）"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#350"*"#351"* ]]
}

@test "前面是英數字的 F<數字>（UTF8）不算 F 項" {
  run "${LINT}" subject "fix(io): 讀 UTF8 與 F36 的編碼（#345）"

  [ "${status}" -eq 0 ]
}

@test "列舉的 scope 被擋下，單一 scope（含 /）通過" {
  run "${LINT}" subject "feat(core,io): 兩層一起改（#9）"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"scope"* ]]

  run "${LINT}" subject "feat(web/help): 說明頁的一節（#9）"
  [ "${status}" -eq 0 ]
}

# ── commit-msg 與 pre-push（本機 hook）──────────────────────────────────

@test "commit-msg 只看訊息檔的第一行，內文與註解行不數" {
  printf 'fix(web): 只修一個（F36）\n\n內文提到 F37、F38 不算\n# 註解裡的 #1、#2 也不算\n' >"${WORK}/msg"

  run "${LINT}" commit-msg "${WORK}/msg"

  [ "${status}" -eq 0 ]
}

@test "commit-msg 的主旨列了兩個 F 項時擋下" {
  printf 'fix(web): 兩個一起修（F37、F38）\n' >"${WORK}/msg"

  run "${LINT}" commit-msg "${WORK}/msg"

  [ "${status}" -ne 0 ]
}

# 一份有 remote 的 repo：remote 上已經有一筆違規的舊 commit。
pushed_repo() {
  git init -q --bare "${WORK}/remote.git"
  REPO="${WORK}/repo"
  git init -q -b main "${REPO}"
  git -C "${REPO}" commit -q --allow-empty -m "feat(core,io): 舊的違規 commit（F1、F2）"
  git -C "${REPO}" remote add origin "${WORK}/remote.git"
  git -C "${REPO}" push -q origin main
  OLD="$(git -C "${REPO}" rev-parse HEAD)"
  cd "${REPO}" || return 1
}

@test "pre-push 只看要推上去的新 commit，remote 上已經有的舊 commit 不數" {
  pushed_repo
  git commit -q --allow-empty -m "fix(web): 新的一筆（F36）"

  run "${LINT}" pre-push <<<"refs/heads/main $(git rev-parse HEAD) refs/heads/main ${OLD}"

  [ "${status}" -eq 0 ]
}

@test "pre-push 的新 commit 違規時擋下，指名那一筆" {
  pushed_repo
  git commit -q --allow-empty -m "fix(web): 兩個一起修（F37、F38）"

  run "${LINT}" pre-push <<<"refs/heads/main $(git rev-parse HEAD) refs/heads/main ${OLD}"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"F37"* ]]
}

@test "pre-push 推新分支時只看 remote 還沒有的 commit" {
  pushed_repo
  git switch -q -c topic
  git commit -q --allow-empty -m "fix(web): 新分支上的一筆（F36）"

  run "${LINT}" pre-push <<<"refs/heads/topic $(git rev-parse HEAD) refs/heads/topic 0000000000000000000000000000000000000000"

  [ "${status}" -eq 0 ]
}

@test "pre-push 遇到刪除分支的那一行時略過" {
  pushed_repo

  run "${LINT}" pre-push <<<"(delete) 0000000000000000000000000000000000000000 refs/heads/gone ${OLD}"

  [ "${status}" -eq 0 ]
}

# ── pr（CI）─────────────────────────────────────────────────────────────

@test "PR 的標題、commit、關聯 issue 都合規時通過" {
  pr_repo "fix(web): 回滾失敗後保留警示（F36、#345）"
  issue 345 "fix(web): F36 回滾失敗後的警示" "F36 的修正"
  stub_gh "fix(web): 回滾失敗後保留警示（F36、#345）" "Closes #345" '{"number": 345}'

  pr

  [ "${status}" -eq 0 ]
}

@test "PR 以關閉關鍵字綁定兩張 issue 時擋下，指名那幾張（共用的綁定判定，#407）" {
  pr_repo "docs(readme): 一筆（#41）"
  issue 41 "docs(readme): 一件事"
  issue 42 "docs(readme): 另一件事"
  stub_gh "docs(readme): 一筆（#41）" "Closes #41, closes #42" '{"number": 41}, {"number": 42}'

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#41"*"#42"* ]]
}

@test "PR 標題列了兩個 F 項時擋下" {
  pr_repo "fix(web): 一筆（#41）"
  issue 41 "fix(web): 一件事"
  stub_gh "fix(web): 兩個一起（F37、F38）" "Closes #41" '{"number": 41}'

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"PR 標題"* ]]
}

@test "PR 裡一筆 commit 主旨列了兩個 # 編號時擋下，merge commit 不數" {
  pr_repo "docs(readme): 兩件事（#41、#42）"
  git switch -q -c side "${BASE}"
  git commit -q --allow-empty -m "docs(readme): 旁支（#41）"
  git switch -q main
  git merge -q --no-ff -m "Merge #1, #2 into main" side
  issue 41 "docs(readme): 一件事"
  stub_gh "docs(readme): 一件事（#41）" "Closes #41" '{"number": 41}'

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"兩件事"* ]]
  [[ "${output}" != *"Merge #1, #2"* ]]
}

@test "寫了 F36（#345）而 #345 的標題與內文都沒有 F36 時擋下" {
  pr_repo "fix(web): 回滾失敗後保留警示（F36、#345）"
  issue 345 "fix(web): 回滾後的警示" "沒有提到那個 F 項"
  stub_gh "fix(web): 回滾失敗後保留警示（F36、#345）" "Closes #345" '{"number": 345}'

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"F36"*"#345"* ]]
}

@test "關聯 issue 的標題列了兩個 F 項時擋下，指名那張 issue（Refs 引用的也算）" {
  pr_repo "fix(web): 一筆（#41）"
  issue 41 "fix(web): 一件事"
  issue 50 "fix(web): 兩個問題（F37、F38）"
  stub_gh "fix(web): 一筆（#41）" $'Closes #41\nRefs #50' '{"number": 41}'

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#50"* ]]
}

@test "有 sub-issue 的 issue 一樣檢查標題，沒有豁免" {
  # 維護者 11/18：新增一張子 issue 就能避開檢查，例外太寬。彙總 issue 的標題照樣數。
  pr_repo "chore(workflow): 一筆（#352）"
  issue 41 "chore(workflow): 一件事"
  issue 352 "chore(workflow): 彙總（#350、#351）" "- [ ] #350\n- [ ] #351"
  stub_gh "chore(workflow): 一筆（#352）" $'Closes #41\nRefs #352' '{"number": 41}'

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#352"* ]]
}

@test "綁定判定不成立時擋下，印出它自己讀到的原因（#407）" {
  pr_repo "docs(readme): 一筆（#41）"
  issue 41 "docs(readme): 一件事"
  stub_gh "docs(readme): 一筆（#41）" "只是改個錯字" ''

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"沒有關閉關鍵字"* ]]
}

@test "pr 模式印出讀到的識別：PR 標題與描述的 sha256、關聯 issue 的編號與 updatedAt（#407）" {
  pr_repo "fix(web): 回滾失敗後保留警示（F36、#345）"
  issue 345 "fix(web): F36 回滾失敗後的警示" "F36 的修正"
  stub_gh "fix(web): 回滾失敗後保留警示（F36、#345）" "Closes #345" '{"number": 345}'
  title_sha="$(printf '%s' "fix(web): 回滾失敗後保留警示（F36、#345）" | sha256sum | cut -d' ' -f1)"

  pr

  [[ "${output}" == *"title_sha256=${title_sha}"* ]]
  [[ "${output}" == *"識別 issue #345 updatedAt=2026-11-21T00:00:00Z"* ]]
}

@test "讀不到關聯的 issue 時失敗，不放行" {
  pr_repo "fix(web): 一筆（#41）"
  stub_gh "fix(web): 一筆（#41）" "Closes #41" '{"number": 41}'

  pr

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"#41"* ]]
}

# ── 登記的 hook 檢查 ──────────────────────────────────────────────────────

@test "登記的 commit-msg 檢查把訊息檔交給 lint_single_task.sh commit-msg" {
  mkdir -p "${WORK}/copy/script/githook-checks/commit-msg"
  cp "${LINT}" "${WORK}/copy/script/lint_single_task.sh"
  cp "${REPO_ROOT}/script/githook-checks/commit-msg/10-single-task.sh" "${WORK}/copy/script/githook-checks/commit-msg/"
  printf 'fix(web): 兩個一起修（F37、F38）\n' >"${WORK}/msg"

  run "${WORK}/copy/script/githook-checks/commit-msg/10-single-task.sh" "${WORK}/msg"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"F37"* ]]
}

@test "登記的 pre-push 檢查把標準輸入交給 lint_single_task.sh pre-push" {
  pushed_repo
  git commit -q --allow-empty -m "fix(web): 兩個一起修（F37、F38）"
  mkdir -p "${REPO}/script/githook-checks/pre-push"
  cp "${LINT}" "${REPO}/script/lint_single_task.sh"
  cp "${REPO_ROOT}/script/githook-checks/pre-push/20-single-task.sh" "${REPO}/script/githook-checks/pre-push/"

  run "${REPO}/script/githook-checks/pre-push/20-single-task.sh" origin "${WORK}/remote.git" \
    <<<"refs/heads/main $(git rev-parse HEAD) refs/heads/main ${OLD}"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"F37"* ]]
}
