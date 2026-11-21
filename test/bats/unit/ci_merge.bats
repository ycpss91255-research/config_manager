#!/usr/bin/env bats
#
# script/ci_merge.sh 與 script/gh_snapshot.sh —— merge job（T19、#407 調整需求第 1 點）。
#
# merge job 排在 ci-rollup 之後：合併前把讀 PR 或 issue 資料的檢查在同一份現況資料上重跑一次，全過才
# 以 merge commit 合併，只關判定出來的那一張 issue；PR 已合併時（重跑）只補作業。
#
# 測的是公開行為：一份真的 git repo（PR 範圍與 base 的歷史）與 PATH 上的假 gh（gh_fixture.bash）回的
# PR、issue、執行紀錄進去，看結束碼、它指名了什麼，以及它對 gh 送了哪些會寫入的呼叫（合併、關 issue、
# 留言只記在 calls.log，不真的發生）。重判跑的是 script/ 底下真的那幾道檢查。

load gh_fixture

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  MERGE="${REPO_ROOT}/script/ci_merge.sh"
  WORK="$(cd -- "$(mktemp -d)" && pwd -P)"
  export GITHUB_REPOSITORY=acme/widgets GITHUB_RUN_ID=555 CM_CI_ROLLUP_RESULT=success
  export GIT_AUTHOR_NAME=spec GIT_AUTHOR_EMAIL=spec@example.com
  export GIT_COMMITTER_NAME=spec GIT_COMMITTER_EMAIL=spec@example.com
  gh_fixture_setup
  git init -q -b main "${WORK}/repo"
  cd "${WORK}/repo" || return 1
  git commit -q --allow-empty -m "chore(base): 起點"
  BASE="$(git rev-parse HEAD)"
  git switch -q -c feat/x
  git commit -q --allow-empty -m "feat(x): 一則變更（#7）"
  HEAD_SHA="$(git rev-parse HEAD)"
  RED="https://github.com/acme/widgets/actions/runs/100"
  fixture_pr 1 "feat(x): 一則變更（#7）" "Closes #7

## TDD red

${RED}" 7
  fixture_set "${FIX}/pr-1.json" "d['headRefOid'] = '${HEAD_SHA}'"
  fixture_issue 7 "feat(x): 一則變更" "- [x] 第一條 — feat(x): 一則變更（#7）"
  fixture_red_run 100
  fixture_runs 555:5
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

# fixture_runs <run id>:<run number>[:<PR 編號>]…：這個分支在 ci.yaml 的 pull_request 執行清單（a0 讀它）。
fixture_runs() {
  python3 - "$(api_file 'repos/acme/widgets/actions/workflows/ci.yaml/runs?event=pull_request&branch=feat/x&per_page=100')" "$@" <<'PY'
import json, sys
path, *items = sys.argv[1:]
runs = []
for item in items:
    run_id, run_number, *pr = item.split(":")
    runs.append({"id": int(run_id), "run_number": int(run_number), "pull_requests": [{"number": int(pr[0] if pr else 1)}]})
json.dump({"total_count": len(runs), "workflow_runs": runs}, open(path, "w", encoding="utf-8"))
PY
}

# 一張合規的 issue <N>：帳本勾完、記的主旨在 PR 裡。
compliant_issue() {
  fixture_issue "$1" "feat(x): 另一件事" "- [x] 第一條 — feat(x): 一則變更（#7）"
}

# 描述改綁到 #<N>（GitHub 的關閉參照跟著改），TDD red 段落照舊。
rebind() {
  fixture_set "$1" "d['body'] = d['body'].replace('Closes #7', 'Closes #$2'); d['closingIssuesReferences'][0]['number'] = $2"
}

