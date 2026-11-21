#!/usr/bin/env bats
#
# script/lint_repo_rules.sh —— GitHub 上實際的保護設定，和 AGENTS.md 的「repo 保護設定」表一不一致
# （T19、#407 調整需求第 9 點）。
#
# 被觀察的是命令列：一份表（${WORK}/AGENTS.md，以參數給）、GITHUB_REPOSITORY 與 PATH 上的假 gh
# 進去，結束碼與它印出來的「表上寫的」「實際讀到的」出來。規格不連網。
#
# 假 gh 只認 `gh api <path>`：回 ${WORK}/api/<path>.json；同一個 path 有 .fail 檔時，把那個檔的內容
# 印到 fd 2 並以 1 結束（模擬 403、404）；兩個都沒有也以 1 結束。替身回應照 2026-11-20 從
# ycpss91255-docker/base 讀到的形狀：pull_request 帶表上沒列的預設參數，deletion、non_fast_forward
# 沒有 parameters，rules/branches/main 的每一條帶 ruleset_id。
#
# setup 寫一份「一致」的基準；每條失敗的規格只改一處，看那一項被指名。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_repo_rules.sh"
  WORK="$(cd -- "$(mktemp -d)" && pwd -P)"
  R="repos/owner/repo"
  mkdir -p "${WORK}/bin"
  cat >"${WORK}/bin/gh" <<STUB
#!/usr/bin/env bash
if [[ "\$1" != api || \$# -ne 2 ]]; then
  echo "假 gh 只認 gh api <path>，收到：\$*" >&2
  exit 64
fi
base="${WORK}/api/\$2"
if [[ -f "\${base}.fail" ]]; then
  cat "\${base}.fail" >&2
  exit 1
fi
if [[ -f "\${base}.json" ]]; then
  cat "\${base}.json"
  exit 0
fi
echo "gh: Not Found (HTTP 404)" >&2
exit 1
STUB
  chmod +x "${WORK}/bin/gh"
  table
  baseline
}

teardown() {
  rm -rf "${WORK}"
}

# 表：AGENTS.md 那一節，前後各有別的標題。
table() {
  cat >"${WORK}/AGENTS.md" <<'MD'
# AGENTS.md

## 合併規則

### repo 保護設定

`repo-rules` job 每一輪讀實際設定，和這張表逐項比對。

| 對象 | 項目 | 設定值 |
|---|---|---|
| repo | `allow_merge_commit` | `true` |
| ruleset main | `target` | `"branch"` |
| ruleset main | `enforcement` | `"active"` |
| ruleset main | `conditions.ref_name.include` | `["~DEFAULT_BRANCH"]` |
| ruleset main | `conditions.ref_name.exclude` | `[]` |
| ruleset main | `rules` | `["deletion", "non_fast_forward", "pull_request", "required_status_checks"]` |
| ruleset main | `pull_request.required_approving_review_count` | `0` |
| ruleset main | `pull_request.required_review_thread_resolution` | `true` |
| ruleset main | `pull_request.allowed_merge_methods` | `["merge"]` |
| ruleset main | `required_status_checks.required_status_checks` | `[{"context": "ci-rollup", "integration_id": 15368}]` |
| ruleset main | `required_status_checks.strict_required_status_checks_policy` | `true` |
| ruleset main | `required_status_checks.do_not_enforce_on_create` | `false` |
| ruleset Require CI for release tags | `target` | `"tag"` |
| ruleset Require CI for release tags | `enforcement` | `"active"` |
| ruleset Require CI for release tags | `conditions.ref_name.include` | `["refs/tags/v*"]` |
| ruleset Require CI for release tags | `conditions.ref_name.exclude` | `[]` |
| ruleset Require CI for release tags | `rules` | `["deletion", "non_fast_forward", "required_status_checks"]` |
| ruleset Require CI for release tags | `required_status_checks.required_status_checks` | `[{"context": "ci-rollup", "integration_id": 15368}]` |
| ruleset Require CI for release tags | `required_status_checks.do_not_enforce_on_create` | `false` |

核對不了：bypass 名單。

## 下一節
MD
}

# 基準：和表一致的替身回應。
baseline() {
  rm -rf "${WORK}/api"
  mkdir -p "${WORK}/api/${R}/rulesets" "${WORK}/api/${R}/rules/branches"
  cat >"${WORK}/api/${R}.json" <<'JSON'
{"id": 1, "full_name": "owner/repo", "allow_merge_commit": true, "allow_squash_merge": true, "allow_rebase_merge": true}
JSON
  cat >"${WORK}/api/${R}/rulesets.json" <<'JSON'
[
  {"id": 101, "name": "main", "target": "branch", "source_type": "Repository", "source": "owner/repo", "enforcement": "active"},
  {"id": 102, "name": "Require CI for release tags", "target": "tag", "source_type": "Repository", "source": "owner/repo", "enforcement": "active"}
]
JSON
  cat >"${WORK}/api/${R}/rulesets/101.json" <<'JSON'
{
  "id": 101, "name": "main", "target": "branch", "source_type": "Repository", "source": "owner/repo",
  "enforcement": "active",
  "conditions": {"ref_name": {"exclude": [], "include": ["~DEFAULT_BRANCH"]}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": false, "required_reviewers": [],
      "require_code_owner_review": false, "dismissal_restriction": {"enabled": false, "allowed_actors": []},
      "require_last_push_approval": false, "required_review_thread_resolution": true,
      "require_extra_approval_for_unattributed_changes": true, "allowed_merge_methods": ["merge"]}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": true, "do_not_enforce_on_create": false,
      "required_status_checks": [{"context": "ci-rollup", "integration_id": 15368}]}}
  ],
  "current_user_can_bypass": "never"
}
JSON
  cat >"${WORK}/api/${R}/rulesets/102.json" <<'JSON'
{
  "id": 102, "name": "Require CI for release tags", "target": "tag", "source_type": "Repository", "source": "owner/repo",
  "enforcement": "active",
  "conditions": {"ref_name": {"exclude": [], "include": ["refs/tags/v*"]}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false, "do_not_enforce_on_create": false,
      "required_status_checks": [{"context": "ci-rollup", "integration_id": 15368}]}}
  ],
  "current_user_can_bypass": "never"
}
JSON
  cat >"${WORK}/api/${R}/rules/branches/main.json" <<'JSON'
