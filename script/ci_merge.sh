#!/usr/bin/env bash
#
# merge job 的腳本（#407 調整需求第 1 點）：檢查全過，由 CI 以 merge commit 合併，只關判定出來的那一張
# issue。照 ycpss91255-docker/sim_asset 的 merge job（維護者 11/20 定案 1、3）；CI 的 merge job 排在
# ci-rollup 之後、只在同 repo、base 是 main、不是草稿的 pull_request 上執行它。
#
# ## 合併前在同一份現況資料上重判（a0、a）
#
# --match-head-commit 只鎖 head sha：改描述、換綁 issue、轉回草稿、issue 換 milestone，都可以發生在同一個
# sha 上，edited 與 concurrency 只減少舊的一輪。所以合併前：
#   a0  這一輪必須是這個 PR 最新的一輪 pull_request 執行，而且 head 仍是這一輪事件的 head
#   a   讀 PR 或 issue 資料的檢查（REJUDGE）在 script/gh_snapshot.sh 的同一份資料上重跑一次，不沿用前面
#       job 的綠燈。不讀 PR、issue 的檢查（lint、規格、build……）沿用這一輪 ci-rollup 的結果。
# 重判通過，用的 issue 編號也來自那一份資料；紀錄裡印出這份資料的識別，網頁合併前拿它和現況比對。
#
# ## 合併（b、c、d）
#
#   b  落後 main（mergeStateStatus 是 BEHIND）時不嘗試合併；真正擋的是 ruleset 的 strict
#   c  主旨固定是「Merge pull request #<n>」，內文是一行紀錄（RECORD：issue、head、run id）；兩者有任何
#      關閉參照（script/closing-references.txt）就不送出。被拒時重讀 PR：落後就寫 rebase 的下一步
#   d  只關判定出來的那一張，還開著才關，再留「由 PR #<n> 合併完成。」（已經有就不再留）
#
# ## 重跑要能補做、不重複（e）
#
# PR 已經合併時不再判、不再合併：先核對這是**這一輪** bot 完成的合併（合併者是 app/github-actions、合併
# commit 的第二個 parent 是這一輪的 head、紀錄裡的 run id 是這一輪），issue 照紀錄、不照現在的描述，再照
# issue 現況做 d。核對不符就失敗：不是這一輪 bot 的合併，issue 由合併的人處理。
#
# 擋不住的：重判之後、送出合併之前的時間差裡改描述或轉回草稿；合併之後、關 issue 之前被取消（要重跑
# 原本那一輪的 merge job 補作業）；一個 PR 改掉自己的守門程式讓自己通過（信任有 push 權限的人）。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/ci_merge.sh <PR 編號> <事件的 head sha> <base ref>

  <PR 編號>        這一輪 pull_request 事件的 PR
  <事件的 head sha> 這一輪事件的 head（github.event.pull_request.head.sha）
  <base ref>       重判用的 base，例如 origin/main