# PR 已經合併：fixture_merged <合併者> <紀錄裡的 run id> [<第二個 parent>]
MERGE_OID="abcdabcdabcdabcdabcdabcdabcdabcdabcdabcd"
fixture_merged() {
  fixture_set "${FIX}/pr-1.json" "d['state'] = 'MERGED'; d['mergedBy'] = {'login': '$1', 'is_bot': True}; d['mergeCommit'] = {'oid': '${MERGE_OID}'}"
  python3 - "$(api_file "repos/acme/widgets/commits/${MERGE_OID}")" "${BASE}" "${3:-${HEAD_SHA}}" "${HEAD_SHA}" "$2" <<'PY'
import json, sys
path, base, second, head, run = sys.argv[1:]
json.dump({"sha": "abcd" * 10, "parents": [{"sha": base}, {"sha": second}],
           "commit": {"message": f"Merge pull request #1\n\n合併紀錄：issue #7；head {head}；run {run}"}},
          open(path, "w", encoding="utf-8"), ensure_ascii=False)
PY
}

bot_comment() {
  fixture_set "${FIX}/issue-$1.json" "d['comments'] = [{'author': {'login': 'github-actions'}, 'body': '由 PR #1 合併完成。'}]"
}

merge_job() {
  run "${MERGE}" 1 "${HEAD_SHA}" "${BASE}"
}

merged() {
  [ "$(gh_calls pr merge 1)" -eq 1 ]
}

not_merged() {
  [ "${status}" -eq 1 ]
  [ "$(gh_calls pr merge)" -eq 0 ]
}

# ── 合併 ───────────────────────────────────────────────────────────────────

@test "全部條件成立、重判全部通過時以 merge commit 合併，鎖住這一輪的 head" {
  merge_job

  [ "${status}" -eq 0 ]
  grep -qF -- "$(printf 'pr\tmerge\t1\t--repo\tacme/widgets\t--merge\t--match-head-commit\t%s' "${HEAD_SHA}")" "${FIX}/calls.log"
}

@test "合併後只關判定出來的那一張 issue，並留言由 PR 合併完成" {
  merge_job

  [ "$(gh_calls issue close 7)" -eq 1 ]
  grep -qF -- "$(printf 'issue\tcomment\t7\t--repo\tacme/widgets\t--body\t由 PR #1 合併完成。')" "${FIX}/calls.log"
}

@test "合併訊息的主旨固定是 Merge pull request #<n>，內文是一行紀錄（issue、head、run id）" {
  merge_job

  grep -qF -- "$(printf -- '--subject\tMerge pull request #1\t--body\t合併紀錄：issue #7；head %s；run 555' "${HEAD_SHA}")" "${FIX}/calls.log"
}

@test "PR 標題不進合併訊息：主旨不帶標題與分支名稱" {
  merge_job

  merged
  ! grep -F -- '--subject' "${FIX}/calls.log" | grep -qF -- '一則變更'
}

@test "同一個 head 上換綁到另一張也合規的 issue：照現況合併，關的與紀錄寫的都是新綁的那一張" {
  compliant_issue 8
  rebind "${FIX}/pr-1.json" 8

  merge_job

  [ "$(gh_calls issue close 8)" -eq 1 ]
  [ "$(gh_calls issue close 7)" -eq 0 ]
  grep -qF -- '合併紀錄：issue #8；' "${FIX}/calls.log"
}

@test "判定途中描述被改綁：帳本、TDD red、milestone 與合併紀錄用的是同一個 issue 編號" {
  # 假 gh 回完第一次 PR 之後就換成改綁到 #8 的那一份；快照只讀一次，每一道檢查看到的都是 #7。
  compliant_issue 8
  cp "${FIX}/pr-1.json" "${FIX}/pr-1.json.next"
  rebind "${FIX}/pr-1.json.next" 8

  merge_job

  [ "$(gh_calls issue close 7)" -eq 1 ]
  [ "$(gh_calls issue close 8)" -eq 0 ]
  grep -qF -- '合併紀錄：issue #7；' "${FIX}/calls.log"
}

