#!/usr/bin/env bash
#
# 合併前的兩道檢查（#407 調整需求第 6 點）：這個 PR 有沒有自己的 TDD red，以及前面的 milestone 關了沒。
# 兩者都要讀 PR 描述與 issue，repo 的 ruleset 讀不到，所以由這支在 CI 擋（merge-gate job，列在 ci-rollup
# 的 --pull-request 組）。merge job 合併前在同一份現況資料上再跑一次（script/ci_merge.sh）。
#
# ## TDD red（2026-11-20 的規則）
#
# PR 描述的 `## TDD red` 段落要有本 repo 的 Actions 執行連結，而那一輪的任一次 attempt：
#   - 結論是 failure（重跑過的執行，整輪的結論會變成最後一次的結果，所以逐次讀 attempt 與 jobs）
#   - 是這個 PR 在 .github/workflows/ci.yaml 的 pull_request 執行：run 的 pull_requests 有這個 PR 的編號
#     （不比 commit sha：rebase 會改寫它；不用分支名稱：同一個分支先後開的兩個 PR 分不出來）
#   - 可以認定 red 的 job（RED_JOBS）失敗在它名為 specs 的步驟，而那一步的紀錄裡有規格層級的失敗
#     （bats 的 not ok、pytest 的 FAILED）。只壞在流程檢查、只壞在建映像（prepare、build）的不算。
# 或寫「不適用：<理由>」——只在 PR 改的每一個檔都在 doc/ 底下或副檔名是 .md 時放行（定案 5）。
# 段落有幾個連結，至少一個合格就過；每個連結的判定都印出來。
#
# ## milestone 順序（定案 6）
#
# 綁定的 issue（共用綁定判定 script/pr_binding.sh 回傳的那一張）所在的 milestone，只要還有版本號更小、
# 沒關閉的 milestone，就擋下。版本號照 vX.Y.Z 逐段比數字；issue 沒有 milestone 時不檢查這一項。
# 有 milestone 的標題不是這個格式（綁定的那一個，或某個還開著的）時失敗並指名，不默默略過。
#
# 每一次都印出讀到的識別：綁定判定的那一組、milestone 順序用到的每一個 milestone（綁定的那一個與所有
# 版本號比它小的）的 ID、標題與開關狀態、head sha。
#
# 擋不住的：失敗的是不是這個 PR 新增的那條規格、原因是不是預期的（靠 review）；純文件豁免照路徑與
# 副檔名判定，.md 的 symlink 或可執行檔也算純文件。
#
# 跑在 runner 上、不在測試映像裡，與 lint_checkpoints.sh 同一個理由：它要 gh 與 token（另要 actions: read）。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/lint_merge_gate.sh <PR 編號> <base ref>

  <PR 編號>   要檢查的 pull request
  <base ref>  交給共用綁定判定（script/pr_binding.sh）的 base

