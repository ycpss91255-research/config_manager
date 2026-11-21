#!/usr/bin/env bash
#
# 署名檢查（#405，#269 第 2 步）：commit 訊息、PR 標題與描述裡的 Claude 署名與 session 連結。
#
# 樣式在 script/attribution-patterns.txt，三個呼叫端都讀那一份：這支的 pr 模式（CI 的 attribution job）、
# commit-msg 模式（script/githook-checks/commit-msg/20-no-attribution.sh），以及 Claude Code 的 hook
# （script/claude-hooks/no-attribution.sh）。一行一個延伸正規式，不分大小寫、逐行比對。
#
# **失敗時只印位置（哪個 commit、標題或描述、第幾行）與命中的樣式，不印命中的內容**：CI 的紀錄是公開的，
# 印出來就等於把完整的 session 網址又公開一次。
#
# pr 模式讀 PR 範圍內（base..HEAD）每個 commit 的完整訊息，merge commit 也算：改成 merge commit 合併之後，
# PR 分支上的每個 commit 都原樣進 main。讀不到 commit 或 PR 就失敗，不放行。
#
# 它擋不住的（另見 doc/TEST-PLAN.md）：在網頁上改 PR 標題或描述之後，ci.yaml 的 pull_request 沒有 edited，
# 這個 job 不會重跑；run-name 把 PR 標題印進 workflow 的執行名稱，那不是這支的輸出。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

usage() {
  cat <<'USAGE'
用法：script/lint_attribution.sh commit-msg <訊息檔>
      script/lint_attribution.sh pr <PR 編號> <base ref>

  commit-msg   commit-msg hook：檢查 git 給的訊息檔的每一行
  pr           CI：PR 範圍內（base ref..HEAD）每個 commit 的訊息、PR 標題與描述。要 gh；讀不到就失敗

  樣式在 script/attribution-patterns.txt。失敗時只印位置與命中的樣式，不印命中的內容。

  exit 0   都沒有命中
  exit 1   有命中，或讀不到要讀的訊息檔、commit、PR、樣式清單
  exit 2   用法不對
USAGE
}

