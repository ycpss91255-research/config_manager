#!/usr/bin/env bats
#
# script/lint_attribution.sh 與登記的 commit-msg 檢查 —— commit 訊息、PR 標題與描述裡的 Claude 署名與
# session 連結（T19、#405）。
#
# pr 模式是 CI 的 attribution job：PR 範圍內每個 commit 的訊息、PR 標題與描述；commit-msg 模式由
# script/githook-checks/commit-msg/20-no-attribution.sh 呼叫。失敗時只印位置與命中的樣式，不印命中的內容。
# 三個呼叫端（另一個是 Claude Code 的 hook）都讀 script/attribution-patterns.txt。
#
# 測的是公開行為：一份真的 git repo、一個訊息檔，或 PATH 上的假 gh 回的 PR 進去，看結束碼與它指名了什麼。
# ci.yaml 的接線照 workflow 裡實際寫的 needs 與三組規則呼叫 script/ci_rollup.sh，不在這裡另抄一份清單。
# 署名的字樣在執行時才組出來，這份檔案裡沒有從行首開始的署名行，也沒有完整的 session 網址。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_attribution.sh"
  CHECK="${REPO_ROOT}/script/githook-checks/commit-msg/20-no-attribution.sh"
  WORKFLOW="${REPO_ROOT}/.github/workflows/ci.yaml"
  WORK="$(cd -- "$(mktemp -d)" && pwd -P)"
  STUB="${WORK}/bin"
  mkdir -p "${STUB}"
  export GIT_AUTHOR_NAME=spec GIT_AUTHOR_EMAIL=spec@example.com
  export GIT_COMMITTER_NAME=spec GIT_COMMITTER_EMAIL=spec@example.com
  NL=$'\n'
  COAUTHOR="Co-Authored-By"": Claude Opus 5 <noreply@anthropic.com>"
  GENERATED="🤖 Generated with [Claude Code]""(https://claude.com/claude-code)"
  SESSION_URL="https://claude.ai/code/session_""01SpecFakeId23"
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

json_str() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"
}

# 假 gh：`gh pr view` 回 ${STUB}/pr.json；其他一律失敗。
stub_gh() {
  printf '{"title": %s, "body": %s}' "$(json_str "$1")" "$(json_str "$2")" >"${STUB}/pr.json"
  cat >"${STUB}/gh" <<STUB
#!/usr/bin/env bash
case "\$1 \$2" in
  "pr view") cat "${STUB}/pr.json" ;;
  *) exit 1 ;;
esac
STUB
  chmod +x "${STUB}/gh"
}

# 一份 PR 的 repo：起點之後，一筆筆以參數給的完整訊息提交。
pr_repo() {
  REPO="${WORK}/repo"
  git init -q -b main "${REPO}"
  git -C "${REPO}" commit -q --allow-empty -m "chore(base): 起點"
  BASE="$(git -C "${REPO}" rev-parse HEAD)"
  local message
  for message in "$@"; do
    printf '%s\n' "${message}" >"${WORK}/message"
    git -C "${REPO}" commit -q --allow-empty --cleanup=verbatim -F "${WORK}/message"
  done
  cd "${REPO}" || return 1
}

pr() {
  run env PATH="${STUB}:${PATH}" "${LINT}" pr 7 "${BASE}"
}

# ── pr 模式（CI 的 attribution job）──────────────────────────────────────

@test "pr：PR 範圍內有一個 commit 的訊息命中時失敗，指名那個 commit 與行號" {
  pr_repo "fix(web): 乾淨的一筆" "fix(web): 修好${NL}${NL}${COAUTHOR}"
  stub_gh "fix(web): 修好" "乾淨的描述"

  pr

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"$(git rev-parse HEAD)"*"第 3 行"* ]]
}

@test "pr：PR 標題命中時失敗，指名標題" {
  pr_repo "fix(web): 乾淨的一筆"
  stub_gh "${GENERATED}" "乾淨的描述"

  pr

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"標題"* ]]
}

@test "pr：PR 描述命中時失敗，指名描述與行號" {
  pr_repo "fix(web): 乾淨的一筆"
  stub_gh "fix(web): 乾淨的一筆" "## 摘要${NL}${COAUTHOR}"

  pr

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"描述"*"第 2 行"* ]]
}

@test "pr：commit、標題、描述都沒有命中時通過" {
  pr_repo "fix(web): 乾淨的一筆" "fix(web): 說明 Co-Authored-By 那一行要刪${NL}${NL}連結寫成 session_<ID>"
  stub_gh "fix(web): 乾淨的一筆" "說明：Generated with [Claude Code] 那一行要刪"

  pr

  [ "${status}" -eq 0 ]
}

