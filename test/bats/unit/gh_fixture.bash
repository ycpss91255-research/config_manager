# 合併規則那幾支守門腳本（#407）共用的假 gh。以 `load gh_fixture` 讀進來。
#
# 被測的腳本以 subprocess 呼叫 gh、自己解析 JSON（測試映像沒有 jq，這裡也不模擬 --jq）。這支假 gh
# 從 ${FIX} 底下的檔案回答，規格只要把替身資料寫成檔：
#
#   gh pr view <N> …       → ${FIX}/pr-<N>.json
#   gh issue view <N> …    → ${FIX}/issue-<N>.json
#   gh api <路徑> …        → ${FIX}/api/<路徑，/ ? & = 換成 _>；帶 --slurp 時包成 [ <那一頁> ]
#
# 檔不在：回 gh 的「Could not resolve」並以 1 結束。旁邊有 <檔>.fail：把它的內容印到 fd 2、以 1 結束
# （模擬 403、404）。旁邊有 <檔>.next：第一次回完之後換成它——用來觀察「判定途中資料被改」。
# 會寫入的呼叫（pr merge、issue close、issue comment）不改任何檔，只記下來；${FIX}/<動作>.exit
# 存在時以它的內容結束（例如合併被拒）。每一次呼叫都逐行記在 ${FIX}/calls.log，參數以 tab 隔開。

# 設好 ${FIX}、${STUB}，把假 gh 放上 PATH。
gh_fixture_setup() {
  FIX="${WORK}/fix"
  STUB="${WORK}/bin"
  mkdir -p "${FIX}/api" "${STUB}"
  : >"${FIX}/calls.log"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'FIX=%q\n' "${FIX}"
    cat <<'STUB'
( IFS=$'\t'; printf '%s\n' "$*" ) >>"${FIX}/calls.log"
serve() {
  local file="$1" wrap="$2"
  if [[ -f "${file}.fail" ]]; then cat "${file}.fail" >&2; exit 1; fi
  if [[ ! -f "${file}" ]]; then
    printf 'GraphQL: Could not resolve to an issue or pull request (%s)\n' "${file##*/}" >&2
    exit 1
  fi
  if [[ "${wrap}" == 1 ]]; then printf '['; cat "${file}"; printf ']\n'; else cat "${file}"; fi
  if [[ -f "${file}.next" ]]; then mv "${file}.next" "${file}"; fi
  exit 0
}
written() {
  local code=0
  [[ -f "${FIX}/$1.exit" ]] && code="$(cat "${FIX}/$1.exit")"
  [[ -f "${FIX}/$1.stderr" ]] && cat "${FIX}/$1.stderr" >&2
  exit "${code}"
}
case "$1 $2" in
  "pr view") serve "${FIX}/pr-$3.json" 0 ;;
  "issue view") serve "${FIX}/issue-$3.json" 0 ;;
  "pr merge") written merge ;;
  "issue close") written close ;;
  "issue comment") written comment ;;
esac
if [[ "$1" == api ]]; then
  shift
  path="" slurp=0
  for argument in "$@"; do
    case "${argument}" in
      --slurp) slurp=1 ;;
      -*) ;;
      *) [[ -z "${path}" ]] && path="${argument}" ;;
    esac
  done
  serve "${FIX}/api/$(printf '%s' "${path}" | tr '/?&=' '____')" "${slurp}"
fi
printf '假 gh 不認得這個呼叫：%s\n' "$*" >&2
exit 64
STUB
  } >"${STUB}/gh"
  chmod +x "${STUB}/gh"
  PATH="${STUB}:${PATH}"
  export PATH FIX
}

# 字串轉成 JSON 字串字面。
json_str() {
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1], ensure_ascii=False))' "$1"
}

# api_file <路徑>：gh api <路徑> 會讀的那個檔名。
api_file() {
  printf '%s/api/%s' "${FIX}" "$(printf '%s' "$1" | tr '/?&=' '____')"
}