[
  {"type": "deletion", "ruleset_source_type": "Repository", "ruleset_source": "owner/repo", "ruleset_id": 101},
  {"type": "non_fast_forward", "ruleset_source_type": "Repository", "ruleset_source": "owner/repo", "ruleset_id": 101},
  {"type": "required_status_checks", "parameters": {
    "strict_required_status_checks_policy": true, "do_not_enforce_on_create": false,
    "required_status_checks": [{"context": "ci-rollup", "integration_id": 15368}]},
   "ruleset_source_type": "Repository", "ruleset_source": "owner/repo", "ruleset_id": 101},
  {"type": "pull_request", "parameters": {
    "required_approving_review_count": 0, "dismiss_stale_reviews_on_push": false, "required_reviewers": [],
    "require_code_owner_review": false, "dismissal_restriction": {"enabled": false, "allowed_actors": []},
    "require_last_push_approval": false, "required_review_thread_resolution": true,
    "require_extra_approval_for_unattributed_changes": true, "allowed_merge_methods": ["merge"]},
   "ruleset_source_type": "Repository", "ruleset_source": "owner/repo", "ruleset_id": 101}
]
JSON
}

# api_edit <path> <python 敘述>：把那個替身回應讀成 d，執行敘述，再寫回去。
# rule(規則清單, 種類) 是那一條規則，params(規則清單, 種類) 是它的 parameters，named(列表, 名稱) 是那一筆 ruleset。
api_edit() {
  python3 - "${WORK}/api/$1.json" "$2" <<'PY'
import json
import sys

path, statement = sys.argv[1], sys.argv[2]


def rule(rules, kind):
    return next(item for item in rules if item["type"] == kind)


def params(rules, kind):
    return rule(rules, kind)["parameters"]


def named(listing, name):
    return next(item for item in listing if item["name"] == name)


with open(path, encoding="utf-8") as handle:
    d = json.load(handle)
exec(statement)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(d, handle, ensure_ascii=False)
PY
}

