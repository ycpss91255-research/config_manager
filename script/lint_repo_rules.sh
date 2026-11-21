#!/usr/bin/env bash
#
# repo 的保護設定由 CI 核對（#407 調整需求第 9 點，維護者定案 12）：GitHub 上實際的設定，和
# AGENTS.md 的「repo 保護設定」表一不一致。
#
# 合併改由 CI 的 merge job 做之後，main 守不守得住全靠 ruleset：strict 被關掉、討論串那一項被
# 取消、允許的合併方式多了 squash、tag ruleset 不見了——每一件都不會讓任何一個 job 變紅，卻
# 都讓合併規則失效。所以 repo-rules job 每一輪都把實際設定讀出來，和表逐項比，**一致的也印**，
# 讓人看得到比了什麼。
#
# 讀的四個端點（文件都標為只要 Metadata 讀取權）：
#
#   repos/<repo>/rules/branches/main   main 上實際生效的規則（含別的 ruleset 加上去的）
#   repos/<repo>/rulesets              列表，照表上的名稱找 id
#   repos/<repo>/rulesets/<id>         每個表上的 ruleset 的詳情：目標、狀態、規則與參數
#   repos/<repo>                       repo 那幾列（allow_merge_commit）
#
# 比對的約定：
#
#   - 設定值要完全相同；陣列當成順序不重要的集合比，物件陣列也一樣。
#   - 規則的種類兩個方向都比：表上有、實際沒有，或實際有、表上沒有（例如 merge_queue），都失敗。
#   - 表上沒列的參數欄位不比，但印出來給人看；deletion、non_fast_forward 沒有 parameters 或是
#     null 都算一致（證據檔 base 的兩個 ruleset 都是這樣）。
#   - ruleset main 另外和 rules/branches/main 比同一組種類與參數：ruleset 寫對了不代表 main 上
#     實際生效的只有它。
#   - **必驗項目讀不到一律失敗**：gh 非零結束（403、404）、回 null、欄位不在，都指名那一項，
#     不移出比對。讀不到卻算通過，等於沒有核對。
#   - repo 那幾列讀不到時不失敗，印出「未自動核對：<項目>，以設定頁人工核對為準」：只有 pull
#     權限的帳號讀 allow_merge_commit 回 null（#407 證據檔第 7 節），讀不到不代表設定錯。
#   - bypass_actors 文件寫明只在呼叫者對那個 ruleset 有寫入權時才回傳，所以每一輪對每個 ruleset
#     都印一行「核對不了」，不影響結束碼，也不算通過。
#
# JSON 由 python 自己解析，不用 gh 的 --jq：測試映像沒有 jq，規格的假 gh 也模擬不了。
#
# 它擋不住的：bypass 名單；Actions 的「Workflow permissions」（讀是 403）；它只在 CI 跑的時候才
# 發現，設定被改掉而沒有人開 PR 或手動觸發，就不會發現。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/lint_repo_rules.sh [<表所在的檔>]

  <表所在的檔>  預設 AGENTS.md（相對於目前目錄）。讀標題「### repo 保護設定」底下
                第一張表頭是「| 對象 | 項目 | 設定值 |」的表，讀到表結束為止

環境變數：
  GITHUB_REPOSITORY  <owner>/<repo>，必填（GitHub Actions 會自動設好）

讀的 API（gh api，唯讀）：
  repos/<repo>/rules/branches/main   main 上實際生效的規則
  repos/<repo>/rulesets              列表，照名稱找 id
  repos/<repo>/rulesets/<id>         表上每個 ruleset 的詳情
  repos/<repo>                       repo 那幾列（例如 allow_merge_commit）

表的格式（每一列三欄）：
  對象    repo，或 ruleset <名稱>（照名稱在列表裡找；找不到、同名有兩個以上都失敗）
  項目    反引號包住：target、enforcement、conditions.ref_name.include、
          conditions.ref_name.exclude、rules（規則種類的集合），或 <規則種類>.<參數名>；
          每個 ruleset 都要有前五項，表上要有 ruleset main
  設定值  反引號包住的一段 JSON，不能是 null