@test "帳本重判：rebase 後 SHA 變了、主旨仍在的 commit 照樣接受" {
  git commit -q --amend --no-edit --allow-empty
  HEAD_SHA="$(git rev-parse HEAD)"
  fixture_set "${FIX}/pr-1.json" "d['headRefOid'] = '${HEAD_SHA}'"

  merge_job

  merged
}

@test "紀錄裡印出重判資料的識別：PR 的 updatedAt 與標題、描述的 sha256，issue 的編號與 updatedAt，head" {
  merge_job

  [[ "${output}" == *"識別 PR #1 updatedAt=2026-11-21T00:00:00Z title_sha256="*"body_sha256="*"head=${HEAD_SHA}"* ]]
  [[ "${output}" == *"識別 issue #7 updatedAt=2026-11-21T00:00:00Z"* ]]
}

@test "紀錄裡印出 milestone 順序用到的每一個 milestone 的 ID、標題與開關狀態" {
  fixture_milestones "v0.9.0:closed" "v0.10.0:open"
  fixture_in_milestone 7 2 v0.10.0

  merge_job

  [[ "${output}" == *"id=901 number=1 title=v0.9.0 state=closed"* ]]
}

@test "網頁合併的比對：重跑後 PR 沒變、issue 的帳本改了，印出的 issue 識別就不同" {
  merge_job
  before="$(grep -F '識別 issue #7' <<<"${output}")"
  fixture_set "${FIX}/issue-7.json" 'd["updatedAt"] = "2026-11-22T00:00:00Z"'

  merge_job
  after="$(grep -F '識別 issue #7' <<<"${output}")"

  [ "${before}" != "${after}" ]
}

# ── 不合併 ─────────────────────────────────────────────────────────────────

@test "草稿時不合併，指名草稿" {
  fixture_set "${FIX}/pr-1.json" 'd["isDraft"] = True'

  merge_job
  not_merged
  [[ "${output}" == *"草稿"* ]]
}

@test "fork 的 PR 不合併" {
  fixture_set "${FIX}/pr-1.json" 'd["isCrossRepository"] = True'

  merge_job
  not_merged
  [[ "${output}" == *"fork"* ]]
}

@test "base 不是 main 時不合併" {
  fixture_set "${FIX}/pr-1.json" 'd["baseRefName"] = "develop"'

  merge_job
  not_merged
}

@test "ci-rollup 不是 success 時不合併" {
  CM_CI_ROLLUP_RESULT=failure merge_job
  not_merged
  [[ "${output}" == *"ci-rollup"* ]]
}

@test "重跑的是舊的一輪（之後又有推送，head 不等於事件的 head）時不合併，指出要看最新那一輪" {
  fixture_set "${FIX}/pr-1.json" 'd["headRefOid"] = "f" * 40'

  merge_job
  not_merged
  [[ "${output}" == *"看最新那一輪"* ]]
}

@test "同一個 head 上已經有更新的一輪（例如標題改過）時，舊的一輪不合併" {
  fixture_runs 555:5 556:6

  merge_job
  not_merged
  [[ "${output}" == *"最新"*"556"* ]]
}

@test "同一個 head 上拿掉 ## TDD red 時不合併，指名 merge-gate" {
  fixture_set "${FIX}/pr-1.json" 'd["body"] = "Closes #7"'

  merge_job
  not_merged
  [[ "${output}" == *"merge-gate"* ]]
}

@test "同一個 head 上換綁到帳本沒勾完的 issue 時不合併，指名 checkpoints" {
  fixture_issue 8 "feat(x): 另一件事" "- [x] 第一條 — feat(x): 一則變更（#7）
- [ ] 第二條"
  rebind "${FIX}/pr-1.json" 8

  merge_job
  not_merged
  [[ "${output}" == *"重判沒過：checkpoints"* ]]
}