# api_fail <path> <gh 的錯誤訊息>：這個 path 改成把訊息印到 fd 2、以 1 結束。
api_fail() {
  printf '%s\n' "$2" >"${WORK}/api/$1.fail"
}

lint() {
  run env GITHUB_REPOSITORY=owner/repo PATH="${WORK}/bin:${PATH}" "${LINT}" "${WORK}/AGENTS.md"
}

# ── 一致 ──────────────────────────────────────────────────────────────────

@test "實際設定和表一致時以 0 結束" {
  lint

  [ "${status}" -eq 0 ]
}

@test "一致的項目也逐項印出表上寫的與實際讀到的" {
  lint

  [ "${status}" -eq 0 ]
  [[ "${output}" == *'target：表上寫的 "branch"，實際讀到的 "branch"'* ]]
  [[ "${output}" == *'allow_merge_commit：表上寫的 true，實際讀到的 true'* ]]
}

# ── 表上有的規則實際沒有；實際有、表上沒寫的規則 ──────────────────────────

@test "strict 被關掉時失敗，列出表上寫的 true 與實際讀到的 false" {
  api_edit "${R}/rulesets/101" 'params(d["rules"], "required_status_checks")["strict_required_status_checks_policy"] = False'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"ruleset main 的 required_status_checks.strict_required_status_checks_policy：表上寫的 true，實際讀到的 false"* ]]
}

@test "main 實際生效的規則裡討論串那一項被取消時失敗" {
  api_edit "${R}/rules/branches/main" 'params(d, "pull_request")["required_review_thread_resolution"] = False'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"rules/branches/main 的 pull_request.required_review_thread_resolution（ruleset_id 101）：表上寫的 true，實際讀到的 false"* ]]
}

@test "main 實際生效的規則多出表上沒寫的 merge_queue 時失敗，指名多出的那一條" {
  api_edit "${R}/rules/branches/main" 'd.append({"type": "merge_queue", "parameters": {"merge_method": "MERGE", "grouping_strategy": "ALLGREEN"}, "ruleset_source_type": "Repository", "ruleset_source": "owner/repo", "ruleset_id": 103})'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"rules/branches/main 的 rules："*"實際有、表上沒有：merge_queue"* ]]
}

@test "ruleset 的詳情少一條規則時失敗，指名少的那一條" {
  api_edit "${R}/rulesets/101" 'd["rules"] = [item for item in d["rules"] if item["type"] != "non_fast_forward"]'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"ruleset main 的 rules："*"表上有、實際沒有：non_fast_forward"* ]]
}

@test "不一致時最後的訊息指名有幾項，下一步寫改回設定或在同一個 PR 改表" {
  api_edit "${R}/rulesets/101" 'params(d["rules"], "pull_request")["required_review_thread_resolution"] = False'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"1 項不一致、0 項讀不到"* ]]
  [[ "${output}" == *"下一步：改回設定，或在同一個 PR 改表"* ]]
}

# ── 允許的合併方式 ────────────────────────────────────────────────────────

@test "允許的合併方式不只 merge 時失敗" {
  api_edit "${R}/rulesets/101" 'params(d["rules"], "pull_request")["allowed_merge_methods"] = ["merge", "squash", "rebase"]'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *'ruleset main 的 pull_request.allowed_merge_methods：表上寫的 ["merge"]，實際讀到的 ["merge", "squash", "rebase"]'* ]]
}

# ── tag ruleset ───────────────────────────────────────────────────────────