# fixture_pr <N> <標題> <描述> [關閉參照的編號…]：寫一份 PR，其餘欄位用合規的預設值；
# 之後要改哪個欄位，用 fixture_set 改。
fixture_pr() {
  local number="$1" title="$2" body="$3"
  shift 3
  python3 - "${FIX}/pr-${number}.json" "${number}" "${title}" "${body}" "${GITHUB_REPOSITORY}" "$@" <<'PY'
import json, sys
path, number, title, body, repo, *closing = sys.argv[1:]
owner, name = repo.split("/")
data = {
    "number": int(number), "title": title, "body": body, "baseRefName": "main", "isDraft": False,
    "isCrossRepository": False, "state": "OPEN", "headRefName": "feat/x", "headRefOid": "0" * 40,
    "mergeStateStatus": "CLEAN", "updatedAt": "2026-11-21T00:00:00Z", "mergedAt": None, "mergedBy": None,
    "mergeCommit": None, "url": f"https://github.com/{repo}/pull/{number}",
    "closingIssuesReferences": [
        {"number": int(n), "repository": {"name": name, "owner": {"login": owner}},
         "url": f"https://github.com/{repo}/issues/{n}"} for n in closing],
}
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# fixture_issue <N> <標題> <內文> [milestone 標題]：一張開著的 issue。
fixture_issue() {
  python3 - "${FIX}/issue-$1.json" "$1" "$2" "$3" "${4:-}" "${GITHUB_REPOSITORY}" <<'PY'
import json, sys
path, number, title, body, milestone, repo = sys.argv[1:]
data = {
    "number": int(number), "title": title, "body": body, "state": "OPEN", "stateReason": None,
    "url": f"https://github.com/{repo}/issues/{number}", "updatedAt": "2026-11-21T00:00:00Z",
    "createdAt": "2026-01-01T00:00:00Z", "comments": [],
    "milestone": ({"number": 1, "title": milestone, "description": "", "dueOn": None} if milestone else None),
}
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# fixture_set <檔> <python 運算式>：以 d 為那份 JSON 改一個欄位，例如
#   fixture_set "${FIX}/pr-1.json" 'd["isDraft"] = True'
fixture_set() {
  python3 - "$1" "$2" <<'PY'
import json, sys
path, statement = sys.argv[1:]
d = json.load(open(path, encoding="utf-8"))
exec(statement)
json.dump(d, open(path, "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# gh_calls <開頭>：calls.log 裡以這些參數開頭的呼叫有幾次（參數以空白給，比對時換成 tab）。
gh_calls() {
  local prefix
  prefix="$(printf '%s' "$*" | tr ' ' '\t')"
  awk -v p="${prefix}" 'index($0, p) == 1 { n++ } END { print n + 0 }' "${FIX}/calls.log"
}

# ── TDD red 與 milestone 的替身（merge-gate 與 merge job 的規格共用）──────────

# fixture_red_run <run id> [鍵=值…]：一輪替身執行。預設是一輪合格的 red：ci.yaml 的 pull_request 執行，
# pull_requests 有 PR #1，一次 attempt 是 failure，bats 失敗在 specs，那一步的紀錄裡有 not ok。
#   event=… path=… prs=<逗號分隔的 PR 編號，可空> attempts=<每一次的結論，逗號分隔>
#   job=<失敗的 job 名稱> failing=<失敗的那一步> steps=<這個 job 的步驟，逗號分隔>
#   marker=<specs 那一步紀錄裡的一行，可空> jobs_fail=<讀 jobs 時回的錯誤>
fixture_red_run() {
  python3 - "${FIX}" "$@" <<'PY'
import json, sys
fix, run_id, *pairs = sys.argv[1:]
o = dict(event="pull_request", path=".github/workflows/ci.yaml", prs="1", attempts="failure", job="bats",
         failing="specs", steps="prepare,specs", marker="not ok 3 某一條新規格", jobs_fail="")
o.update(pair.split("=", 1) for pair in pairs)
def put(path, data, raw=False):
    key = path.translate(str.maketrans("/?&=", "____"))
    with open(f"{fix}/api/{key}", "w", encoding="utf-8") as handle:
        handle.write(data if raw else json.dumps(data, ensure_ascii=False))
base = f"repos/acme/widgets/actions/runs/{run_id}"
conclusions = o["attempts"].split(",")
put(base, {"id": int(run_id), "path": o["path"], "event": o["event"], "run_attempt": len(conclusions),
           "pull_requests": [{"number": int(n)} for n in o["prs"].split(",") if n]})
for attempt, conclusion in enumerate(conclusions, 1):
    put(f"{base}/attempts/{attempt}", {"id": int(run_id), "run_attempt": attempt, "conclusion": conclusion})
    job_id = int(run_id) * 10 + attempt
    steps = []
    for index, name in enumerate(o["steps"].split(",")):
        failed = conclusion == "failure" and name == o["failing"]
        steps.append({"name": name, "number": index + 1, "conclusion": "failure" if failed else "success",
                      "started_at": f"2026-11-21T00:0{index}:00Z", "completed_at": f"2026-11-21T00:0{index}:59Z"})
    jobs = [{"id": job_id, "name": o["job"], "conclusion": conclusion, "steps": steps},
            {"id": job_id + 5000, "name": "ci-rollup", "conclusion": conclusion, "steps": []}]
    put(f"{base}/attempts/{attempt}/jobs?per_page=100", {"total_count": len(jobs), "jobs": jobs})
    if o["jobs_fail"]:
        with open(f"{fix}/api/" + f"{base}/attempts/{attempt}/jobs?per_page=100".translate(str.maketrans("/?&=", "____")) + ".fail", "w") as handle:
            handle.write(o["jobs_fail"] + "\n")
    specs_index = o["steps"].split(",").index("specs") if "specs" in o["steps"].split(",") else 0
    log = [f"2026-11-21T00:0{specs_index}:10.0000000Z ##[group]Run ./script/test.sh --part bats"]
    if o["marker"]:
        log.append(f"2026-11-21T00:0{specs_index}:30.1234567Z {o['marker']}")
    log.append(f"2026-11-21T00:0{specs_index}:40.0000000Z ##[error]Process completed with exit code 1.")
    put(f"repos/acme/widgets/actions/jobs/{job_id}/logs", "\n".join(log) + "\n", raw=True)
PY
}

# fixture_milestones <標題:狀態>…：repo 的 milestone 清單，編號照給的順序從 1 起、id 是 900＋編號。
fixture_milestones() {
  python3 - "$(api_file 'repos/acme/widgets/milestones?state=all&per_page=100')" "$@" <<'PY'
import json, sys
path, *items = sys.argv[1:]
data = [{"id": 900 + i, "number": i, "title": t, "state": s}
        for i, (t, s) in enumerate((item.rsplit(":", 1) for item in items), 1)]
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# fixture_in_milestone <issue> <milestone 編號> <標題>：把 issue 放進那個 milestone。
fixture_in_milestone() {
  fixture_set "${FIX}/issue-$1.json" "d['milestone'] = {'number': $2, 'title': '$3', 'description': '', 'dueOn': None}"
}

# fixture_files <路徑>…：PR #1 改了哪些檔。
fixture_files() {
  python3 - "$(api_file 'repos/acme/widgets/pulls/1/files?per_page=100')" "$@" <<'PY'
import json, sys
path, *files = sys.argv[1:]
json.dump([{"filename": f} for f in files], open(path, "w", encoding="utf-8"))
PY
}
