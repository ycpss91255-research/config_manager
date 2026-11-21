#!/usr/bin/env bash
#
# 共用的綁定判定（#407 調整需求第 5 點）：這個 PR 綁的是哪一張 issue。
#
# 維護者 11/20 定案 8：每個 PR 都要用關閉關鍵字綁一張 issue（「只開 PR」的兩種例外取消）。
# checkpoints、single-task、merge-gate 與 merge job 都呼叫這一支，所以每一道檢查認的是同一套規則；
# 關閉參照的擷取規格只有一份 script/closing-references.txt。下列全部成立才回傳那一張：
#
#   - PR 開到 main（關閉關鍵字只在 PR 開到預設分支時生效，開到別的分支就沒有關閉參照可以核對）
#   - 描述裡的關閉參照正好一張，是本 repo 存在的 issue（不是別的 repo、不是 PR、不是網址）
#   - 那一張還沒有被完成：沒關閉；issue 上沒有「由 PR #<數字> 合併完成」開頭的留言；base 的歷史
#     裡沒有 merge job 的合併紀錄寫了它（曾經被完成、之後重開的仍算已完成）
#   - GitHub 的 closingIssuesReferences 正好就是那一張
#   - PR 標題沒有關閉參照；PR 範圍裡每個 commit 的訊息沒有指到綁定以外的關閉參照（commit 訊息的
#     關閉關鍵字合進預設分支時也會關 issue，而且不會列進 closingIssuesReferences）
#
# 讀不到（gh 非零結束）一律失敗，不當成「沒有被完成」。每一次都印出讀到的識別（PR 的 updatedAt、
# 標題與描述各自的 sha256、issue 的編號與 updatedAt）：同一輪的幾道檢查可能剛好讀在改綁的前後，
# 每一道紅燈都要能自己說清楚讀到的是什麼。
#
# 擋不住的：綁的那張 issue 真的描述了這個 PR 的內容；上線前就完成、之後被重開的舊 issue（沒有上面
# 兩種紀錄，靠 review 看 issue 的時間軸）。
#
# 跑在 runner 上、不在測試映像裡，與 lint_checkpoints.sh 同一個理由：它要 gh 與 token。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/pr_binding.sh <PR 編號> <base ref>

  <PR 編號>   要判定的 pull request
  <base ref>  PR 的範圍是 <base ref>..HEAD；也從它的歷史找 merge job 的合併紀錄

讀這個環境變數：
  GITHUB_REPOSITORY  <owner>/<repo>，判定「本 repo」用

  exit 0  綁定成立；最後一行是「pr_binding: 綁定 #<N>」
  exit 1  綁定不成立（逐條指名原因與下一步），或讀不到要讀的 PR、issue
  exit 2  用法不對
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
    printf 'pr_binding: GITHUB_REPOSITORY 沒有設，分不出哪些參照指的是本 repo\n' >&2
    printf 'pr_binding: 下一步：在 GitHub Actions 裡執行，或設 GITHUB_REPOSITORY=<owner>/<repo>\n' >&2
    return 2
  fi
  local here
  here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  python3 - "$1" "$2" "${here}/closing-references.txt" <<'PY'
import hashlib
import json
import os
import re
import subprocess
import sys

number, base, pattern_file = sys.argv[1:4]
repo = os.environ["GITHUB_REPOSITORY"]
TAG = "pr_binding"
COMPLETED = re.compile(r"^\s*由 PR #([0-9]+) 合併完成")
RECORD = re.compile(r"^合併紀錄：issue #([0-9]+)；", re.MULTILINE)
problems = []


def out(line):
    print(f"{TAG}: {line}", flush=True)


def finish():
    if problems:
        for problem in problems:
            print(f"{TAG}: {problem}", file=sys.stderr)
        print(f"{TAG}: 下一步：照上面每一條改——每個 PR 只用關閉關鍵字綁一張本 repo 還沒完成的 issue（#407）", file=sys.stderr)
        sys.exit(1)


def load_pattern(path):
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.rstrip("\n") for line in handle if line.strip() and not line.lstrip().startswith("#")]
    except OSError as error:
        print(f"{TAG}: 讀不到關閉參照的擷取規格 {path}：{error}", file=sys.stderr)
        print(f"{TAG}: 下一步：確認 script/closing-references.txt 在簽出裡", file=sys.stderr)
        sys.exit(2)
    if len(lines) != 1:
        print(f"{TAG}: {path} 應該正好有一行樣式，讀到 {len(lines)} 行", file=sys.stderr)
        print(f"{TAG}: 下一步：把樣式合成一行，說明寫在 # 開頭的行", file=sys.stderr)
        sys.exit(2)
    return re.compile(lines[0], re.IGNORECASE)


PATTERN = load_pattern(pattern_file)


def refs(text):
    """(原文, repo＋編號 或 None, 網址 或 None)。沒寫 repo 的算本 repo。"""
    found = []
    for match in PATTERN.finditer(text or ""):
        if match.group("url"):
            found.append((match.group(0), None, match.group("url")))
        else:
            owner = (match.group("repo") or repo).lower()
            found.append((match.group(0), (owner, int(match.group("number"))), None))
    return found


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


