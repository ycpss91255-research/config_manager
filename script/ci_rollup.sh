#!/usr/bin/env bash
#
# CI 唯一的必要狀態檢查背後的判定：這一次執行裡，每一個該跑的 job 都真的跑了、而且過了。
#
# 分支保護只指名 ci-rollup 一個 job，所以**它放行的就是被合併的**。它先前的判定是
# 「每個相依的結果是 success 或 skipped」——那對只在 pull request 上跑的三個 job 是對的，
# 對其餘的不是：一個每次都該跑的 job 被跳過，那一次執行就少檢查了一樣東西，卻與全綠
# 分不出來。checks 拆成平行的幾段之後（#358）這件事變得更容易發生：覆蓋率那一段相依於
# 每一份分片，任何一份沒過，它就被跳過，而「被跳過」在舊的判定裡是通過。
#
# 所以每個 job 照它**這一次該不該跑**判：
#
#   每次都該跑    必須是 success
#   只在某種觸發  那種觸發時必須是 success；其餘時候 skipped 或 success
#
# failure、cancelled、任何不認得的結果，在哪種情況下都不是通過。
#
# 另外三件事對不起來也擋，因為每一件都是「有東西沒被判到」：
#
#   - workflow 裡有、ci-rollup 的 needs 沒列的 job：它的結果根本不會被看見
#   - needs 裡有、這裡的規則沒有分類的 job：不知道它這一次該不該跑
#   - 規則裡寫了、needs 裡沒有的 job：規則以為在守的東西不在
#
# 跑在 runner 上而不是測試映像裡，與 lint_checkpoints.sh 同一個理由：它讀的是 GitHub
# 給這個 job 的資訊（needs 的結果、觸發事件），那些只在 runner 上有。需要的只有 python3。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/ci_rollup.sh <workflow 檔> --always "<job …>" [--pull-request "<job …>"] [--main-push "<job …>"]

  <workflow 檔>          這個 job 所在的 workflow，用來找出每一個 job
  --always "<job …>"     每次都必須是 success 的 job，以空白分隔
  --pull-request "<job …>"
                         pull_request 觸發時必須是 success；其餘觸發允許 skipped
  --main-push "<job …>"  push 到 refs/heads/main 時必須是 success；其餘觸發允許 skipped

讀這幾個環境變數：
  NEEDS              每個相依 job 的結果，即 ${{ toJSON(needs) }}
  GITHUB_JOB         這個 job 自己的 id，從 workflow 的 job 清單裡排除
  GITHUB_EVENT_NAME  觸發事件
  GITHUB_REF         觸發的 ref

  fail  任何一個該跑的 job 不是 success（含 failure、cancelled、被跳過）
  fail  任何一個 job 是 failure 或 cancelled，不論它這一次該不該跑
  fail  workflow、needs、這幾組規則三者列的 job 對不起來
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  local workflow="${1:-}"
  if [[ -z "${workflow}" || "${workflow}" == --* ]]; then
    printf 'ci_rollup: 沒有指定 workflow 檔，job 清單無從比對。下一步：第一個參數給 workflow 的路徑，例如 .github/workflows/ci.yaml\n' >&2
    usage >&2
    return 2
  fi
  shift

  local always="" pull_request="" main_push=""
  while (( $# > 0 )); do
    case "$1" in
      --always | --pull-request | --main-push)
        if (( $# < 2 )); then
          printf 'ci_rollup: %s 後面少了 job 清單。下一步：照下面的用法補上\n' "$1" >&2
          usage >&2
          return 2
        fi
        case "$1" in
          --always) always="$2" ;;
          --pull-request) pull_request="$2" ;;
          --main-push) main_push="$2" ;;
        esac
        shift 2
        ;;
      *)
        printf 'ci_rollup: 不認得的參數 %s。下一步：照下面的用法改\n' "$1" >&2
        usage >&2
        return 2
        ;;
    esac
  done

  if [[ ! -f "${workflow}" ]]; then
    printf 'ci_rollup: 找不到 workflow 檔 %s，job 清單無從比對\n' "${workflow}" >&2
    printf 'ci_rollup: 下一步：確認這個 job 有先 checkout，且路徑相對於 repo 根目錄\n' >&2
    return 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'ci_rollup: python3 不在 PATH 上，一個 job 的結果都沒有被判\n' >&2
    printf 'ci_rollup: 下一步：在跑這支腳本的機器上裝 python3\n' >&2
    return 1
  fi

  python3 - "${workflow}" "${always}" "${pull_request}" "${main_push}" <<'PY'
import json
import os
import re
import sys

workflow_path, always, pull_request, main_push = sys.argv[1:5]
event = os.environ.get("GITHUB_EVENT_NAME", "")
ref = os.environ.get("GITHUB_REF", "")
self_job = os.environ.get("GITHUB_JOB", "")


