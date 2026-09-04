#!/usr/bin/env bash
#
# 訊息 lint。面向使用者的錯誤訊息缺三要素就失敗。
#
# 設計 §0.4「例外處理的具體要求」第 2 條：「面向使用者的錯誤訊息必須包含三要素：
# 發生什麼、在哪裡（檔案／行號／欄位）、該怎麼改。『格式錯誤』不合格。」同一節的
# 開頭又寫著「所有規範必須可由工具檢查——無法自動檢查的規範等同不存在」。在這支
# 腳本之前，沒有任何工具檢查這一條，所以依它自己的規則，它先前並不存在。
#
# 檢查兩種出口：`raise <具名例外>(...)` 的訊息字串，以及 `print(..., file=sys.stderr)`。
# 這兩個是使用者實際會讀到的地方——前者經 api 層轉成 HTTP 回應與介面文案，後者是 CLI。
#
# 判準（刻意保守；一支上線就噴一堆錯的 lint 會被關掉，而被關掉的 lint 等於不存在）：
#
#   R1  訊息含「下一步：」。三要素的第三項「該怎麼改」。挑一個固定字串而不是猜測
#       語意，是因為這條要能被寫訊息的人預測——猜不到判準的 lint 只會被繞過。
#   R2  訊息至少帶一個具體標的（路徑、欄位名、uid、完整參照形式）。三要素的第二項
#       「在哪裡」。標的可以是內插值，也可以是字面的機器識別碼——環境變數名、含 /
#       的路徑、帶副檔名的檔名。這一條擋下的是「格式錯誤」那一類：整則都是中文散文，
#       沒有任何可以指過去的東西，使用者無從行動。
#
#       R2 起初只認內插值。實測 27 則現有訊息，那個版本擋下 8 則，其中 1 則是誤報：
#       `CM_CONFIG_REPO 未設定` 的標的是那個環境變數名，它本來就沒有東西可以內插。
#       誤報率 1/8 對一支剛上線的 lint 太高——會被關掉的 lint 等於不存在——所以放寬
#       成現在這樣，剩下的 7 則全是真的缺「下一步：」。
#
# 第一項要素「發生什麼」不另立規則：訊息只要存在就一定說了些什麼，把它變成可檢查的
# 條件只會退化成字數下限，而字數擋不住廢話。R1 與 R2 是真的擋得住東西的那兩條。
#
# **沒有逐行的抑制註解。** §0.4 的執行方式那段要求「調整需修改設定檔並在 PR 中說明
# 理由——使放寬本身成為可見的決策，而非個案 # noqa」。要放行某一則訊息，就改這裡的
# 判準並在 PR 說明，不是在那一行加註解。
#
# **不含中文的訊息不擋，但會被列出來。** 介面文案一律中文（CONTEXT.md），所以純
# ASCII 的字串是 usage 行，或 `print(f"preflight: {error}")` 這種轉述既有訊息的殼——
# 它轉述的那則訊息本身已經在這支 lint 的管轄裡。這是已知的漏洞：用英文寫的、真的
# 面向使用者的訊息會從這裡溜過去。所以它們**逐則印出來**，不是靜默跳過（不變式 2）。
#
# 用 python3 的 ast 而不是 grep：訊息會跨行、會隱含串接、括號會出現在字串裡面，
# 而這三件事在這個 repo 的訊息裡同時發生。用正規式去逼近它只會得到一支自己會說謊
# 的 lint，那比沒有更糟。
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: script/lint_messages.sh [<path>]

  <path>  A directory of Python sources, or one .py file
          (default: src/config_manager). A single file is what the
          post-edit hook passes -- see script/check_file.sh.

  fail  a user-facing message is missing "下一步：" (what to do about it)
  fail  a user-facing message names nothing concrete (no interpolation, no
        env var, path or filename to point at)
  skip  a message with no Chinese in it -- a relay or a usage line; listed, not silent

Checked: raise <NamedException>(...) messages, and print(..., file=sys.stderr).
There is no per-line suppression comment: loosening the rule means editing this
script and saying why in the PR (design §0.4).
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac
  local target="${1:-src/config_manager}"

  if [[ ! -e "${target}" ]]; then
    printf 'lint_messages: %s does not exist; nothing to check.\n' "${target}"
    return 0
  fi
  if [[ -f "${target}" && "${target}" != *.py ]]; then
    printf 'lint_messages: %s is not a Python source; nothing to check.\n' "${target}"
    return 0
  fi

  # python3 缺席時大聲失敗。一支因為直譯器不在而回 0 的 lint，就是不變式 2 禁止的
  # 靜默通過——而那個形狀在這個 repo 已經讓 hadolint 連綠六次、CI 連紅六次。
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'lint_messages: python3 is not on PATH, so nothing was checked.\n' >&2
    printf 'lint_messages: run this inside docker/Dockerfile.test-tools, or install python3.\n' >&2
    return 1
  fi

  python3 - "${target}" <<'PY'