def sha256(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


pull, error = gh_json("pr", "view", number, "--json",
                      "number,title,body,baseRefName,closingIssuesReferences,updatedAt")
if pull is None:
    problems.append(f"讀不到 PR #{number}：{error}——讀不到就不放行")
    finish()
title, body = pull.get("title") or "", pull.get("body") or ""
out(f"識別 PR #{number} updatedAt={pull.get('updatedAt')} title_sha256={sha256(title)} body_sha256={sha256(body)}")

if pull.get("baseRefName") != "main":
    problems.append(f"PR #{number} 開到 {pull.get('baseRefName')!r}，不是 main：關閉關鍵字只在 PR 開到預設分支時生效"
                    "——下一步：本 repo 的 PR 一律開到 main")

# ── 描述 ────────────────────────────────────────────────────────────────────
here = repo.lower()
bound = []
for text, target, url in refs(body):
    if url:
        problems.append(f"PR #{number} 的描述裡「{text}」接的是網址，不是本 repo 的 issue 編號"
                        "——下一步：改寫成 Closes #<issue 編號>")
    elif target[0] != here:
        problems.append(f"PR #{number} 的描述裡「{text}」指到別的 repo（{target[0]}#{target[1]}）"
                        "——下一步：只綁本 repo 的 issue")
    elif target not in bound:
        bound.append(target)
if not refs(body):
    problems.append(f"PR #{number} 的描述沒有關閉關鍵字，沒有綁任何 issue（每個 PR 都要綁一張，#407）"
                    "——下一步：先開 issue，再在描述寫 Closes #<issue 編號>")
elif len(bound) > 1:
    listed = "、".join(f"#{n}" for _, n in bound)
    problems.append(f"PR #{number} 的描述綁了不只一張 issue（{listed}）——下一步：一個 PR 只綁一張，其餘的另開 PR")
candidate = bound[0][1] if len(bound) == 1 else None

# ── 標題與 commit 訊息 ──────────────────────────────────────────────────────
for text, _, _ in refs(title):
    problems.append(f"PR #{number} 的標題有關閉參照「{text}」——下一步：標題只寫 (#<issue 編號>)，關閉關鍵字寫在描述")

listing, error = run("git", "log", "--format=%H%x1f%B%x1e", f"{base}..HEAD")
if listing is None:
    problems.append(f"讀不到 PR 範圍（{base}..HEAD）的 commit 訊息：{error}——讀不到就不放行")
else:
    for entry in listing.split("\x1e"):
        if "\x1f" not in entry:
            continue
        sha, message = entry.strip("\n").split("\x1f", 1)
        for text, target, url in refs(message):
            if url or target != (here, candidate):
                problems.append(f"commit {sha[:7]} 的訊息有綁定以外的關閉參照「{text}」：合進 main 時也會關掉它"
                                "——下一步：改寫那則 commit 訊息（git commit --amend 或 rebase），拿掉那個關閉關鍵字")

# ── GitHub 的關閉參照 ───────────────────────────────────────────────────────
linked = []
for item in pull.get("closingIssuesReferences") or []:
    owner = ((item.get("repository") or {}).get("owner") or {}).get("login", "")
    name = (item.get("repository") or {}).get("name", "")
    linked.append((f"{owner}/{name}".lower(), item.get("number")))
for target in linked:
    if candidate is None or target != (here, candidate):
        problems.append(f"GitHub 的 closingIssuesReferences 有描述以外的 {target[0]}#{target[1]}"
                        "（例如側欄手動連了一張）——下一步：拿掉側欄多連的那一張")
if candidate is not None and (here, candidate) not in linked:
    problems.append(f"描述綁 #{candidate}，GitHub 的 closingIssuesReferences 卻沒有它"
                    "——下一步：確認描述的寫法 GitHub 認得（例如 Closes #N），或那個編號是 issue 不是 PR")

# ── 綁定的那一張 ────────────────────────────────────────────────────────────
if candidate is not None:
    issue, error = gh_json("issue", "view", str(candidate), "--json", "number,title,state,url,updatedAt,comments")
    if issue is None:
        problems.append(f"讀不到 #{candidate}（不存在，或查詢失敗）：{error}——讀不到就不放行")
    else:
        out(f"識別 issue #{candidate} updatedAt={issue.get('updatedAt')}")
        if "/pull/" in (issue.get("url") or ""):
            problems.append(f"#{candidate} 是 PR，不是 issue——下一步：綁這個 PR 要完成的那張 issue")
        elif issue.get("state") != "OPEN":
            problems.append(f"#{candidate} 已關閉（{issue.get('state')}），不能再綁——下一步：新的工作另開 issue")
        for comment in issue.get("comments") or []:
            match = COMPLETED.match(comment.get("body") or "")
            if match:
                author = (comment.get("author") or {}).get("login", "")
                problems.append(f"#{candidate} 已由 PR #{match[1]} 完成（{author} 的留言「{(comment.get('body') or '').strip().splitlines()[0]}」）"
                                "——下一步：這張已由那個 PR 完成；新的工作另開 issue")
        history, error = run("git", "log", "--format=%H%x1f%s%x1f%B%x1e", base)
        if history is None:
            problems.append(f"讀不到 {base} 的歷史，查不了 merge job 的合併紀錄：{error}——讀不到就不放行")
        else:
            for entry in history.split("\x1e"):
                if entry.count("\x1f") < 2:
                    continue
                sha, subject, message = entry.strip("\n").split("\x1f", 2)
                if any(int(n) == candidate for n in RECORD.findall(message)):
                    problems.append(f"#{candidate} 已由 {base} 上的合併 commit {sha[:7]}（{subject}）完成"
                                    "——下一步：這張已被完成；新的工作另開 issue")

finish()
out(f"綁定 #{candidate}")
PY
}

main "$@"
