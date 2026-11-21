#!/usr/bin/env bash
#
# 標題任務編號限制（#351）：commit 主旨、PR 標題與關聯 issue 的標題，最多列一個 F 項、一個 #編號。
#
# 維護者 11/14、11/18 定案：只做機械判定，不靠人工審查。**它數的是編號，不證明**一個 commit、PR 或
# issue 只有一個任務——標題只寫一個編號、內容卻做了兩件事，這裡看不出來；那是否滿足「單一任務」，
# 照維護者核准的定義判定。數法：
#
#   全形數字與全形 ＃ 先轉半形
#   F 項       前面不是英數字的 F<數字>（UTF8 不算）
#   #編號      #<數字>
#   同一個編號出現兩次算一個；範圍與列舉（F33～F35、F33-35、F37、F38、#1,#2）一律算多個
#   scope      type(scope) 只能有一個 scope：`,`、`、`、`+`、`&` 或空白算列舉（`/` 不算）
#
# **有 sub-issue 的 issue 一樣檢查**，沒有豁免（11/18：新增一張子 issue 就能避開檢查，例外太寬）。
#
# 本機 hook 只做不必讀 issue 的部分（commit-msg、pre-push：主旨的編號與 scope）；要讀 PR 與 issue 的
# 部分在 CI 的 single-task job（pr 模式）：關閉關鍵字綁定的 issue 數、F 項與 issue 的對應、關聯 issue
# 的標題。讀不到就失敗，不放行。merge job 的合併 commit（主旨固定是「Merge pull request #<n>」，#407）
# 不在範圍內。
#
# 綁定數由共用的綁定判定（script/pr_binding.sh，#407）判：checkpoints、merge-gate、merge job 認的是
# 同一套規則。pr 模式每一次都印出讀到的識別（PR 標題與描述的 sha256、關聯 issue 的編號與 updatedAt），
# merge job 的重判與網頁合併前的比對看這幾行。
#
# 獨立的一支，不併進 script/test.sh --lint，也不併進 lint_commit.sh 或 lint_checkpoints.sh。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/lint_single_task.sh subject <主旨>…
      script/lint_single_task.sh commit-msg <訊息檔>
      script/lint_single_task.sh pre-push
      script/lint_single_task.sh pr <PR 編號> <base ref>

  subject      檢查一則或多則主旨（commit 主旨或 PR 標題）
  commit-msg   commit-msg hook：只看訊息檔的第一行
  pre-push     pre-push hook：讀標準輸入，只看要推上去的新 commit（remote 上已經有的不數）
  pr           CI：PR 標題、PR 裡的 commit（merge commit 不數）、綁定判定（script/pr_binding.sh，
               要 GITHUB_REPOSITORY）、F 項與 issue 的對應、關聯 issue（綁定的，加上內文 Refs #N、
               Relates to #N）的標題。要 gh；讀不到 PR 或 issue 就失敗

  exit 0   全部通過
  exit 1   有違反標題任務編號限制的主旨、標題或 issue，或讀不到要讀的 PR／issue
  exit 2   用法不對
USAGE
}

main() {
  local mode="${1:-}"
  case "${mode}" in
    subject | commit-msg | pre-push | pr) shift ;;
    -h | --help)
      usage
      return 0
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac

  # python 程式本身經標準輸入（heredoc）餵進去，pre-push 的標準輸入先存成檔再交給它。
  local input=""
  if [[ "${mode}" == pre-push ]]; then
    input="$(mktemp)"
    cat >"${input}"
    set -- "${input}"
  fi
  local code=0
  CM_PR_BINDING="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/pr_binding.sh" \
    python3 - "${mode}" "$@" <<'PY' || code=$?
import hashlib
import json
import os
import re
import subprocess
import sys

mode, args = sys.argv[1], sys.argv[2:]