@test "tag ruleset 不存在時失敗，指名找不到的名稱" {
  api_edit "${R}/rulesets" 'd[:] = [item for item in d if item["name"] != "Require CI for release tags"]'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"ruleset Require CI for release tags 的 target："*"找不到名稱是「Require CI for release tags」的 ruleset"* ]]
}

@test "tag ruleset 不是 active 時失敗" {
  api_edit "${R}/rulesets/102" 'd["enforcement"] = "disabled"'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *'ruleset Require CI for release tags 的 enforcement：表上寫的 "active"，實際讀到的 "disabled"'* ]]
}

@test "tag ruleset 的 do_not_enforce_on_create 是 true 時失敗" {
  api_edit "${R}/rulesets/102" 'params(d["rules"], "required_status_checks")["do_not_enforce_on_create"] = True'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"ruleset Require CI for release tags 的 required_status_checks.do_not_enforce_on_create：表上寫的 false，實際讀到的 true"* ]]
}

@test "檢查來源不是 integration_id 15368 時失敗" {
  api_edit "${R}/rulesets/102" 'params(d["rules"], "required_status_checks")["required_status_checks"] = [{"context": "ci-rollup", "integration_id": 12345}]'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *'ruleset Require CI for release tags 的 required_status_checks.required_status_checks：表上寫的 [{"context": "ci-rollup", "integration_id": 15368}]，實際讀到的 [{"context": "ci-rollup", "integration_id": 12345}]'* ]]
}

# ── 目標、狀態與名稱 ──────────────────────────────────────────────────────

@test "target 不符時失敗" {
  api_edit "${R}/rulesets/101" 'd["target"] = "tag"'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *'ruleset main 的 target：表上寫的 "branch"，實際讀到的 "tag"'* ]]
}

@test "目標的 include 不符時失敗" {
  api_edit "${R}/rulesets/102" 'd["conditions"]["ref_name"]["include"] = ["refs/tags/*"]'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *'ruleset Require CI for release tags 的 conditions.ref_name.include：表上寫的 ["refs/tags/v*"]，實際讀到的 ["refs/tags/*"]'* ]]
}

@test "目標的 exclude 不符時失敗" {
  api_edit "${R}/rulesets/101" 'd["conditions"]["ref_name"]["exclude"] = ["refs/heads/release"]'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *'ruleset main 的 conditions.ref_name.exclude：表上寫的 []，實際讀到的 ["refs/heads/release"]'* ]]
}

@test "ruleset 的名稱和表不符時失敗：照名稱在列表裡找不到" {
  api_edit "${R}/rulesets" 'named(d, "main")["name"] = "main protection"'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"ruleset main 的 target："*"找不到名稱是「main」的 ruleset"* ]]
}

@test "同名的 ruleset 有兩個時失敗，分不出是哪一個" {
  api_edit "${R}/rulesets" 'd.append(dict(named(d, "main"), id=103))'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"名稱是「main」的有 2 個（id 101、103）"* ]]
}

# ── 必驗項目讀不到 ────────────────────────────────────────────────────────

@test "ruleset 的詳情回 403 時失敗，逐項指名讀不到，不移出比對" {
  api_fail "${R}/rulesets/101" 'gh: Resource not accessible by integration (HTTP 403)'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *'ruleset main 的 target：表上寫的 "branch"，實際讀不到（gh api repos/owner/repo/rulesets/101 以 1 結束：gh: Resource not accessible by integration (HTTP 403)）'* ]]
  [[ "${output}" == *"ruleset main 的 required_status_checks.strict_required_status_checks_policy：表上寫的 true，實際讀不到"* ]]
}

@test "rules/branches/main 回 404 時失敗，不移出比對" {
  api_fail "${R}/rules/branches/main" 'gh: Not Found (HTTP 404)'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"rules/branches/main 的 rules：表上寫的"*"實際讀不到（gh api repos/owner/repo/rules/branches/main 以 1 結束：gh: Not Found (HTTP 404)）"* ]]
}