@test "checkpoints 綠了之後才改綁到不合規的 issue：merge job 自己印出實際的原因與下一步" {
  fixture_issue 8 "feat(x): 另一件事" "- [ ] 還沒做"
  rebind "${FIX}/pr-1.json" 8

  merge_job
  not_merged
  [[ "${output}" == *"還有 1 條驗收條件沒有勾起來"* ]]
}

@test "同一個 head 上 issue 換到前面還有沒關的 milestone 時不合併" {
  fixture_milestones "v0.9.0:open" "v0.10.0:open"
  fixture_in_milestone 7 2 v0.10.0

  merge_job
  not_merged
  [[ "${output}" == *"v0.9.0"* ]]
}

@test "重跑後較早的 milestone 被重開：識別裡那個 milestone 的狀態變成 open，也不合併" {
  fixture_milestones "v0.9.0:open" "v0.10.0:open"
  fixture_in_milestone 7 2 v0.10.0

  merge_job
  not_merged
  [[ "${output}" == *"id=901 number=1 title=v0.9.0 state=open"* ]]
}

@test "合併前標題剛改成不合規、新的一輪還查不到時，重判 single-task 而不合併" {
  fixture_set "${FIX}/pr-1.json" 'd["title"] = "feat(x): 一則變更（#7、#8）"'

  merge_job
  not_merged
  [[ "${output}" == *"重判沒過："*"single-task"* ]]
}

@test "被引用的 issue 拿掉 F<N> 之後，PR 與 head 都沒變，重判 single-task 而不合併" {
  fixture_set "${FIX}/pr-1.json" 'd["title"] = "feat(x): 一則變更（F36、#7）"'

  merge_job
  not_merged
  [[ "${output}" == *"F36"* ]]
}

@test "第一個 PR 已合併並關掉 issue，第二個 PR 也綁它時不合併" {
  fixture_set "${FIX}/issue-7.json" 'd["state"] = "CLOSED"'

  merge_job
  not_merged
  [[ "${output}" == *"已關閉"* ]]
}

@test "第一個 PR 已合併、issue 沒被關，第二個 PR 綁它時不合併（main 上的合併紀錄寫了它）" {
  git switch -q main
  git commit -q --allow-empty -m "Merge pull request #5" -m "合併紀錄：issue #7；head $(printf 'a%.0s' {1..40})；run 1"
  BASE="$(git rev-parse HEAD)"
  git switch -q feat/x
  git rebase -q main
  HEAD_SHA="$(git rev-parse HEAD)"
  fixture_set "${FIX}/pr-1.json" "d['headRefOid'] = '${HEAD_SHA}'"

  merge_job
  not_merged
  [[ "${output}" == *"已由"* ]]
}

@test "issue 由維護者網頁合併的 PR 完成後又被重開，第二個 PR 綁它仍不合併" {
  fixture_set "${FIX}/issue-7.json" "d['comments'] = [{'author': {'login': 'ycpss91255'}, 'body': '由 PR #5 合併完成（網頁合併）'}]"

  merge_job
  not_merged
  [[ "${output}" == *"PR #5"* ]]
}

@test "帳本重判：勾選行記的主旨在歷史裡找不到時不合併" {
  fixture_issue 7 "feat(x): 一則變更" "- [x] 第一條 — feat(x): 這筆 commit 不存在"

  merge_job
  not_merged
}

@test "TDD red 的重判讀不到 attempt 的 jobs（403）時不合併並指名" {
  fixture_red_run 100 jobs_fail="HTTP 403: Resource not accessible by integration"

  merge_job
  not_merged
  [[ "${output}" == *"讀不到第 1 次 attempt 的 jobs"* ]]
}

@test "PR 落後 main（mergeStateStatus 是 BEHIND）時不嘗試合併，寫出 rebase 的下一步" {
  fixture_set "${FIX}/pr-1.json" 'd["mergeStateStatus"] = "BEHIND"'

  merge_job
  not_merged
  [[ "${output}" == *"rebase 到最新的 main"* ]]
}