比對：
  設定值要完全相同；陣列當成順序不重要的集合比，物件陣列也一樣
  rules 兩個方向都比：表上有、實際沒有，或實際有、表上沒有，都失敗
  表上沒列的參數欄位不比，只印出來；規則沒有 parameters 或是 null，只要表上沒列它的參數就算一致
  ruleset main 另外和 rules/branches/main 比同一組種類與參數（同一種規則出現兩次，每一次都要符合）
  ruleset 的項目讀不到（gh 非零結束、null、欄位不在）一律失敗，不移出比對
  repo 的項目讀不到時不失敗，印出「未自動核對：<項目>，以設定頁人工核對為準」
  bypass_actors 每一輪對每個 ruleset 印「核對不了」，不影響結束碼

  exit 0   表上每一項都一致
  exit 1   有不一致，或 ruleset 的項目讀不到
  exit 2   用法不對、沒設 GITHUB_REPOSITORY、表讀不到或格式不對、沒有 python3
USAGE
}

main() {
  case "${1:-}" in
    -h | --help)
      usage
      return 0
      ;;
    -*)
      printf 'lint_repo_rules: 不認得的參數 %s。下一步：照下面的用法改\n' "$1" >&2
      usage >&2
      return 2
      ;;
  esac
  if (( $# > 1 )); then
    printf 'lint_repo_rules: 參數太多（%s），只收一個表所在的檔。下一步：照下面的用法改\n' "$*" >&2
    usage >&2
    return 2
  fi
  local table="${1:-AGENTS.md}"

  local repo="${GITHUB_REPOSITORY:-}"
  if [[ -z "${repo}" ]]; then
    printf 'lint_repo_rules: 沒有設 GITHUB_REPOSITORY，不知道要讀哪個 repo 的保護設定\n' >&2
    printf 'lint_repo_rules: 下一步：設 GITHUB_REPOSITORY=<owner>/<repo>（GitHub Actions 會自動設好）\n' >&2
    return 2
  fi
  if [[ ! "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    printf 'lint_repo_rules: GITHUB_REPOSITORY 是「%s」，不是 <owner>/<repo> 的形式\n' "${repo}" >&2
    printf 'lint_repo_rules: 下一步：設成 <owner>/<repo>，例如 ycpss91255-research/config_manager\n' >&2
    return 2
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'lint_repo_rules: python3 不在 PATH 上，保護設定一項都沒有比對\n' >&2
    printf 'lint_repo_rules: 下一步：在跑這支腳本的機器上裝 python3\n' >&2
    return 2
  fi

  python3 - "${table}" "${repo}" <<'PY'
import json
import re
import subprocess
import sys

table_path, repo = sys.argv[1], sys.argv[2]

NAME = "lint_repo_rules"
HEADING = re.compile(r"###\s+repo 保護設定\s*")
SECTION_END = re.compile(r"#{1,3}\s")
FENCE = re.compile(r"\s*(`{3,}|~{3,})")
HEADER = ["對象", "項目", "設定值"]
SEPARATOR = re.compile(r":?-+:?")
CODE = re.compile(r"`([^`]+)`")
REPO_ITEM = re.compile(r"[a-z_]+")
PARAM_ITEM = re.compile(r"([a-z_]+)\.([A-Za-z0-9_]+)")
TARGETING = ("target", "enforcement", "conditions.ref_name.include", "conditions.ref_name.exclude")
FIXED = TARGETING + ("rules",)
MAIN = "main"
FORMAT_HINT = "格式見 script/lint_repo_rules.sh --help"


# ── 表 ────────────────────────────────────────────────────────────────────

def format_error(lineno, what, next_step):
    where = f"{table_path}:{lineno}" if lineno else table_path
    print(f"{NAME}: {where}：{what}", file=sys.stderr)
    print(f"{NAME}: 下一步：{next_step}", file=sys.stderr)
    sys.exit(2)


def cells(line):
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|") and not inner.endswith("\\|"):
        inner = inner[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", inner)]


def visible_lines(lines):
    """[(行號, 那一行)]，略過圍起來的程式碼區塊：區塊裡的 # 不是標題。"""
    fence = None
    for number, line in enumerate(lines, 1):
        marker = FENCE.match(line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence is None:
            yield number, line


def table_rows():
    try:
        with open(table_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except (OSError, UnicodeDecodeError) as error:
        format_error(0, f"讀不到表所在的檔：{error}", "確認路徑；不給參數時讀目前目錄的 AGENTS.md")
    visible = list(visible_lines(lines))
    start = next((i for i, (_, line) in enumerate(visible) if HEADING.fullmatch(line)), None)
    if start is None:
        format_error(0, "找不到標題「### repo 保護設定」", f"在這個檔加上這一節與它的表（{FORMAT_HINT}）")
    heading_line = visible[start][0]
    header_line = None
    for number, line in visible[start + 1:]:
        if SECTION_END.match(line):
            break
        if line.strip().startswith("|") and cells(line) == HEADER:
            header_line = number
            break
    if header_line is None:
        format_error(
            heading_line,
            "「repo 保護設定」這一節裡沒有表頭是「| 對象 | 項目 | 設定值 |」的表",
            f"在這一節加上那張表（{FORMAT_HINT}）",
        )
    separator = cells(lines[header_line]) if header_line < len(lines) else []
    if len(separator) != 3 or not all(SEPARATOR.fullmatch(cell) for cell in separator):
        format_error(header_line + 1, "表頭下一行不是分隔列", "表頭下面接一行 |---|---|---|")
    rows = []
    for number in range(header_line + 2, len(lines) + 1):
        line = lines[number - 1]
        if not line.strip().startswith("|"):
            break
        rows.append((number, line))
    if not rows:
        format_error(header_line, "「repo 保護設定」的表沒有任何一列", f"把要核對的設定寫進表裡（{FORMAT_HINT}）")
    return rows


def parse_table():
    """{對象: {"kind", "name", "line", "items": {項目: 設定值}, "params": {種類: {參數: 設定值}}}}。"""
    objects = {}
    for number, line in table_rows():
        row = cells(line)
        if len(row) != 3:
            format_error(number, f"這一列有 {len(row)} 欄，要正好三欄（對象、項目、設定值）", "照表頭補齊，或拿掉多出來的欄")
        owner, item_cell, value_cell = row
        item_match, value_match = CODE.fullmatch(item_cell), CODE.fullmatch(value_cell)
        if not item_match or not value_match:
            format_error(number, "項目與設定值都要用一對反引號包住", '寫成 | ruleset main | `target` | `"branch"` |')
        item = item_match[1].strip()
        try:
            value = json.loads(value_match[1])
        except json.JSONDecodeError as error:
            format_error(number, f"設定值 {value_match[1]} 不是 JSON：{error}", '字串要加雙引號，例如 `"branch"`')
        if value is None:
            format_error(number, f"{item} 的設定值是 null：讀到 null 一律算讀不到，這一列永遠不會一致", "寫出實際該有的值")
        if owner == "repo":
            kind, name = "repo", None
            if not REPO_ITEM.fullmatch(item):
                format_error(number, f"repo 的項目「{item}」不是 repo 回應的頂層欄位名", "寫成 `allow_merge_commit` 這種欄位名")
        elif owner.startswith("ruleset ") and owner[len("ruleset "):].strip():
            kind, name = "ruleset", owner[len("ruleset "):].strip()
            owner = f"ruleset {name}"
            param = PARAM_ITEM.fullmatch(item)
            if item not in FIXED and (param is None or param[1] == "conditions"):
                format_error(
                    number,
                    f"ruleset 的項目「{item}」不認得",
                    "項目寫 target、enforcement、conditions.ref_name.include／exclude、rules，或 <規則種類>.<參數名>",
                )
            if item == "rules" and not (isinstance(value, list) and value and all(isinstance(v, str) for v in value)):
                format_error(number, "rules 的設定值要是規則種類（字串）的清單", '寫成 `["deletion", "non_fast_forward"]`')
            if item.startswith("conditions.") and not isinstance(value, list):
                format_error(number, f"{item} 的設定值要是清單", '寫成 `["~DEFAULT_BRANCH"]` 或 `[]`')
            if item in ("target", "enforcement") and not isinstance(value, str):
                format_error(number, f"{item} 的設定值要是字串", '寫成 `"branch"` 或 `"active"`')
        else:
            format_error(number, f"對象「{owner}」不認得", "對象寫 repo，或 ruleset <名稱>")
        entry = objects.setdefault(owner, {"kind": kind, "name": name, "line": number, "items": {}, "params": {}})
        if item in entry["items"]:
            format_error(number, f"{owner} 的 {item} 出現了兩次", "同一個對象的同一個項目只留一列")
        entry["items"][item] = value
    for owner, entry in objects.items():
        if entry["kind"] != "ruleset":
            continue
        missing = [item for item in FIXED if item not in entry["items"]]
        if missing:
            format_error(entry["line"], f"{owner} 少了 {'、'.join(missing)}", "每個 ruleset 都要有 target、enforcement、conditions.ref_name.include／exclude、rules")
        for item, value in entry["items"].items():
            if item in FIXED:
                continue
            kind, param = item.split(".", 1)
            if kind not in entry["items"]["rules"]:
                format_error(entry["line"], f"{owner} 列了 {item}，但 rules 沒有 {kind}", f"在 rules 加上 {kind}，或拿掉 {item}")
            entry["params"].setdefault(kind, {})[param] = value
    if f"ruleset {MAIN}" not in objects:
        format_error(0, f"表上沒有 ruleset {MAIN}，rules/branches/main 無從比對", "加上 ruleset main 那幾列")
    return objects


# ── 實際設定 ──────────────────────────────────────────────────────────────

def gh_api(path):
    """(回應, 讀不到的原因)。gh 非零結束、不是 JSON、回 null 都算讀不到。"""
    try:
        completed = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None, f"gh 不在 PATH 上，讀不了 {path}"
    if completed.returncode != 0:
        detail = " ".join((completed.stderr or completed.stdout).split())[:300] or "gh 沒有給出原因"
        return None, f"gh api {path} 以 {completed.returncode} 結束：{detail}"
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        return None, f"gh api {path} 回的不是 JSON：{error}"
    if data is None:
        return None, f"gh api {path} 回的是 null"
    return data, None


def field(data, path):
    """(值, 讀不到的原因)。欄位不在或是 null 都算讀不到。"""
    value, walked = data, []
    for key in path.split("."):
        walked.append(key)
        if not isinstance(value, dict) or key not in value:
            return None, f"回應裡沒有 {'.'.join(walked)}"
        value = value[key]
        if value is None:
            return None, f"{'.'.join(walked)} 是 null"
    return value, None


def rule_list(rules, where):
    """(規則清單, 讀不到的原因)。每一條都要是帶 type 的物件。"""
    if rules is None:
        return None, f"{where} 沒有規則清單或是 null"
    if not isinstance(rules, list):
        return None, f"{where} 不是規則的清單"
    if not all(isinstance(rule, dict) and isinstance(rule.get("type"), str) for rule in rules):
        return None, f"{where} 裡有一條規則沒有 type"
    return rules, None


# ── 比對 ──────────────────────────────────────────────────────────────────

def show(value):
    return json.dumps(value, ensure_ascii=False)


def canonical(value):
    """完全相同才一致：物件照鍵排序，陣列當成順序不重要的集合；true 與 1 不同。"""
    if isinstance(value, dict):
        return "{" + ",".join(f"{show(key)}:{canonical(item)}" for key, item in sorted(value.items())) + "}"
    if isinstance(value, list):
        return "[" + ",".join(sorted({canonical(item) for item in value})) + "]"
    return show(value)


counts = {"一致": 0, "不一致": 0, "讀不到": 0}
problems = []
unverified = []


def verdict(owner, item, expected, actual, reason=None, note=""):
    """印出一項（一致的也印）；不一致或讀不到就記下來。"""
    if reason is not None:
        status, seen = "讀不到", f"實際讀不到（{reason}）"
    else:
        status = "一致" if canonical(expected) == canonical(actual) else "不一致"
        seen = f"實際讀到的 {show(actual)}"
    line = f"{item}：表上寫的 {show(expected)}，{seen}{note}"
    counts[status] += 1
    print(f"  {status:<4}{line}")
    if status != "一致":
        problems.append(f"{owner} 的 {line}")


def position(rule, index, total):
    parts = []
    if total > 1:
        parts.append(f"第 {index} 條")
    if "ruleset_id" in rule:
        parts.append(f"ruleset_id {rule['ruleset_id']}")
    return f"（{'，'.join(parts)}）" if parts else ""


def check_rules(owner, spec, rules, reason):
    """規則的種類兩個方向都比；表上列的參數逐一比；表上沒列的參數只印出來。"""
    expected = spec["items"]["rules"]
    if reason is not None:
        verdict(owner, "rules", expected, None, reason)
    else:
        actual = sorted({rule["type"] for rule in rules})
        missing = [kind for kind in dict.fromkeys(expected) if kind not in actual]
        extra = [kind for kind in actual if kind not in expected]
        parts = []
        if missing:
            parts.append(f"表上有、實際沒有：{'、'.join(missing)}")
        if extra:
            parts.append(f"實際有、表上沒有：{'、'.join(extra)}")
        verdict(owner, "rules", expected, actual, note=f"（{'；'.join(parts)}）" if parts else "")
    for kind, params in spec["params"].items():
        same_kind = [] if reason is not None else [rule for rule in rules if rule["type"] == kind]
        for name, value in params.items():
            item = f"{kind}.{name}"
            if reason is not None:
                verdict(owner, item, value, None, reason)
                continue
            if not same_kind:
                verdict(owner, item, value, None, f"實際沒有 {kind} 這條規則")
                continue
            for index, rule in enumerate(same_kind, 1):
                label = item + position(rule, index, len(same_kind))
                actual_params = rule.get("parameters")
                if not isinstance(actual_params, dict):
                    verdict(owner, label, value, None, f"{kind} 的 parameters 不在或是 null")
                elif name not in actual_params:
                    verdict(owner, label, value, None, f"{kind} 的 parameters 裡沒有 {name}")
                elif actual_params[name] is None:
                    verdict(owner, label, value, None, f"{item} 是 null")
                else:
                    verdict(owner, label, value, actual_params[name])
    if reason is not None:
        return
    for kind in dict.fromkeys(rule["type"] for rule in rules):
        same_kind = [rule for rule in rules if rule["type"] == kind]
        listed = spec["params"].get(kind, {})
        for index, rule in enumerate(same_kind, 1):
            actual_params = rule.get("parameters")
            if not isinstance(actual_params, dict):
                continue
            for name, value in actual_params.items():
                if name not in listed:
                    print(f"  不比  {kind}.{name}{position(rule, index, len(same_kind))}：表上沒列，實際讀到的 {show(value)}")


def check_ruleset(owner, spec, listing, listing_error):
    name = spec["name"]
    detail, reason, source = None, None, "rulesets 列表"
    if listing_error is not None:
        reason = f"讀不到 ruleset 列表：{listing_error}"
    else:
        matches = [entry for entry in listing if isinstance(entry, dict) and entry.get("name") == name]
        if not matches:
            reason = f"ruleset 列表裡找不到名稱是「{name}」的 ruleset"
        elif len(matches) > 1:
            ids = "、".join(str(entry.get("id")) for entry in matches)
            reason = f"ruleset 列表裡名稱是「{name}」的有 {len(matches)} 個（id {ids}），分不出是哪一個"
        elif matches[0].get("id") is None:
            reason = f"ruleset 列表裡「{name}」那一筆沒有 id"
        else:
            source = f"repos/{repo}/rulesets/{matches[0]['id']}"
            detail, reason = gh_api(source)
            if detail is not None and not isinstance(detail, dict):
                detail, reason = None, f"gh api {source} 回的不是物件"
    print(f"== {owner}（{source}）")
    for item in TARGETING:
        expected = spec["items"][item]
        if reason is not None:
            verdict(owner, item, expected, None, reason)
        else:
            value, why = field(detail, item)
            verdict(owner, item, expected, value, why)
    rules, why = (None, reason) if reason is not None else rule_list(detail.get("rules"), "詳情的 rules")
    check_rules(owner, spec, rules, why)
    print(
        f"  核對不了：{name} 的 bypass_actors（文件寫明只在呼叫者對這個 ruleset 有寫入權時才回傳；"
        "誰可以繞過，由維護者在設定頁核對）"
    )
    if detail is None:
        print("    這一輪沒有讀到詳情，bypass_actors 也讀不到")
    elif "bypass_actors" in detail:
        print(f"    這一輪的回應有 bypass_actors（只印出來，不比）：{show(detail['bypass_actors'])}")
    else:
        print("    這一輪的回應沒有 bypass_actors，讀不到")


def check_effective(spec):
    owner = "rules/branches/main"
    path = f"repos/{repo}/rules/branches/main"
    effective, reason = gh_api(path)
    rules, why = (None, reason) if reason is not None else rule_list(effective, f"gh api {path} 的回應")
    print(f"== {owner}（main 上實際生效的規則，照 ruleset {MAIN} 的 rules 與參數比）")
    check_rules(owner, spec, rules, why)


def check_repo(spec):
    path = f"repos/{repo}"
    data, reason = gh_api(path)
    if data is not None and not isinstance(data, dict):
        data, reason = None, f"gh api {path} 回的不是物件"
    print(f"== repo（{path}）")
    for item, expected in spec["items"].items():
        value, why = (None, reason) if reason is not None else field(data, item)
        if why is not None:
            # 讀不到不失敗（只有 pull 權限的帳號回 null）；這一行照原文印，由人到設定頁核對。
            print(f"未自動核對：{item}，以設定頁人工核對為準")
            print(f"    表上寫的 {show(expected)}；讀不到的原因：{why}")
            unverified.append(item)
        else:
            verdict("repo", item, expected, value)


# ── 主流程 ────────────────────────────────────────────────────────────────

objects = parse_table()
print(f"{NAME}: 讀 {repo} 的保護設定，和 {table_path} 的「repo 保護設定」表逐項比對")
listing, listing_error = None, None
if any(entry["kind"] == "ruleset" for entry in objects.values()):
    listing, listing_error = gh_api(f"repos/{repo}/rulesets")
    if listing is not None and not isinstance(listing, list):
        listing, listing_error = None, f"gh api repos/{repo}/rulesets 回的不是清單"
for owner, spec in objects.items():
    if spec["kind"] == "repo":
        check_repo(spec)
        continue
    check_ruleset(owner, spec, listing, listing_error)
    if spec["name"] == MAIN:
        check_effective(spec)

sys.stdout.flush()
if problems:
    print(
        f"{NAME}: 和 {table_path} 的「repo 保護設定」表比對，"
        f"{counts['不一致']} 項不一致、{counts['讀不到']} 項讀不到：",
        file=sys.stderr,
    )
    for problem in problems:
        print(f"{NAME}:   {problem}", file=sys.stderr)
    print(
        f"{NAME}: 下一步：改回設定，或在同一個 PR 改表；讀不到的項目先查原因"
        "（權杖讀不讀得到、ruleset 在不在），不能把它移出比對",
        file=sys.stderr,
    )
    sys.exit(1)
tail = f"；未自動核對 {len(unverified)} 項（{'、'.join(unverified)}），以設定頁人工核對為準" if unverified else ""
print(f"{NAME}: 比對了 {counts['一致']} 項，實際設定都和表一致；bypass_actors 核對不了，不算在內{tail}")
PY
}

main "$@"