讀這幾個環境變數：
  GITHUB_REPOSITORY    <owner>/<repo>
  GITHUB_RUN_ID        這一輪的 run id（合併紀錄與「最新一輪」的核對）
  CM_CI_ROLLUP_RESULT  這一輪 ci-rollup 的結論（needs.ci-rollup.result），不是 success 就不合併

  exit 0  合併了並關好 issue，或 PR 已由這一輪合併、補作業做完
  exit 1  沒有合併（逐條指名原因與下一步），或補作業失敗
  exit 2  用法不對
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac
  if [[ $# -ne 3 ]]; then
    usage >&2
    return 2
  fi
  local name
  for name in GITHUB_REPOSITORY GITHUB_RUN_ID CM_CI_ROLLUP_RESULT; do
    if [[ -z "${!name:-}" ]]; then
      printf 'ci_merge: %s 沒有設，沒有合併\n' "${name}" >&2
      printf 'ci_merge: 下一步：在 ci.yaml 的 merge job 裡執行，或在規格裡自己設 %s\n' "${name}" >&2
      return 2
    fi
  done
  local here real snapshot code=0
  here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  real="$(command -v gh || true)"
  if [[ -z "${real}" ]]; then
    printf 'ci_merge: PATH 上找不到 gh，沒有合併\n' >&2
    printf 'ci_merge: 下一步：在有 gh 的 runner 上執行\n' >&2
    return 2
  fi
  snapshot="$(mktemp -d)"
  "${here}/gh_snapshot.sh" init "${snapshot}"
  CM_SCRIPT_DIR="${here}" CM_SNAPSHOT="${snapshot}" CM_GH_WRITE="${real}" \
    python3 - "$1" "$2" "$3" <<'PY' || code=$?
import hashlib
import json
import os
import re
import subprocess
import sys

number, event_head, base = sys.argv[1:4]
repo = os.environ["GITHUB_REPOSITORY"]
run_id = os.environ["GITHUB_RUN_ID"]
rollup = os.environ["CM_CI_ROLLUP_RESULT"]
here = os.environ["CM_SCRIPT_DIR"]
snapshot = os.environ["CM_SNAPSHOT"]
real_gh = os.environ["CM_GH_WRITE"]
READ_ENV = dict(os.environ, PATH=os.path.join(snapshot, "bin") + os.pathsep + os.environ["PATH"])
TAG = "ci_merge"
BOTS = {"app/github-actions", "github-actions", "github-actions[bot]"}
SUBJECT = f"Merge pull request #{number}"
RECORD = "合併紀錄：issue #{issue}；head {head}；run {run}"
RECORD_PATTERN = re.compile(r"^合併紀錄：issue #([0-9]+)；head ([0-9a-f]{40})；run ([0-9]+)$", re.MULTILINE)
DONE = f"由 PR #{number} 合併完成。"
# 讀 PR 或 issue 資料的檢查，照 #407 調整需求第 1 點 a 的明確清單。
REJUDGE = [
    ("綁定判定", ["pr_binding.sh", number, base]),
    ("checkpoints", ["lint_checkpoints.sh", number, base]),
    ("single-task", ["lint_single_task.sh", "pr", number, base]),
    ("test-interfaces", ["lint_test_interfaces.sh", "agreed", base]),
    ("merge-gate", ["lint_merge_gate.sh", number, base]),
    ("attribution", ["lint_attribution.sh", "pr", number, base]),
]


def out(line):
    print(f"{TAG}: {line}", flush=True)


def stop(lines, next_step):
    for line in lines:
        print(f"{TAG}: {line}", file=sys.stderr)
    print(f"{TAG}: 下一步：{next_step}", file=sys.stderr)
    sys.exit(1)


def call(command, env=None):
    completed = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
    return completed.returncode, completed.stdout, completed.stderr


def read_json(*arguments):
    """經快照讀：同一個物件只讀一次。"""
    code, stdout, stderr = call(["gh", *arguments], env=READ_ENV)
    if code != 0:
        return None, stderr.strip() or f"gh 以 {code} 結束"
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as error:
        return None, f"gh 回的不是 JSON：{error}"


def fresh_json(*arguments):
    """不經快照、讀現況：合併被拒之後與關 issue 之前用。"""
    code, stdout, stderr = call([real_gh, *arguments])
    if code != 0:
        return None, stderr.strip() or f"gh 以 {code} 結束"
    try:
        return json.loads(stdout), None
    except json.JSONDecodeError as error:
        return None, f"gh 回的不是 JSON：{error}"


def write(*arguments):
    code, stdout, stderr = call([real_gh, *arguments])
    sys.stdout.write(stdout)
    return code, stderr.strip()


def sha256(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def finish_issue(issue):
    """d：還開著就關，再留一次完成留言；已經有就不再留。照 issue 現況做，重跑也不重複。"""
    data, error = fresh_json("issue", "view", str(issue), "--repo", repo, "--json", "state,comments")
    if data is None:
        stop([f"已合併，但讀不到 #{issue} 的現況：{error}"],
             f"重跑這一輪的 merge job（gh run rerun {run_id} --job <merge job 的 id>）補作業")
    if data.get("state") == "OPEN":
        code, error = write("issue", "close", str(issue), "--repo", repo, "--reason", "completed")
        if code != 0:
            stop([f"已合併，但關不掉 #{issue}：{error}"],
                 f"重跑這一輪的 merge job（gh run rerun {run_id} --job <merge job 的 id>）補作業")
        out(f"已關閉 #{issue}")
    else:
        out(f"#{issue} 已經是關閉的，不再關")
    commented = any(((c.get("author") or {}).get("login") in BOTS) and (c.get("body") or "").strip().startswith(DONE.rstrip("。"))
                    for c in data.get("comments") or [])
    if commented:
        out(f"#{issue} 已經有「{DONE}」的留言，不再留")
        return
    code, error = write("issue", "comment", str(issue), "--repo", repo, "--body", DONE)
    if code != 0:
        stop([f"已合併並關閉 #{issue}，但留言失敗：{error}"],
             f"重跑這一輪的 merge job（gh run rerun {run_id} --job <merge job 的 id>）補留言")
    out(f"已在 #{issue} 留言「{DONE}」")


out(f"PR #{number}，事件的 head {event_head}，這一輪 run {run_id}，ci-rollup {rollup}")
pull, error = read_json("pr", "view", number, "--json",
                        "number,title,body,state,isDraft,baseRefName,headRefName,headRefOid,isCrossRepository,"
                        "mergeStateStatus,updatedAt,mergedBy,mergeCommit")
if pull is None:
    stop([f"讀不到 PR #{number}：{error}——讀不到就不合併"], "看上面的錯誤，修好後重跑這一輪")

# ── e：PR 已經合併（重跑）────────────────────────────────────────────────
if pull.get("state") == "MERGED":
    merger = (pull.get("mergedBy") or {}).get("login", "")
    oid = (pull.get("mergeCommit") or {}).get("oid", "")
    commit, error = read_json("api", f"repos/{repo}/commits/{oid}") if oid else (None, "沒有合併 commit")
    records = RECORD_PATTERN.findall(((commit or {}).get("commit") or {}).get("message") or "")
    parents = [p.get("sha") for p in (commit or {}).get("parents") or []]
    mine = [r for r in records if r[2] == run_id]
    if merger not in BOTS or commit is None or len(parents) < 2 or parents[1] != event_head or not mine:
        stop([f"PR #{number} 已經合併，但不是這一輪 bot 的合併（合併者 {merger or '不明'}、合併 commit {oid[:7] or '不明'}、"
              f"第二個 parent {parents[1][:7] if len(parents) > 1 else '沒有'}、紀錄的 run {[r[2] for r in records] or '沒有'}）"
              + (f"；讀合併 commit 時：{error}" if commit is None else "")],
             "不是這一輪 bot 的合併，issue 由合併的人處理（網頁合併照 AGENTS.md 留言並關閉）")
    issue = int(mine[0][0])
    out(f"PR #{number} 已由這一輪合併（{oid[:7]}），照合併紀錄補作業：#{issue}")
    finish_issue(issue)
    sys.exit(0)

# ── 不合併的情況 ────────────────────────────────────────────────────────────
blocked = []
if rollup != "success":
    blocked.append(f"這一輪 ci-rollup 的結論是 {rollup}，不是 success")
if pull.get("isDraft"):
    blocked.append("PR 是草稿（全部驗完才 gh pr ready）")
if pull.get("baseRefName") != "main":
    blocked.append(f"PR 開到 {pull.get('baseRefName')!r}，不是 main")
if pull.get("isCrossRepository"):
    blocked.append("PR 來自 fork：merge job 只合同 repo 的分支，fork 的 PR 由維護者照 AGENTS.md 在網頁上合併")
if pull.get("state") != "OPEN":
    blocked.append(f"PR 的狀態是 {pull.get('state')}，不是開著的")
if blocked:
    stop([f"PR #{number} 不合併：{reason}" for reason in blocked], "照上面每一條處理；條件都成立後，新的一輪會再判")
if pull.get("headRefOid") != event_head:
    stop([f"PR #{number} 現在的 head 是 {pull.get('headRefOid')}，不是這一輪事件的 head {event_head}（之後又有推送）"],
         "看最新那一輪；這一輪不合併")

# ── a0：這一輪是這個 PR 最新的一輪 ──────────────────────────────────────────
branch = pull.get("headRefName") or ""
runs, error = read_json("api", "--paginate", "--slurp",
                        f"repos/{repo}/actions/workflows/ci.yaml/runs?event=pull_request&branch={branch}&per_page=100")
if runs is None:
    stop([f"讀不到這個 PR 的執行清單：{error}——讀不到就不合併"], "看上面的錯誤，修好後重跑這一輪")
mine = [r for page in runs for r in page.get("workflow_runs") or []
        if int(number) in [p.get("number") for p in r.get("pull_requests") or []]]
latest = max(mine, key=lambda r: r.get("run_number") or 0, default=None)
if latest is None or str(latest.get("id")) != run_id:
    stop([f"這一輪（run {run_id}）不是 PR #{number} 最新的一輪 pull_request 執行"
          f"（最新的是 run {latest.get('id') if latest else '查不到'}）：標題或描述改過、或又有推送"],
         "看最新那一輪；這一輪不合併")

# ── a：在同一份現況資料上重判 ───────────────────────────────────────────────
failed, bound = [], None
for label, command in REJUDGE:
    out(f"── 重判：{label}（{' '.join(command)}）")
    code, stdout, stderr = call([os.path.join(here, command[0]), *command[1:]], env=READ_ENV)
    sys.stdout.write(stdout)
    sys.stdout.write(stderr)
    sys.stdout.flush()
    if code != 0:
        failed.append(label)
    elif label == "綁定判定":
        bound = int(stdout.strip().splitlines()[-1].rsplit("#", 1)[1])

# 這份資料的識別：網頁合併前拿它和現況比對（AGENTS.md 的網頁合併程序）。milestone 由 merge-gate 印。
out(f"識別 PR #{number} updatedAt={pull.get('updatedAt')} title_sha256={sha256(pull.get('title'))} "
    f"body_sha256={sha256(pull.get('body'))} head={pull.get('headRefOid')}")
objects = os.path.join(snapshot, "objects")
for name in sorted(os.listdir(objects)):
    if not name.startswith("issue-"):
        continue
    with open(os.path.join(objects, name), encoding="utf-8") as handle:
        record = json.load(handle)
    if record["code"] == 0:
        data = json.loads(record["stdout"])
        out(f"識別 issue #{data.get('number')} updatedAt={data.get('updatedAt')}")
    else:
        out(f"識別 issue {name[6:-5]} 讀不到")

if failed or bound is None:
    stop([f"重判沒過：{'、'.join(failed)}（各自的原因在上面它自己印的那幾行）"],
         "照上面那幾道檢查自己的下一步改；這一輪不合併")

# ── b、c：合併 ─────────────────────────────────────────────────────────────
if pull.get("mergeStateStatus") == "BEHIND":
    stop([f"PR #{number} 落後 main（mergeStateStatus 是 BEHIND）"], "rebase 到最新的 main 再推送；推送會重跑 CI")

record = RECORD.format(issue=bound, head=event_head, run=run_id)
with open(os.path.join(here, "closing-references.txt"), encoding="utf-8") as handle:
    pattern = re.compile(next(line.rstrip("\n") for line in handle if line.strip() and not line.lstrip().startswith("#")),
                         re.IGNORECASE)
leaked = [m.group(0) for text in (SUBJECT, record) for m in pattern.finditer(text)]
if leaked:
    stop([f"合併訊息有關閉參照（{'、'.join(leaked)}），不送出：commit 訊息的關閉關鍵字也會關 issue"],
         "修 script/ci_merge.sh 的合併訊息")

out(f"合併 PR #{number}：{SUBJECT}／{record}")
code, error = write("pr", "merge", number, "--repo", repo, "--merge", "--match-head-commit", event_head,
                    "--subject", SUBJECT, "--body", record)
if code != 0:
    now, _ = fresh_json("pr", "view", number, "--repo", repo, "--json", "mergeStateStatus,state")
    if (now or {}).get("mergeStateStatus") == "BEHIND":
        stop([f"合併請求被拒，重讀後 PR #{number} 落後 main（另一個 PR 先合進去了）：{error}"],
             "rebase 到最新的 main 再推送；推送會重跑 CI")
    stop([f"合併請求被拒：{error}"], "看上面 GitHub 回的原因；只有合併操作本身失敗、檢查都過時，照 AGENTS.md 的網頁合併程序")
out(f"已合併 PR #{number}")
finish_issue(bound)
PY
  rm -rf "${snapshot}"
  return "${code}"
}

main "$@"