@test "兩個都過了預檢、另一個先合進去而自己的合併請求被拒：重讀後落後，寫出 rebase 的下一步" {
  printf '1\n' >"${FIX}/merge.exit"
  printf 'GraphQL: Head branch is out of date\n' >"${FIX}/merge.stderr"
  cp "${FIX}/pr-1.json" "${FIX}/pr-1.json.next"
  fixture_set "${FIX}/pr-1.json.next" 'd["mergeStateStatus"] = "BEHIND"'

  merge_job
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"rebase 到最新的 main"* ]]
  [ "$(gh_calls issue close)" -eq 0 ]
}

@test "合併請求因為其他原因被拒時照原訊息失敗，不關 issue" {
  printf '1\n' >"${FIX}/merge.exit"
  printf 'GraphQL: A conversation must be resolved before this pull request can be merged.\n' >"${FIX}/merge.stderr"

  merge_job
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"A conversation must be resolved"* ]]
  [ "$(gh_calls issue close)" -eq 0 ]
}

# ── 重跑時 PR 已合併：補作業 ───────────────────────────────────────────────

@test "已由這一輪 bot 合併、issue 還開著：不再合併，關 issue 並留言一次" {
  fixture_merged app/github-actions 555

  merge_job
  [ "${status}" -eq 0 ]
  [ "$(gh_calls pr merge)" -eq 0 ]
  [ "$(gh_calls issue close 7)" -eq 1 ]
  [ "$(gh_calls issue comment 7)" -eq 1 ]
}

@test "已合併、issue 已關、沒有那則留言（關 issue 之後、留言之前中斷）：只補留言一次" {
  fixture_merged app/github-actions 555
  fixture_set "${FIX}/issue-7.json" 'd["state"] = "CLOSED"'

  merge_job
  [ "${status}" -eq 0 ]
  [ "$(gh_calls issue close)" -eq 0 ]
  [ "$(gh_calls issue comment 7)" -eq 1 ]
}

@test "已合併、issue 已關、已有留言：什麼都不做，以 0 結束" {
  fixture_merged app/github-actions 555
  fixture_set "${FIX}/issue-7.json" 'd["state"] = "CLOSED"'
  bot_comment 7

  merge_job
  [ "${status}" -eq 0 ]
  [ "$(gh_calls issue close)" -eq 0 ]
  [ "$(gh_calls issue comment)" -eq 0 ]
}

@test "原本那一輪在補作業途中中斷後重跑：已經留過的不再留，最後只有一則完成留言" {
  fixture_merged app/github-actions 555
  bot_comment 7

  merge_job
  [ "$(gh_calls issue close 7)" -eq 1 ]
  [ "$(gh_calls issue comment)" -eq 0 ]
}

@test "已合併之後描述被改綁：補關的仍是合併紀錄裡的那一張" {
  fixture_merged app/github-actions 555
  compliant_issue 8
  rebind "${FIX}/pr-1.json" 8

  merge_job
  [ "$(gh_calls issue close 7)" -eq 1 ]
  [ "$(gh_calls issue close 8)" -eq 0 ]
}

@test "已合併、但不是 bot 的合併（維護者在網頁上合併）：失敗並寫明 issue 由合併的人處理" {
  fixture_merged ycpss91255 555

  merge_job
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"不是這一輪 bot 的合併"* ]]
  [ "$(gh_calls issue close)" -eq 0 ]
}

@test "已由 bot 合併、但紀錄的 run id 是別一輪：同一個 head 的別一輪不補作業" {
  fixture_merged app/github-actions 554

  merge_job
  [ "${status}" -eq 1 ]
  [[ "${output}" == *"不是這一輪 bot 的合併"* ]]
}