main() {
  local mode="${1:-}"
  case "${mode}" in
    commit-msg)
      if (($# != 2)); then
        usage >&2
        return 2
      fi
      ;;
    pr)
      if (($# != 3)); then
        usage >&2
        return 2
      fi
      ;;
    -h | --help)
      usage
      return 0
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
  shift

  if ! command -v python3 >/dev/null 2>&1; then
    printf 'lint_attribution: python3 不在 PATH 上，一行都沒有被檢查\n' >&2
    printf 'lint_attribution: 下一步：在跑這支腳本的機器上裝 python3\n' >&2
    return 1
  fi

  python3 - "${REPO_ROOT}/script/attribution-patterns.txt" "${mode}" "$@" <<'PY'
import hashlib
import json
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TAG = "lint_attribution"
patterns_path, mode = sys.argv[1], sys.argv[2]
arguments = sys.argv[3:]
LINES = re.compile(r"\r\n|\n|\r")


def fail(lines):
    for line in lines:
        print(f"{TAG}: {line}", file=sys.stderr)
    sys.exit(1)


def load_patterns(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as error:
        fail([f"讀不到樣式清單 {path}（{error.strerror}），一行都沒有被檢查。",
              "下一步：確認 script/attribution-patterns.txt 在這個簽出裡"])
    patterns = []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        try:
            patterns.append((line, re.compile(line, re.IGNORECASE)))
        except re.error as error:
            fail([f"樣式清單 {path} 第 {number} 行不是可用的正規式（{error}）。", "下一步：修正那一行"])
    if not patterns:
        fail([f"樣式清單 {path} 裡沒有任何樣式，一行都沒有被檢查。", "下一步：照檔頭的說明補回樣式"])
    return patterns


PATTERNS = load_patterns(patterns_path)
HITS = []  # (位置, 行號或 None, 樣式)


def check(where, text, numbered=True):
    for lineno, line in enumerate(LINES.split(text), 1):
        for source, pattern in PATTERNS:
            if pattern.search(line):
                HITS.append((where, lineno if numbered else None, source))


def run(*command):
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        return None, completed.stderr.strip() or f"{command[0]} 沒有給出原因"
    return completed.stdout, None


def report(step):
    if not HITS:
        return False
    print(f"{TAG}: 命中 script/attribution-patterns.txt 的樣式（只印位置與樣式，不印命中的內容）：", file=sys.stderr)
    for where, lineno, source in HITS:
        position = f"{where}第 {lineno} 行" if lineno is not None else where
        print(f"{TAG}:   - {position}命中樣式 {source}", file=sys.stderr)
    print(f"{TAG}: 下一步：{step}", file=sys.stderr)
    return True


if mode == "commit-msg":
    path = arguments[0]
    try:
        with open(path, "rb") as handle:
            message = handle.read().decode("utf-8", errors="replace")
    except OSError as error:
        fail([f"讀不到 commit 訊息檔 {path}（{error.strerror}），這次 commit 的訊息沒有被檢查。",
              "下一步：這支由 git 的 commit-msg hook 呼叫，參數是訊息檔的路徑"])
    check("commit 訊息", message)
    if report("刪掉那一行再 commit；要舉例 session 網址時寫成 session_<ID> 這類不完整的寫法"):
        sys.exit(1)
    print(f"{TAG}: 通過：commit 訊息沒有命中 script/attribution-patterns.txt 的樣式")
    sys.exit(0)

number, base = arguments
listing, error = run("git", "rev-list", f"{base}..HEAD")
if listing is None:
    fail([f"讀不到 PR 範圍（{base}..HEAD）裡的 commit：{error}",
          "下一步：checkout 要帶完整歷史（fetch-depth: 0），base ref 寫成 origin/<base 分支>"])
commits = listing.split()
if not commits:
    fail([f"PR 範圍（{base}..HEAD）裡讀不到任何 commit，沒有東西被檢查。",
          "下一步：確認 base ref 是這個 PR 的 base 分支（origin/<base 分支>）"])
for sha in commits:
    message, error = run("git", "log", "-1", "--format=%B", sha)
    if message is None:
        fail([f"讀不到 commit {sha} 的訊息：{error}", "下一步：確認 checkout 帶了完整歷史（fetch-depth: 0）"])
    check(f"commit {sha} 的訊息", message)

raw, error = run("gh", "pr", "view", number, "--json", "title,body")
if raw is None:
    fail([f"讀不到 PR #{number}，PR 標題與描述沒有被檢查：{error}",
          "下一步：確認這個 job 有 GH_TOKEN 與 pull-requests: read 權限，PR 編號是這次觸發的那一個"])
try:
    pull = json.loads(raw)
except json.JSONDecodeError as error:
    fail([f"讀不到 PR #{number}：gh 回的不是 JSON（{error}）", "下一步：在 runner 上重跑 gh pr view 看它回了什麼"])
# 讀到的識別（#407）：merge job 的重判與 fork 的網頁合併前拿它和現況比對。只印雜湊，不印內容。
print(f"{TAG}: 識別 PR #{number} "
      f"title_sha256={hashlib.sha256((pull.get('title') or '').encode('utf-8')).hexdigest()} "
      f"body_sha256={hashlib.sha256((pull.get('body') or '').encode('utf-8')).hexdigest()}")
check("PR 標題", pull.get("title") or "", numbered=False)
check("PR 描述", pull.get("body") or "")

if report("commit 的訊息改寫後以 --force-with-lease 推回（例如 git commit --amend -F <檔>）；PR 標題與描述用 "
          "gh pr edit --title …／--body-file <檔> 改。改了標題或描述會自己重跑（pull_request 有 edited，#407）；"
          "用 GITHUB_TOKEN 改的不會，要手動重跑"):
    sys.exit(1)
print(f"{TAG}: 通過：PR 範圍內 {len(commits)} 個 commit 的訊息、PR 標題與描述都沒有命中 script/attribution-patterns.txt 的樣式")
PY
}

main "$@"