@test "pr：失敗時的輸出只有位置與樣式，沒有命中的共同作者行與 session 網址" {
  pr_repo "fix(web): 修好${NL}${NL}${COAUTHOR}"
  stub_gh "fix(web): 修好" "舊的連結 ${SESSION_URL} 要拿掉"

  pr

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"命中樣式"* && "${output}" != *"noreply@anthropic.com"* && "${output}" != *"${SESSION_URL}"* ]]
}

@test "pr：印出讀到的識別：PR 標題與描述各自的 sha256（#407）" {
  pr_repo "fix(web): 乾淨的一筆"
  stub_gh "fix(web): 乾淨的一筆" "乾淨的描述"
  title_sha="$(printf '%s' "fix(web): 乾淨的一筆" | sha256sum | cut -d' ' -f1)"
  body_sha="$(printf '%s' "乾淨的描述" | sha256sum | cut -d' ' -f1)"

  pr

  [[ "${output}" == *"title_sha256=${title_sha}"*"body_sha256=${body_sha}"* ]]
}

@test "pr：讀不到 PR 時失敗，不放行" {
  pr_repo "fix(web): 乾淨的一筆"
  printf '#!/usr/bin/env bash\necho "GraphQL: Could not resolve to a PullRequest" >&2\nexit 1\n' >"${STUB}/gh"
  chmod +x "${STUB}/gh"

  pr

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"讀不到 PR"* ]]
}

# ── 登記的 commit-msg 檢查 ────────────────────────────────────────────────

@test "commit-msg 檢查：訊息有共同作者行時非零結束" {
  printf 'fix(web): 修好\n\n%s\n' "${COAUTHOR}" >"${WORK}/msg"

  run "${CHECK}" "${WORK}/msg"

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"命中樣式"* ]]
}

@test "commit-msg 檢查：訊息有 Claude-Session 那一行時非零結束" {
  printf 'fix(web): 修好\n\n%s: %s\n' "Claude-Session" "${SESSION_URL}" >"${WORK}/msg"

  run "${CHECK}" "${WORK}/msg"

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"命中樣式"* ]]
}

@test "commit-msg 檢查：訊息有 Generated with 那一行時非零結束" {
  printf 'fix(web): 修好\n\n%s\n' "${GENERATED}" >"${WORK}/msg"

  run "${CHECK}" "${WORK}/msg"

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"命中樣式"* ]]
}

@test "commit-msg 檢查：訊息在行中引用完整的 session 網址時非零結束" {
  printf 'fix(web): 修好\n\n舊的連結 %s 要拿掉\n' "${SESSION_URL}" >"${WORK}/msg"

  run "${CHECK}" "${WORK}/msg"

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"命中樣式"* ]]
}

@test "commit-msg 檢查：乾淨的訊息以 0 結束" {
  printf 'fix(web): 修好\n\n說明 Co-Authored-By 那一行要刪，連結寫成 session_<ID>\n' >"${WORK}/msg"

  run "${CHECK}" "${WORK}/msg"

  [ "${status}" -eq 0 ]
}

# ── 三個呼叫端讀同一份樣式清單 ────────────────────────────────────────────
#
# 複製一份樹，把 script/attribution-patterns.txt 換成只有一條原本沒有的樣式：三個呼叫端都照它判定，
# 讀的就是那一份檔，不是各自抄的一份。

copy_tree() {
  COPY="${WORK}/copy"
  mkdir -p "${COPY}/script/claude-hooks" "${COPY}/script/githook-checks/commit-msg"
  cp "${LINT}" "${COPY}/script/lint_attribution.sh"
  cp "${REPO_ROOT}/script/claude-hooks/no-attribution.sh" "${COPY}/script/claude-hooks/"
  cp "${CHECK}" "${COPY}/script/githook-checks/commit-msg/"
  printf '^Spec-Marker:\n' >"${COPY}/script/attribution-patterns.txt"
}

@test "Claude Code 的 hook 讀 script/attribution-patterns.txt：換上的樣式命中就擋下" {
  copy_tree
  python3 -c '
import json, sys
print(json.dumps({"tool_name": "Bash", "tool_input": {"command": sys.argv[1]}, "cwd": sys.argv[2]}))' \
    "git commit -m \"fix(web): 修好${NL}${NL}Spec-Marker: 1\"" "${WORK}" >"${WORK}/input.json"

  run "${COPY}/script/claude-hooks/no-attribution.sh" <"${WORK}/input.json"

  [ "${status}" -eq 2 ]
}