def stop(lines):
    # 先把已經印在 stdout 的逐項判定送出去，CI 的紀錄裡判定表才會在結論之前。
    sys.stdout.flush()
    for line in lines:
        print(f"ci_rollup: {line}", file=sys.stderr)
    sys.exit(1)


# ── 輸入 ──────────────────────────────────────────────────────────────────
try:
    needs = json.loads(os.environ.get("NEEDS", ""))
except json.JSONDecodeError:
    needs = None
if not isinstance(needs, dict) or not needs:
    stop([
        "NEEDS 不是一份非空的 JSON 物件，一個 job 的結果都沒有被判。",
        "下一步：在 workflow 的這一步設 NEEDS: ${{ toJSON(needs) }}",
    ])
if not self_job:
    stop([
        "GITHUB_JOB 沒有設，分不出 workflow 裡哪一個 job 是自己。",
        "下一步：在 GitHub Actions 裡執行，或在規格裡自己設 GITHUB_JOB",
    ])


def workflow_jobs(path):
    """workflow 檔頂層 jobs: 底下的 job id，照出現順序。

    不靠 YAML 套件：runner 的 python3 不保證有。workflow 是這個 repo 自己寫的區塊式 YAML，
    job id 是 jobs: 底下第一層縮排的鍵。讀不出任何一個就停，不當成「沒有 job」。
    """
    found, indent, inside = [], None, False
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.rstrip("\n")
            if not inside:
                inside = re.fullmatch(r"jobs:\s*(#.*)?", line) is not None
                continue
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line[0].isspace():
                break
            width = len(line) - len(line.lstrip(" "))
            if indent is None:
                indent = width
            if width != indent:
                continue
            match = re.fullmatch(r"([A-Za-z0-9_-]+):\s*(#.*)?", line.strip())
            if match:
                found.append(match[1])
    return found


defined = [job for job in workflow_jobs(workflow_path) if job != self_job]
if not defined:
    stop([
        f"在 {workflow_path} 的 jobs: 底下讀不到任何 job，needs 無從比對。",
        "下一步：確認 workflow 用區塊式寫法，jobs: 底下每個 job 一個鍵",
    ])

rules = {}
problems = []
for kind, listed in (("always", always), ("pull-request", pull_request), ("main-push", main_push)):
    for job in listed.split():
        if job in rules:
            problems.append(f"{job} 同時被列在 --{rules[job]} 與 --{kind}")
        else:
            rules[job] = kind

# ── 三份清單對得起來 ──────────────────────────────────────────────────────
for job in defined:
    if job not in needs:
        problems.append(f"workflow 有 {job}，但 {self_job} 的 needs 沒列它——它的結果不會被看見")
for job in needs:
    if job not in defined:
        problems.append(f"needs 有 {job}，但 workflow 裡沒有這個 job")
    if job not in rules:
        problems.append(f"needs 有 {job}，但沒有被分進 --always／--pull-request／--main-push")
for job in rules:
    if job not in needs:
        problems.append(f"規則列了 {job}，但 needs 裡沒有它")

if problems:
    stop(["workflow、needs 與規則列的 job 對不起來："] + [f"  {p}" for p in problems] + [
        "下一步：在 workflow 裡讓三者列的 job 一致——新增的 job 要加進 needs，也要分進一組規則",
    ])

# ── 每個 job 照它這一次該不該跑判 ─────────────────────────────────────────
expected_now = {
    "always": True,
    "pull-request": event == "pull_request",
    "main-push": event == "push" and ref == "refs/heads/main",
}
KNOWN = {"success", "failure", "cancelled", "skipped"}
failed = []
print(f"ci_rollup: 觸發 {event or '（未設）'}，ref {ref or '（未設）'}")
for job, value in needs.items():
    result = value.get("result") if isinstance(value, dict) else None
    required = expected_now[rules[job]]
    if result == "success":
        verdict = "通過"
    elif result == "skipped" and not required:
        verdict = "通過（這次本來就不跑）"
    elif result == "skipped":
        verdict = "未通過：這次該跑卻被跳過"
    elif result in KNOWN:
        verdict = f"未通過：{result}"
    else:
        verdict = f"未通過：不認得的結果 {result!r}"
    print(f"  {job:<22} {str(result):<10} {'該跑' if required else '可略過':<4}  {verdict}")
    if not verdict.startswith("通過"):
        failed.append(job)

if failed:
    stop([
        f"有 job 沒有通過：{'、'.join(failed)}。",
        "下一步：打開上面列出的那幾個 job 看原因；被跳過的，去看它相依的 job 為什麼沒過",
    ])
print("ci_rollup: 每一個該跑的 job 都跑了，而且都過了")
PY
}

main "$@"