FULLWIDTH = str.maketrans("０１２３４５６７８９＃Ｆ", "0123456789#F")
F_TOKEN = re.compile(r"(?<![A-Za-z0-9])F([0-9]+)")
ISSUE_TOKEN = re.compile(r"#([0-9]+)")
SEPARATOR = r"\s*(?:[～~〜\-–—]|、|,|，|/)\s*"
F_SERIES = re.compile(r"(?<![A-Za-z0-9])F[0-9]+" + SEPARATOR + r"F?[0-9]+")
ISSUE_SERIES = re.compile(r"#[0-9]+" + SEPARATOR + r"#?[0-9]+")
PREFIX = re.compile(r"^[A-Za-z]+\(([^)]*)\)!?:")
LISTED_SCOPE = re.compile(r"[,，、+&\s]")
ZERO = re.compile(r"^0+$")
problems = []


def usage_error(message):
    print(f"lint_single_task: {message}", file=sys.stderr)
    print("lint_single_task: 下一步：看 script/lint_single_task.sh --help 的用法", file=sys.stderr)
    sys.exit(2)


def numbers(text):
    """(F 項的編號, #編號, F 有範圍或列舉, # 有範圍或列舉)。"""
    text = text.translate(FULLWIDTH)
    return (
        sorted(set(F_TOKEN.findall(text)), key=int),
        sorted(set(ISSUE_TOKEN.findall(text)), key=int),
        F_SERIES.search(text) is not None,
        ISSUE_SERIES.search(text) is not None,
    )


def check_title(where, text, check_scope):
    """回傳這一則違反的地方（每一條一句）。"""
    found = []
    fs, issues, f_series, issue_series = numbers(text)
    if len(fs) > 1 or f_series:
        listed = "、".join(f"F{n}" for n in fs)
        found.append(f"{where} 列了不只一個 F 項（{listed}{'，含範圍或列舉寫法' if f_series else ''}）：「{text}」")
    if len(issues) > 1 or issue_series:
        listed = "、".join(f"#{n}" for n in issues)
        found.append(f"{where} 列了不只一個 #編號（{listed}{'，含範圍或列舉寫法' if issue_series else ''}）：「{text}」")
    if check_scope:
        prefix = PREFIX.match(text)
        if prefix and LISTED_SCOPE.search(prefix[1]):
            found.append(f"{where} 的 scope 列了不只一個（{prefix[1]}）：「{text}」")
    return found