import ast
import pathlib
import re
import sys

DIRECTIVE = "下一步："
PRINT_PLUMBING = {"file", "sep", "end", "flush"}

# 字面上就足以當標的的三種機器識別碼：含 / 的路徑、ENV_VAR 形式的名字、帶副檔名的
# 檔名。純中文散文一個都不會命中，那正是「格式錯誤」被擋下的原因。
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
_ENV_VAR = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\Z")
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\.[a-z]{2,5}\Z")


def names_a_target(text):
    for token in _TOKEN.findall(text):
        if "/" in token or _ENV_VAR.match(token) or _FILENAME.match(token):
            return True
    return False


def is_chinese(ch):
    """CJK 表意文字、CJK 標點（。、「」），以及全形符號（：）。"""
    return (
        "　" <= ch <= "〿"
        or "㐀" <= ch <= "鿿"
        or "＀" <= ch <= "￯"
    )


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def literal_and_target(node):
    """(字面文字, 有沒有內插標的)。認不出的形狀算「有標的、沒字面」。"""
    if isinstance(node, ast.Constant):
        return (node.value, False) if isinstance(node.value, str) else (None, True)
    if isinstance(node, ast.JoinedStr):
        # 隱含串接的相鄰字串在這裡已經是同一個 JoinedStr 的多個 Constant。
        text = "".join(
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        holes = any(isinstance(part, ast.FormattedValue) for part in node.values)
        return text, holes
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left_text, left_holes = literal_and_target(node.left)
        right_text, right_holes = literal_and_target(node.right)
        holes = left_holes or right_holes or left_text is None or right_text is None
        return (left_text or "") + (right_text or ""), holes
    return None, True


def message_of(call, drop_kwargs=frozenset()):
    parts, holes = [], False
    args = list(call.args) + [kw for kw in call.keywords if kw.arg not in drop_kwargs]
    for arg in args:
        value = arg.value if isinstance(arg, ast.keyword) else arg
        text, hole = literal_and_target(value)
        if text:
            parts.append(text)
        holes = holes or hole
    return "".join(parts), holes


def sites(tree):
    """(行號, 標籤, 訊息文字, 有沒有標的)，依原始碼順序。"""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            name = dotted(node.exc.func)
            if not name or not name.rsplit(".", 1)[-1][:1].isupper():
                continue
            text, holes = message_of(node.exc)
            found.append((node.lineno, f"raise {name}", text, holes))
        elif isinstance(node, ast.Call) and dotted(node.func) == "print":
            to_stderr = any(
                kw.arg == "file" and dotted(kw.value) == "sys.stderr"
                for kw in node.keywords
            )
            if not to_stderr:
                continue
            text, holes = message_of(node, PRINT_PLUMBING)
            found.append((node.lineno, "print -> stderr", text, holes))
    return sorted(found)


def excerpt(text):
    flat = " ".join(text.split())
    return flat if len(flat) <= 60 else flat[:59] + "…"


root = pathlib.Path(sys.argv[1])
# 單一檔案是 hook 走的路徑：只檢查剛編輯過的那一個，不掃整棵樹。
files = [root] if root.is_file() else sorted(root.rglob("*.py"))
if not files:
    print(f"lint_messages: no Python sources in {root}; nothing to check.")
    raise SystemExit(0)

messages = failures = skipped = 0
for path in files:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as error:
        print(
            f"FAIL  {path}:{error.lineno}  cannot be parsed, so its messages were "
            f"NOT checked: {error.msg}",
            file=sys.stderr,
        )
        failures += 1
        continue

    for lineno, label, text, has_target in sites(tree):
        messages += 1
        if not any(is_chinese(ch) for ch in text):
            print(
                f"SKIP  {path}:{lineno}  {label}: no Chinese in it -- read as a relay "
                f"or a usage line, not a user-facing message"
            )
            skipped += 1
            continue
        reasons = []
        if DIRECTIVE not in text:
            reasons.append("沒有「下一步：」——三要素缺「該怎麼改」")
        if not has_target and not names_a_target(text):
            reasons.append("沒有具體標的——三要素缺「在哪裡」")
        if not reasons:
            continue
        print(f"FAIL  {path}:{lineno}  {label}: {'；'.join(reasons)}", file=sys.stderr)
        print(f"      {excerpt(text)}", file=sys.stderr)
        failures += 1

print(
    f"lint_messages: {messages} message(s) in {root} -- "
    f"{skipped} relayed/not user-facing, {failures} violation(s)"
)
raise SystemExit(1 if failures else 0)
PY
}

main "$@"