@test "已由 bot 合併、但合併 commit 的第二個 parent 不是這一輪的 head：不補作業" {
  fixture_merged app/github-actions 555 "$(printf 'e%.0s' {1..40})"

  merge_job
  [ "${status}" -eq 1 ]
}

# ── 快照：同一個物件只讀一次 ───────────────────────────────────────────────

@test "快照讓重判的每一道檢查共用同一次讀到的 PR：假 gh 只被問過一次 PR #1" {
  merge_job

  [ "$(gh_calls pr view 1 --json)" -eq 1 ]
}

@test "快照拒絕會寫入的呼叫" {
  "${REPO_ROOT}/script/gh_snapshot.sh" init "${WORK}/snap"

  run "${WORK}/snap/bin/gh" issue close 7

  [ "${status}" -ne 0 ]
  [ "$(gh_calls issue close)" -eq 0 ]
}

# ── ci.yaml 的接線 ─────────────────────────────────────────────────────────

# ci.yaml 裡一個 job 的區塊（到下一個 job 為止）。
job_block() {
  python3 - "${REPO_ROOT}/.github/workflows/ci.yaml" "$1" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"^  " + re.escape(sys.argv[2]) + r":\n((?:(?:    .*)?\n)+)", text, re.M)
print(match.group(1) if match else "")
PY
}

@test "ci.yaml 的 merge job 排在 ci-rollup 之後，if 寫明 !cancelled() 與 ci-rollup 是 success" {
  block="$(job_block merge)"

  [[ "${block}" == *"needs: [ci-rollup]"* ]]
  [[ "${block}" == *"!cancelled() && needs.ci-rollup.result == 'success'"* ]]
}

@test "ci.yaml 的 merge job 只在 pull_request、base 是 main、同 repo、不是草稿時跑" {
  block="$(job_block merge)"

  [[ "${block}" == *"github.event_name == 'pull_request'"* ]]
  [[ "${block}" == *"github.event.pull_request.base.ref == 'main'"* ]]
  [[ "${block}" == *"github.event.pull_request.head.repo.full_name == github.repository"* ]]
  [[ "${block}" == *"!github.event.pull_request.draft"* ]]
}

@test "ci.yaml 的 merge job 宣告 contents、pull-requests、issues 的 write 與 actions: read" {
  block="$(job_block merge)"

  [[ "${block}" == *"contents: write"*"pull-requests: write"*"issues: write"*"actions: read"* ]]
}

@test "ci.yaml 的 merge job 以 merge-<PR 編號> 排隊、不取消進行中的那一個" {
  block="$(job_block merge)"

  [[ "${block}" == *"group: merge-\${{ github.event.pull_request.number }}"* ]]
  [[ "${block}" == *"cancel-in-progress: false"* ]]
}

@test "ci.yaml 的 pull_request 寫明 types，含 ready_for_review 與 edited" {
  grep -qxF -- '    types: [opened, synchronize, reopened, ready_for_review, edited]' "${REPO_ROOT}/.github/workflows/ci.yaml"
}

@test "ci.yaml 同一個 PR 只留最新的一輪：workflow 層的 concurrency 以 PR 編號分組並取消舊的" {
  grep -qF -- "github.event.pull_request.number" <(sed -n '/^concurrency:/,/^[a-z]/p' "${REPO_ROOT}/.github/workflows/ci.yaml")
  grep -qF -- "cancel-in-progress: \${{ github.event_name == 'pull_request' }}" "${REPO_ROOT}/.github/workflows/ci.yaml"
}

@test "ci.yaml 的 acceptance 在 pull_request 時也跑：沒有只限 push 到 main 的 if" {
  block="$(job_block acceptance)"

  [[ -n "${block}" ]]
  [[ "${block}" != *"if:"* ]]
}

@test "ci.yaml 的 merge-gate job 多一項 actions: read" {
  block="$(job_block merge-gate)"

  [[ "${block}" == *"actions: read"* ]]
}