@test "commit-msg 檢查讀 script/attribution-patterns.txt：換上的樣式命中就非零結束" {
  copy_tree
  printf 'fix(web): 修好\n\nSpec-Marker: 1\n' >"${WORK}/msg"

  run "${COPY}/script/githook-checks/commit-msg/20-no-attribution.sh" "${WORK}/msg"

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"^Spec-Marker:"* ]]
}

@test "pr 模式讀 script/attribution-patterns.txt：換上的樣式命中就失敗" {
  copy_tree
  pr_repo "fix(web): 修好${NL}${NL}Spec-Marker: 1"
  stub_gh "fix(web): 修好" "乾淨的描述"

  run env PATH="${STUB}:${PATH}" "${COPY}/script/lint_attribution.sh" pr 7 "${BASE}"

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"^Spec-Marker:"* ]]
}

# ── ci.yaml 的接線 ────────────────────────────────────────────────────────

@test "ci.yaml 的 attribution job 只在 pull_request 跑 lint_attribution.sh pr" {
  run python3 - "${WORKFLOW}" <<'PY'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"^  attribution:\n((?:    .*\n|\s*\n)+)", text, re.M)
block = match.group(1) if match else ""
if_line = re.search(r"^    if: (.+)$", block, re.M)
print(if_line.group(1).strip() if if_line else "no-if")
print("runs-lint" if re.search(r"\./script/lint_attribution\.sh pr ", block) else "no-run")
PY

  [ "${status}" -eq 0 ]
  [ "${output}" = "github.event_name == 'pull_request'${NL}runs-lint" ]
}

# 照 ci.yaml 的 ci-rollup 那一步呼叫 ci_rollup.sh：needs 與 --always／--pull-request／--main-push 三組
# 都從 workflow 讀出來。參數是事件、ref 與要給 skipped 的 job（其餘都是 success）；@pull-request、
# @main-push 代表那一組的全部 job。指名的 job 不在 needs 裡時直接失敗，不當成沒事。
rollup_as_wired() {
  local event="$1" ref="$2"
  shift 2
  python3 - "${WORKFLOW}" "$@" >"${WORK}/rollup" <<'PY'
import json, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"^  ci-rollup:\n((?:    .*\n|\s*\n)+)", text, re.M)
if not match:
    sys.exit("ci.yaml 裡找不到 ci-rollup")
block = match.group(1)
needs_block = re.search(r"^    needs:\n((?:      - .*\n)+)", block, re.M)
needs = re.findall(r"^      - (\S+)", needs_block.group(1), re.M) if needs_block else []
groups = {}
for name in ("always", "pull-request", "main-push"):
    found = re.search(r"--" + name + r' "([^"]*)"', block)
    groups[name] = found.group(1) if found else ""
skipped = set()
for arg in sys.argv[2:]:
    if arg.startswith("@"):
        skipped.update(groups[arg[1:]].split())
    elif arg not in needs:
        sys.exit(f"{arg} 不在 ci-rollup 的 needs 裡")
    else:
        skipped.add(arg)
print(json.dumps({job: {"result": "skipped" if job in skipped else "success", "outputs": {}} for job in needs}))
for name in ("always", "pull-request", "main-push"):
    print(groups[name])
PY
  local needs always pull_request main_push
  {
    IFS= read -r needs
    IFS= read -r always
    IFS= read -r pull_request
    IFS= read -r main_push
  } <"${WORK}/rollup"
  run env -i PATH="${PATH}" GITHUB_JOB=ci-rollup GITHUB_EVENT_NAME="${event}" GITHUB_REF="${ref}" NEEDS="${needs}" \
    "${REPO_ROOT}/script/ci_rollup.sh" "${WORKFLOW}" \
    --always "${always}" --pull-request "${pull_request}" --main-push "${main_push}"
}

@test "ci-rollup：pull_request 時 attribution 被跳過就不放行，指名 attribution" {
  rollup_as_wired pull_request refs/pull/7/merge @main-push attribution

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"attribution"* ]]
}

@test "ci-rollup：pull_request 時 attribution 與其餘該跑的都過了就放行" {
  rollup_as_wired pull_request refs/pull/7/merge @main-push

  [ "${status}" -eq 0 ]
}

@test "ci-rollup：merge_group 時 attribution 與其餘只在 pull request 跑的被跳過，放行" {
  rollup_as_wired merge_group refs/heads/gh-readonly-queue/main/pr-7-abc @pull-request @main-push attribution

  [ "${status}" -eq 0 ]
}

@test "ci-rollup：push 到 main 時 attribution 與其餘只在 pull request 跑的被跳過，放行" {
  rollup_as_wired push refs/heads/main @pull-request attribution

  [ "${status}" -eq 0 ]
}