讀這個環境變數：
  GITHUB_REPOSITORY  <owner>/<repo>

  fail  PR 描述沒有 `## TDD red` 段落，或段落裡沒有合格的執行連結，也沒有合格的「不適用：<理由>」
  fail  綁定 issue 的 milestone 前面還有沒關閉的 milestone；有 milestone 的標題不是 vX.Y.Z
  fail  綁定判定不成立，或讀不到要讀的 PR、issue、milestone、執行紀錄

  exit 0  兩道都過；exit 1  有一道沒過；exit 2  用法不對
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac
  if [[ $# -ne 2 ]]; then
    usage >&2
    return 2
  fi
  if [[ -z "${GITHUB_REPOSITORY:-}" ]]; then
    printf 'lint_merge_gate: GITHUB_REPOSITORY 沒有設，分不出哪些執行連結屬於本 repo\n' >&2
    printf 'lint_merge_gate: 下一步：在 GitHub Actions 裡執行，或設 GITHUB_REPOSITORY=<owner>/<repo>\n' >&2
    return 2
  fi
  CM_PR_BINDING="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/pr_binding.sh" \
    python3 - "$1" "$2" <<'PY'
import json
import os
import re
import subprocess
import sys

number, base = sys.argv[1:3]
repo = os.environ["GITHUB_REPOSITORY"]
TAG = "lint_merge_gate"
WORKFLOW = ".github/workflows/ci.yaml"
# 可以認定 red 的 job 與它的規格層級失敗標記。build 的 docker build --target runtime-test 把建映像與
# 映像內的 bats 綁在同一個指令裡，拆不開，不列入；acceptance、compose-smoke、backup-drill 之後拆得開、
# 紀錄有規格層級的失敗標記，再加進來。步驟名稱 specs 是 ci.yaml 的介面。
BATS = re.compile(r"^not ok [0-9]+")
PYTEST = re.compile(r"^FAILED |::\S+ FAILED\b")
RED_JOBS = {"unit-integration": PYTEST, "api": PYTEST, "bats": BATS, "web-e2e": PYTEST}
SPEC_STEP = "specs"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
VERSION = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
problems = []


def out(line):
    print(f"{TAG}: {line}", flush=True)


def run(*arguments):
    completed = subprocess.run(list(arguments), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None, completed.stderr.strip() or f"{arguments[0]} 以 {completed.returncode} 結束、沒有給出原因"
    return completed.stdout, None


def gh_json(*arguments):
    raw, error = run("gh", *arguments)
    if raw is None:
        return None, error
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as error:
        return None, f"gh 回的不是 JSON：{error}"


def pages(path, key=None):
    """gh api --paginate --slurp 回的是每一頁一個元素的陣列；攤平成一份清單。"""
    data, error = gh_json("api", "--paginate", "--slurp", path)
    if data is None:
        return None, error
    items = []
    for page in data:
        items.extend((page.get(key) or []) if key else page)
    return items, None


# ── 綁定判定 ────────────────────────────────────────────────────────────────
binding = subprocess.run([os.environ["CM_PR_BINDING"], number, base], capture_output=True, text=True, check=False)
sys.stdout.write(binding.stdout)
issue_number = None
if binding.returncode != 0:
    for line in binding.stderr.splitlines():
        problems.append(f"綁定判定不成立：{line.removeprefix('pr_binding: ')}")
else:
    issue_number = int(binding.stdout.strip().splitlines()[-1].rsplit("#", 1)[1])

pull, error = gh_json("pr", "view", number, "--json", "body,headRefOid,updatedAt")
if pull is None:
    problems.append(f"讀不到 PR #{number}：{error}——讀不到就不放行")
    body = ""
else:
    body = pull.get("body") or ""
    out(f"識別 head {pull.get('headRefOid')}")


# ── TDD red ─────────────────────────────────────────────────────────────────
def section(text):
    lines, inside, found = [], False, False
    for line in text.splitlines():
        if re.match(r"^##[ \t]+TDD red[ \t]*$", line, re.IGNORECASE):
            inside, found = True, True
            continue
        if inside and re.match(r"^#{1,2}[ \t]", line):
            break
        if inside:
            lines.append(line)
    return ("\n".join(lines) if found else None)


def in_window(stamp, step):
    started, completed = (step.get("started_at") or "")[:19], (step.get("completed_at") or "")[:19]
    return bool(started and completed) and started <= stamp[:19] <= completed


def judge_run(run_id):
    """(合格的那一次 attempt 與 job, 不算的原因)。"""
    reasons = []
    data, error = gh_json("api", f"repos/{repo}/actions/runs/{run_id}")
    if data is None:
        return None, [f"讀不到執行紀錄：{error}"]
    if data.get("path") != WORKFLOW:
        reasons.append(f"不是 {WORKFLOW} 的執行（path 是 {data.get('path')!r}）")
    if data.get("event") != "pull_request":
        reasons.append(f"不是 pull_request 事件（是 {data.get('event')!r}，例如功能分支上手動觸發的）")
    linked = [item.get("number") for item in data.get("pull_requests") or []]
    if int(number) not in linked:
        reasons.append(f"執行的 pull_requests 沒有 PR #{number}（是 {linked or '空的'}）")
    if reasons:
        return None, reasons
    for attempt in range(1, int(data.get("run_attempt") or 1) + 1):
        detail, error = gh_json("api", f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}")
        if detail is None:
            reasons.append(f"讀不到第 {attempt} 次 attempt：{error}")
            continue
        if detail.get("conclusion") != "failure":
            reasons.append(f"第 {attempt} 次 attempt 的結論是 {detail.get('conclusion')!r}，不是 failure")
            continue
        jobs, error = pages(f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", "jobs")
        if jobs is None:
            reasons.append(f"讀不到第 {attempt} 次 attempt 的 jobs：{error}")
            continue
        failed = [job.get("name") for job in jobs if job.get("conclusion") == "failure"]
        candidates = []
        for job in jobs:
            name = re.sub(r" \(.*\)$", "", job.get("name") or "")
            if name not in RED_JOBS or job.get("conclusion") != "failure":
                continue
            steps = {step.get("name"): step for step in job.get("steps") or []}
            spec = steps.get(SPEC_STEP)
            if spec is None:
                reasons.append(f"第 {attempt} 次 attempt 的 {job.get('name')} 沒有名為 {SPEC_STEP} 的步驟")
                continue
            if spec.get("conclusion") != "failure":
                broken = [s.get("name") for s in job.get("steps") or [] if s.get("conclusion") == "failure"]
                reasons.append(f"第 {attempt} 次 attempt 的 {job.get('name')} 失敗在 {SPEC_STEP} 以外的步驟（{'、'.join(broken) or '不明'}）")
                continue
            log, error = run("gh", "api", "--allow-escape-sequences", f"repos/{repo}/actions/jobs/{job.get('id')}/logs")
            if log is None:
                reasons.append(f"讀不到第 {attempt} 次 attempt 的 {job.get('name')} 的紀錄：{error}")
                continue
            marker = RED_JOBS[name]
            hits = []
            for raw in log.splitlines():
                stamp, _, text = raw.partition(" ")
                if in_window(stamp, spec) and marker.search(ANSI.sub("", text)):
                    hits.append(ANSI.sub("", text).strip())
            if hits:
                candidates.append((attempt, job.get("name"), hits))
            else:
                reasons.append(f"第 {attempt} 次 attempt 的 {job.get('name')} 進了 {SPEC_STEP}，但紀錄裡沒有規格層級的失敗標記")
        if candidates:
            return candidates[0], reasons
        if not any(re.sub(r" \(.*\)$", "", name or "") in RED_JOBS for name in failed):
            reasons.append(f"第 {attempt} 次 attempt 失敗的只有 {'、'.join(failed) or '（沒有失敗的 job）'}"
                           "——流程檢查與 build 不算 red")
    return None, reasons


def judge_exemption(text):
    match = re.search(r"不適用[：:][ \t]*(.*)", text)
    if not match.group(1).strip():
        return [f"「不適用」後面沒有理由——下一步：寫成「不適用：<理由>」"]
    files, error = pages(f"repos/{repo}/pulls/{number}/files?per_page=100")
    if files is None:
        return [f"讀不到 PR #{number} 改了哪些檔：{error}——讀不到就不放行"]
    paths = []
    for item in files:
        paths += [p for p in (item.get("filename"), item.get("previous_filename")) if p]
    others = [p for p in dict.fromkeys(paths) if not (p.startswith("doc/") or p.endswith(".md"))]
    return [f"寫了「不適用」，但 PR 改到純文件以外的檔：{p}（定案 5：只有 doc/ 底下或 .md 才算）" for p in others]


tdd = section(body) if pull is not None else ""
if pull is not None and tdd is None:
    problems.append(f"PR #{number} 的描述沒有 `## TDD red` 段落——下一步：補上這一段，放這個 PR 自己的 pull_request 執行連結")
elif pull is not None:
    links = []
    for owner, name, run_id in re.findall(r"https://github\.com/([^/\s]+)/([^/\s]+)/actions/runs/([0-9]+)", tdd):
        if f"{owner}/{name}".lower() != repo.lower():
            out(f"TDD red 連結 runs/{run_id} 不是本 repo 的執行（{owner}/{name}），不算")
        elif run_id not in links:
            links.append(run_id)
    qualified = None
    for run_id in links:
        found, reasons = judge_run(run_id)
        if found:
            attempt, job, hits = found
            out(f"TDD red 連結 runs/{run_id}：合格——第 {attempt} 次 attempt 的 {job} 失敗在 {SPEC_STEP}（{hits[0]}）")
            qualified = qualified or run_id
        else:
            out(f"TDD red 連結 runs/{run_id}：不算——{'；'.join(reasons)}")
            problems.extend(f"TDD red 連結 runs/{run_id} 不算：{reason}" for reason in reasons)
    exempt = re.search(r"不適用[：:]", tdd)
    if qualified:
        problems = [p for p in problems if not p.startswith("TDD red 連結")]
    elif exempt:
        exemption = judge_exemption(tdd)
        if exemption:
            problems.extend(exemption)
        else:
            problems = [p for p in problems if not p.startswith("TDD red 連結")]
            out("TDD red：不適用（PR 只改了 doc/ 底下或 .md 的檔）")
    elif not links:
        problems.append(f"PR #{number} 的 `## TDD red` 段落沒有本 repo 的執行連結，也沒有「不適用：<理由>」"
                        "——下一步：放這個 PR 自己的 pull_request 執行連結")

# ── milestone 順序 ──────────────────────────────────────────────────────────
def version(title):
    match = VERSION.match(title or "")
    return tuple(int(part) for part in match.groups()) if match else None


if issue_number is not None:
    issue, error = gh_json("issue", "view", str(issue_number), "--json", "milestone")
    if issue is None:
        problems.append(f"讀不到 #{issue_number} 的 milestone：{error}——讀不到就不放行")
    elif not issue.get("milestone"):
        out(f"#{issue_number} 沒有 milestone，不檢查 milestone 順序")
    else:
        bound_number = issue["milestone"].get("number")
        milestones, error = pages(f"repos/{repo}/milestones?state=all&per_page=100")
        if milestones is None:
            problems.append(f"讀不到 milestone 清單：{error}——讀不到就不放行")
        else:
            bound = next((m for m in milestones if m.get("number") == bound_number), None)
            if bound is None:
                problems.append(f"milestone 清單裡讀不到 #{issue_number} 所在的 milestone（number {bound_number}）——讀不到就不放行")
            elif version(bound.get("title")) is None:
                problems.append(f"#{issue_number} 所在的 milestone 標題「{bound.get('title')}」不是 vX.Y.Z，排不出順序"
                                "——下一步：把 milestone 改名成 vX.Y.Z，或把 issue 移到有版號的 milestone")
            else:
                mine = version(bound["title"])
                for m in milestones:
                    if m.get("state") == "open" and version(m.get("title")) is None:
                        problems.append(f"還開著的 milestone「{m.get('title')}」標題不是 vX.Y.Z，排不出順序"
                                        "——下一步：改名成 vX.Y.Z，或關掉它")
                used = [bound] + sorted((m for m in milestones if version(m.get("title")) and version(m["title"]) < mine),
                                        key=lambda m: version(m["title"]))
                for m in used:
                    out(f"識別 milestone id={m.get('id')} number={m.get('number')} title={m.get('title')} state={m.get('state')}")
                blocking = [m["title"] for m in used[1:] if m.get("state") == "open"]
                if blocking:
                    problems.append(f"#{issue_number} 在 {bound['title']}，前面還有沒關閉的 milestone：{'、'.join(blocking)}"
                                    "（定案 6：要先完成數字小的）——下一步：等那些 milestone 關閉後重跑這一輪（gh run rerun <run> --failed）")

if problems:
    for problem in problems:
        print(f"{TAG}: {problem}", file=sys.stderr)
    print(f"{TAG}: 下一步：照上面每一條處理；TDD red 要是這個 PR 自己的 pull_request 執行（#407）", file=sys.stderr)
    sys.exit(1)
out("TDD red 與 milestone 順序都通過")
PY
}

main "$@"