@test "必驗的參數是 null 時失敗，指名那一項" {
  api_edit "${R}/rulesets/101" 'params(d["rules"], "pull_request")["required_approving_review_count"] = None'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"ruleset main 的 pull_request.required_approving_review_count：表上寫的 0，實際讀不到（pull_request.required_approving_review_count 是 null）"* ]]
}

@test "必驗的參數不在回應裡時失敗，指名那一項" {
  api_edit "${R}/rulesets/102" 'del params(d["rules"], "required_status_checks")["do_not_enforce_on_create"]'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"ruleset Require CI for release tags 的 required_status_checks.do_not_enforce_on_create：表上寫的 false，實際讀不到"* ]]
}

# ── 不比的部分 ────────────────────────────────────────────────────────────

@test "deletion、non_fast_forward 沒有 parameters 或 parameters 是 null 都算一致" {
  # 基準的 deletion、non_fast_forward 沒有 parameters 這個鍵。
  lint
  [ "${status}" -eq 0 ]

  api_edit "${R}/rulesets/101" 'rule(d["rules"], "deletion")["parameters"] = None; rule(d["rules"], "non_fast_forward")["parameters"] = None'
  api_edit "${R}/rulesets/102" 'rule(d["rules"], "deletion")["parameters"] = None; rule(d["rules"], "non_fast_forward")["parameters"] = None'
  api_edit "${R}/rules/branches/main" 'rule(d, "deletion")["parameters"] = None; rule(d, "non_fast_forward")["parameters"] = None'

  lint

  [ "${status}" -eq 0 ]
}

@test "表上沒列的參數欄位不比，但印出來" {
  api_edit "${R}/rulesets/101" 'params(d["rules"], "pull_request")["require_last_push_approval"] = True'

  lint

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"pull_request.require_last_push_approval：表上沒列，實際讀到的 true"* ]]
}

# ── bypass_actors ─────────────────────────────────────────────────────────

@test "回應沒有 bypass_actors 時每個 ruleset 都印核對不了，以 0 結束而結論不把它算成通過" {
  lint

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"核對不了：main 的 bypass_actors"* ]]
  [[ "${output}" == *"核對不了：Require CI for release tags 的 bypass_actors"* ]]
  [[ "${lines[${#lines[@]}-1]}" == *"bypass_actors 核對不了"* ]]
}

@test "回應有 bypass_actors 時照樣印核對不了並印出內容，結束碼不受影響" {
  api_edit "${R}/rulesets/101" 'd["bypass_actors"] = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]'

  lint

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"核對不了：main 的 bypass_actors"*'"actor_id": 5'* ]]
}

# ── allow_merge_commit ────────────────────────────────────────────────────

@test "allow_merge_commit 是 false 時失敗" {
  api_edit "${R}" 'd["allow_merge_commit"] = False'

  lint

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"repo 的 allow_merge_commit：表上寫的 true，實際讀到的 false"* ]]
}

@test "讀不到 allow_merge_commit 時不失敗，印出未自動核對那一行（403、null、欄位不在）" {
  local change
  for change in forbidden null missing; do
    baseline
    case "${change}" in
      forbidden) api_fail "${R}" 'gh: Resource not accessible by integration (HTTP 403)' ;;
      null) api_edit "${R}" 'd["allow_merge_commit"] = None' ;;
      missing) api_edit "${R}" 'del d["allow_merge_commit"]' ;;
    esac

    lint

    [ "${status}" -eq 0 ]
    [[ "${output}" == *"未自動核對：allow_merge_commit，以設定頁人工核對為準"* ]]
  done
}

# ── 用法 ──────────────────────────────────────────────────────────────────

@test "沒有設 GITHUB_REPOSITORY 時以 2 結束，下一步指向它" {
  run env -u GITHUB_REPOSITORY PATH="${WORK}/bin:${PATH}" "${LINT}" "${WORK}/AGENTS.md"

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"GITHUB_REPOSITORY"*"下一步："* ]]
}