def git(*arguments):
    completed = subprocess.run(["git", *arguments], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        problems.append(f"git {' '.join(arguments)} 失敗：{completed.stderr.strip()}")
        return None
    return completed.stdout


def gh_json(*arguments):
    completed = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None, completed.stderr.strip() or "gh 沒有給出原因"
    try:
        return json.loads(completed.stdout), None
    except json.JSONDecodeError as error:
        return None, f"gh 回的不是 JSON：{error}"


def subjects_between(revisions):
    output = git("log", "--no-merges", "--format=%h%x09%s", *revisions)
    if output is None:
        return []
    return [line.split("\t", 1) for line in output.splitlines() if "\t" in line]


if mode == "subject":
    if not args:
        usage_error("subject 後面要給至少一則主旨")
    for subject in args:
        problems += check_title("主旨", subject, check_scope=True)

elif mode == "commit-msg":
    if len(args) != 1:
        usage_error("commit-msg 後面要給訊息檔的路徑")
    try:
        with open(args[0], encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as error:
        usage_error(f"讀不到訊息檔 {args[0]}：{error}")
    subject = next((line for line in lines if line.strip() and not line.startswith("#")), "")
    problems += check_title("commit 主旨", subject, check_scope=True)

elif mode == "pre-push":
    with open(args[0], encoding="utf-8") as handle:
        pushed = handle.read().splitlines()
    for line in pushed:
        fields = line.split()
        if len(fields) < 4 or ZERO.match(fields[1]):
            continue
        local, remote = fields[1], fields[3]
        revisions = [local, "--not", "--remotes"] if ZERO.match(remote) else [f"{remote}..{local}"]
        for short, subject in subjects_between(revisions):
            problems += check_title(f"commit {short}", subject, check_scope=True)

elif mode == "pr":
    if len(args) != 2:
        usage_error("pr 後面要給 PR 編號與 base ref")
    number, base = args
    pull, error = gh_json("pr", "view", number, "--json", "title,body,closingIssuesReferences")
    if pull is None:
        problems.append(f"讀不到 PR #{number}：{error}——讀不到就不放行")
    else:
        title, body = pull.get("title") or "", pull.get("body") or ""
        print(f"lint_single_task: 識別 PR #{number} "
              f"title_sha256={hashlib.sha256(title.encode('utf-8')).hexdigest()} "
              f"body_sha256={hashlib.sha256(body.encode('utf-8')).hexdigest()}")
        # 綁定數用共用的判定（#407）：它的識別與原因照原樣印出，這一道的紅燈說得出自己讀到的是什麼。
        binding = subprocess.run([os.environ["CM_PR_BINDING"], number, base],
                                 capture_output=True, text=True, check=False)
        sys.stdout.write(binding.stdout)
        closing = [item["number"] for item in pull.get("closingIssuesReferences") or []]
        if binding.returncode != 0:
            for line in binding.stderr.splitlines():
                problems.append(f"綁定判定不成立：{line.removeprefix('pr_binding: ')}")
        else:
            closing = [int(binding.stdout.strip().splitlines()[-1].rsplit("#", 1)[1])]
        titled = [("PR 標題", title)] + [
            (f"commit {short}", subject) for short, subject in subjects_between([f"{base}..HEAD"])
        ]
        for where, text in titled:
            problems += check_title(where, text, check_scope=True)
        referenced = [int(n) for n in re.findall(r"(?i)\b(?:refs|relates\s+to)\s+#([0-9]+)", body)]
        linked = list(dict.fromkeys([*closing, *referenced]))
        issues = {}
        for issue_number in linked:
            data, error = gh_json("issue", "view", str(issue_number), "--json", "title,body,updatedAt")
            if data is None:
                problems.append(f"讀不到關聯的 issue #{issue_number}：{error}——讀不到就不放行")
                continue
            issues[issue_number] = data
            print(f"lint_single_task: 識別 issue #{issue_number} updatedAt={data.get('updatedAt')}")
            problems += check_title(f"issue #{issue_number} 的標題", data.get("title") or "", check_scope=False)
        # F 項與 issue 的對應：主旨或標題同時寫了一個 F 與一個 #，那張 issue 的標題或內文要出現那個 F。
        for where, text in titled:
            fs, refs, _, _ = numbers(text)
            if len(fs) != 1 or len(refs) != 1:
                continue
            target = int(refs[0])
            data = issues.get(target)
            if data is None:
                data, error = gh_json("issue", "view", str(target), "--json", "title,body")
                if data is None:
                    problems.append(f"{where} 寫了 F{fs[0]}（#{target}），但讀不到 issue #{target}：{error}——讀不到就不放行")
                    continue
                issues[target] = data
            mentioned = (data.get("title") or "") + "\n" + (data.get("body") or "")
            if not re.search(rf"(?<![A-Za-z0-9])F{fs[0]}(?![0-9])", mentioned.translate(FULLWIDTH)):
                problems.append(f"{where} 寫了 F{fs[0]}（#{target}），但 issue #{target} 的標題與內文都沒有 F{fs[0]}")

if problems:
    for problem in problems:
        print(f"lint_single_task: {problem}", file=sys.stderr)
    print(
        "lint_single_task: 下一步：一則主旨、一個 PR、一張 issue 只放一個任務編號（標題任務編號限制，#351）——"
        "拆成各自的 commit、PR 或 issue；F 項寫在對應的 issue 裡",
        file=sys.stderr,
    )
    sys.exit(1)
print("lint_single_task: 標題任務編號限制都符合")
PY
  [[ -z "${input}" ]] || rm -f "${input}"
  return "${code}"
}

main "$@"
