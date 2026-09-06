#!/usr/bin/env bash
#
# 訊息 lint。面向使用者的錯誤訊息缺三要素就失敗。
#
# 設計 §0.4「例外處理的具體要求」第 2 條：「面向使用者的錯誤訊息必須包含三要素：
# 發生什麼、在哪裡（檔案／行號／欄位）、該怎麼改。『格式錯誤』不合格。」同一節的
# 開頭又寫著「所有規範必須可由工具檢查——無法自動檢查的規範等同不存在」。在這支
# 腳本之前，沒有任何工具檢查這一條，所以依它自己的規則，它先前並不存在。
#
# 兩種語言，兩組出口：
#
#   Python  `raise <具名例外>(...)` 的訊息字串，以及 `print(..., file=sys.stderr)`
#   shell   寫到 fd 2 的東西（`printf ... >&2`），以及轉述函式（`die`）的呼叫點
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
# 面向使用者的訊息會從這裡溜過去。所以它們**逐則印出來**，不是靜默跳過（不變式 2），
# 而且**每個標的結束時再把數量印成一段大聲的結論**——`script/` 的執行期輸出目前
# 幾乎全是英文（#106／#108），一個「掃了、每則都跳過、回報 0 violation」的摘要
# 比不掃更危險，因為它看起來在檢查（#133）。
#
# Python 那邊用 python3 的 ast 而不是 grep：訊息會跨行、會隱含串接、括號會出現在
# 字串裡面，而這三件事在這個 repo 的訊息裡同時發生。用正規式去逼近它只會得到一支
# 自己會說謊的 lint，那比沒有更糟。shell 沒有 AST 可用，換的保證寫在下面的
# `# ---- shell ----` 那一段。
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: script/lint_messages.sh [<path>...]

  <path>  A directory, one .py file, or one .sh file
          (default: src/config_manager script). A single file is what the
          post-edit hook passes -- see script/check_file.sh.

  fail  a user-facing message is missing "下一步：" (what to do about it)
  fail  a user-facing message names nothing concrete (no interpolation, no
        env var, path or filename to point at)
  fail  a source file cannot be parsed, so its messages were not checked
  skip  a message with no Chinese in it -- a relay or a usage line; listed
        one by one, and counted again in a loud closing note

Checked: raise <NamedException>(...) messages and print(..., file=sys.stderr)
in Python; writes to fd 2 and relay-function calls in shell.
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

  local -a targets=("$@")
  # 預設兩個標的。`script/` 是 #133 補上的：先前的預設只有 src/config_manager，
  # 於是 32 支腳本的執行期輸出完全不在管轄內，而 pdf-conformance review 只好把
  # 它們「另計」、逐則人工判讀——人工判讀正是 §0.4 說「等同不存在」的那種規範。
  (( ${#targets[@]} )) || targets=(src/config_manager script)

  # python3 缺席時大聲失敗。一支因為直譯器不在而回 0 的 lint，就是不變式 2 禁止的
  # 靜默通過——而那個形狀在這個 repo 已經讓 hadolint 連綠六次、CI 連紅六次。
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'lint_messages: python3 不在 PATH 上，所以一則訊息都沒有被檢查\n' >&2
    printf 'lint_messages: 下一步：改用 ./script/test.sh，它在 docker/Dockerfile.test-tools 裡跑\n' >&2
    return 1
  fi

  python3 - "${targets[@]}" <<'PY'
import ast
import pathlib
import re
import subprocess
import sys

DIRECTIVE = "下一步："
PRINT_PLUMBING = {"file", "sep", "end", "flush"}

# 字面上就足以當標的的三種機器識別碼：含 / 的路徑、ENV_VAR 形式的名字、帶副檔名的
# 檔名。純中文散文一個都不會命中，那正是「格式錯誤」被擋下的原因。
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
# 兩種形狀：帶底線的 `CM_CONFIG_REPO`，以及不帶底線的全大寫 `PATH`／`HOME`。
# 第二種是把 `script/` 納進來之後補的：`release: gh 不在 PATH 上` 這則訊息指名了
# 一個工具與一個環境變數，卻因為 `PATH` 沒有底線而被判成「沒有具體標的」——誤報。
# 下限三個字母，讓 `PR`、`CI` 這種散文裡的縮寫不會被當成標的（#133）。
_ENV_VAR = re.compile(r"(?:[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[A-Z][A-Z0-9]{2,})\Z")
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\.[a-z]{2,5}\Z")


def names_a_target(text):
    for token in _TOKEN.findall(text):
        if "/" in token or _ENV_VAR.match(token) or _FILENAME.match(token):
            return True
    return False


def is_chinese(ch):
    """CJK 表意文字。

    先前這裡還認 CJK 標點（。、「」）與全形符號（：）。把 `script/` 納進來之後改掉：
    `lint_commit.sh` 有一則英文訊息叫做 `subject ends with a period (. or 。)`——
    那個 `。` 是**被描述的字元本身**，不是這則訊息的語言。判成中文之後，一則英文
    訊息被拿去對「下一步：」比對，得到一次誤報。中文訊息一定帶表意文字，所以只認
    這一段既擋得住原本擋得住的，又擋不下這一則（#133）。
    """
    return "㐀" <= ch <= "鿿"


def has_chinese(text):
    return any(is_chinese(ch) for ch in text)


# ============================================================================
# Python
# ============================================================================

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


def python_sites(source, path):
    """(行號, 標籤, 訊息文字, 有沒有標的, 有沒有順帶傾印用法)，依原始碼順序。

    最後一項對 Python 恆為 False：那個豁免是 shell 才有的形狀（`usage >&2`）。
    """
    tree = ast.parse(source, filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            name = dotted(node.exc.func)
            if not name or not name.rsplit(".", 1)[-1][:1].isupper():
                continue
            text, holes = message_of(node.exc)
            found.append((node.lineno, f"raise {name}", text, holes, False))
        elif isinstance(node, ast.Call) and dotted(node.func) == "print":
            to_stderr = any(
                kw.arg == "file" and dotted(kw.value) == "sys.stderr"
                for kw in node.keywords
            )
            if not to_stderr:
                continue
            text, holes = message_of(node, PRINT_PLUMBING)
            found.append((node.lineno, "print -> stderr", text, holes, False))
    return sorted(found)


# ============================================================================
# shell（#133）
# ============================================================================
#
# shell 沒有 AST 可用，而這支腳本原本的註解說得對：用正規式去逼近訊息的形狀，只會
# 得到一支自己會說謊的 lint。所以這裡換一個保證：**不宣稱看懂 shell，只保證沒有
# 任何一個出口從眼皮底下溜過去**。三件事撐起那個保證：
#
#   1. **出口的定義是結構性的，不是語意性的：寫到 fd 2。** 整個檔案以引號狀態機
#      掃過一次，`>&2` 只有落在「不在引號裡、不在 heredoc 主體裡、不在註解裡」的
#      位置才算數。`printf 'run it as foo >&2'` 這種字面裡的 `>&2` 不會被誤認為
#      出口，而 `usage >&2` 這種沒有字面文字的會被算進去、判為轉述——與 Python 那邊
#      `print(f"preflight: {error}")` 同一個先例。
#   2. **認不出來就大聲失敗。** 檔案先過 `bash -n`（與 Python 那邊接住 SyntaxError
#      是同一件事：解析不了的檔案，它的訊息沒有被檢查，那不是通過）；掃完仍停在
#      未閉合的引號裡也一樣非零結束並指名行號。會說謊的 lint 的失效形態是
#      「掃了、沒看懂、回報通過」，這兩道把那條路徑封死。
#   3. **`die` 不寫死名字。** 轉述函式由檔案自己的結構推導：一個函式若它的每個
#      stderr 寫入都沒有中文字面、而且內插的是 `$*`／`$@`／`$1`，它就是把別人的話
#      送出去的殼，它的**呼叫點**才帶訊息。`entrypoint.sh` 的 `die` 由這條規則認
#      出來，不是由名字認出來——下一支腳本把它叫做 `fail` 也一樣成立。轉述函式的
#      **本體不被豁免**：它自己那則沒有中文，會照常被列成 SKIP。沒有任何出口
#      被規則排除掉，這是第 1 點的完整性能成立的原因。
#
# **一則訊息不是一個 printf。** 這個 repo 的 shell 訊息幾乎都是連續數個
# `printf ... >&2`：第一行說發生什麼，最後一行說下一步。實測 `script/` 底下的腳本，
# 「一個 printf 等於一則訊息」會把每一組的第一行都判成缺「下一步：」。所以合併規則
# 是：**相鄰的 stderr 寫入算同一則訊息，中間只允許空行與註解行**。這條規則對寫訊息
# 的人是可預測的（與挑「下一步：」這個固定字串同一個理由），而且它只會把訊息合得
# 更大——合得太大是漏抓，合得太小是誤報，而誤報會讓 lint 被關掉。

_HEREDOC = re.compile(r"""<<-?[ \t]*(?:'([^']*)'|"([^"]*)"|([A-Za-z_][A-Za-z0-9_]*))""")
_FUNC_OPEN = re.compile(r"^[ \t]*(?:function[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(\)[ \t]*\{")
_RELAY_ARG = re.compile(r"\$(?:[*@]|\{[*@]\})")


class Unterminated(Exception):
    def __init__(self, lineno):
        super().__init__(lineno)
        self.lineno = lineno


def logical_lines(lines):
    """[(起始行號, [(kind, text), ...])]。kind 是 code／sq／dq。

    heredoc 主體、註解與續行在這裡消化掉；引號跨行的算同一個邏輯行。
    """
    out = []
    total = len(lines)
    index = 0
    while index < total:
        start = index
        pieces = []
        buf = []
        state = "normal"
        heredocs = []
        while True:
            if index >= total:
                if state != "normal":
                    raise Unterminated(start + 1)
                break
            line = lines[index]
            column = 0
            continued = False
            while column < len(line):
                char = line[column]
                if state == "normal":
                    if char == "\\":
                        if column + 1 == len(line):
                            continued = True
                            column += 1
                            continue
                        buf.append(line[column : column + 2])
                        column += 2
                        continue
                    if char == "'":
                        pieces.append(("code", "".join(buf)))
                        buf = []
                        state = "sq"
                        column += 1
                        continue
                    if char == '"':
                        pieces.append(("code", "".join(buf)))
                        buf = []
                        state = "dq"
                        column += 1
                        continue
                    if char == "#" and (column == 0 or line[column - 1] in " \t;&|("):
                        break  # 註解吃到行尾
                    if line.startswith("<<", column) and not line.startswith("<<<", column):
                        match = _HEREDOC.match(line, column)
                        if match:
                            heredocs.append(
                                match.group(1) or match.group(2) or match.group(3)
                            )
                            buf.append(line[column : match.end()])
                            column = match.end()
                            continue
                    buf.append(char)
                    column += 1
                    continue
                if state == "sq":
                    close = line.find("'", column)
                    if close < 0:
                        buf.append(line[column:])
                        column = len(line)
                        break
                    buf.append(line[column:close])
                    pieces.append(("sq", "".join(buf)))
                    buf = []
                    state = "normal"
                    column = close + 1
                    continue
                # dq
                if char == "\\" and column + 1 < len(line):
                    buf.append(line[column + 1])
                    column += 2
                    continue
                if char == "\\":
                    column += 1
                    continue
                if char == '"':
                    pieces.append(("dq", "".join(buf)))
                    buf = []
                    state = "normal"
                    column += 1
                    continue
                buf.append(char)
                column += 1
                continue
            index += 1
            if state != "normal":
                buf.append("\n")
                continue
            if continued:
                continue
            break
        pieces.append(("code", "".join(buf)))
        # heredoc 主體排在整個邏輯行之後，不是排在 `<<TAG` 之後。
        for tag in heredocs:
            while index < total and lines[index].strip() != tag:
                index += 1
            index += 1
        out.append((start + 1, pieces))
    return out


def _code(pieces):
    return "".join(text for kind, text in pieces if kind == "code")


def _literal(pieces):
    return "".join(text for kind, text in pieces if kind in ("sq", "dq"))


def _interpolates(pieces):
    return any("$" in text for kind, text in pieces if kind in ("code", "dq"))


def _writes_stderr(pieces):
    return ">&2" in _code(pieces)


def _functions(logical):
    """{函式名: [本體的邏輯行]}。收尾靠行首的 `}`，那是這個 repo 每支腳本的形狀。"""
    bodies = {}
    open_name = None
    body = []
    for _lineno, pieces in logical:
        code = _code(pieces)
        if open_name is None:
            match = _FUNC_OPEN.match(code)
            if match:
                open_name = match.group(1)
                body = []
            continue
        if code.startswith("}"):
            bodies[open_name] = body
            open_name = None
            continue
        body.append(pieces)
    return bodies


def _relay_functions(bodies):
    """轉述函式：每個 stderr 寫入都沒有中文字面，而且送出去的是 `$*`／`$@`。

    `entrypoint.sh` 的 `die` 由這條認出來，不是由名字認出來。內插 `$1` 不算——
    實測 `script/build.sh` 的 `main()` 就會被 `$1` 那個版本誤認成轉述函式，
    而任何吃位置參數的函式都會。轉述的定義是「把**全部**的話原封不動送出去」。
    """
    names = set()
    for name, body in bodies.items():
        writes = [pieces for pieces in body if _writes_stderr(pieces)]
        if writes and all(
            not has_chinese(_literal(pieces))
            and _RELAY_ARG.search(_code(pieces) + _literal(pieces))
            for pieces in writes
        ):
            names.add(name)
    return names


def _help_functions(bodies):
    """說明函式：本體有 heredoc、而且完全不寫 stderr。`usage()` 就是這個形狀。

    由結構推導而不是認 `usage` 這個名字——同一支腳本把它改名叫 `help` 也一樣成立。
    """
    return {
        name
        for name, body in bodies.items()
        if any("<<" in _code(pieces) for pieces in body)
        and not any(_writes_stderr(pieces) for pieces in body)
    }


def _calls(code, names):
    for name in names:
        if re.search(r"(?:^|[;&|(){}\s])" + re.escape(name) + r"(?=[ \t]|$)", code):
            return name
    return None


# 兩個出口之間最多容許幾個邏輯行，仍算同一則訊息。**這個數字是量出來的**：
# `script/` 底下跨得最開的一組是 `release.sh` 的 218／220／223，中間隔著 `if` 與
# `fi`，各一行。再放寬就會開始把兩個不同錯誤分支的訊息黏成一則——那是漏抓；
# 收得更緊則把「第一行說發生什麼、最後一行說下一步」拆成兩則——那是誤報。
# 空行與註解不佔位（掃描時它們已經消失），所以這個數字只數真的有東西的行。
MERGE_WINDOW = 2


def shell_sites(source, path):
    """(行號, 標籤, 訊息文字, 有沒有標的, 有沒有順帶傾印用法)。相鄰的出口已合併。"""
    lines = source.splitlines()
    logical = logical_lines(lines)
    bodies = _functions(logical)
    relays = _relay_functions(bodies)
    helps = _help_functions(bodies)

    outlets = []
    for lineno, pieces in logical:
        code = _code(pieces)
        label = None
        if _writes_stderr(pieces):
            label = "printf -> fd 2"
        elif relays and not _FUNC_OPEN.match(code):
            called = _calls(code, relays)
            if called:
                label = f"{called}()"
        if label is None:
            continue
        # `... >&2; usage >&2` ——同一個出口把用法說明一起送到 fd 2。下一步就是
        # 使用者眼前那份說明，R1 視為滿足。判準是結構性的（呼叫了本檔的說明函式，
        # 而且那次呼叫寫到 fd 2），不是猜測語意。
        dumps_help = bool(helps and _writes_stderr(pieces) and _calls(code, helps))
        outlets.append(
            (lineno, label, _literal(pieces), _interpolates(pieces), dumps_help)
        )

    # 相鄰的出口合併成一則訊息。空行與註解在上一步已經被消化成「什麼都沒有」的
    # 邏輯行，所以編號時把它們跳過；剩下的距離就是 MERGE_WINDOW 在數的東西。
    order = {}
    position = 0
    for lineno, pieces in logical:
        if not _code(pieces).strip() and not _literal(pieces):
            continue
        order[lineno] = position
        position += 1

    merged = []
    for lineno, label, text, target, dumps_help in outlets:
        if merged and order[lineno] - merged[-1][5] <= MERGE_WINDOW:
            head = merged[-1]
            merged[-1] = (
                head[0],
                head[1],
                head[2] + text,
                head[3] or target,
                head[4] or dumps_help,
                order[lineno],
            )
            continue
        merged.append((lineno, label, text, target, dumps_help, order[lineno]))
    return [entry[:5] for entry in merged]


# ============================================================================
# 判定
# ============================================================================

def excerpt(text):
    flat = " ".join(text.split())
    return flat if len(flat) <= 60 else flat[:59] + "…"


def collect(path):
    """(訊息清單, 解析失敗的理由)。解析不了的檔案不是「沒有訊息」。"""
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        try:
            return python_sites(source, path), None
        except SyntaxError as error:
            return [], (error.lineno or 1, f"cannot be parsed by python: {error.msg}")
    probe = subprocess.run(
        ["bash", "-n", str(path)], capture_output=True, text=True, check=False
    )
    if probe.returncode != 0:
        detail = " ".join(probe.stderr.split()) or "bash -n rejected it"
        return [], (1, f"cannot be parsed by bash: {detail}")
    try:
        return shell_sites(source, path), None
    except Unterminated as error:
        return [], (error.lineno, "a quote opened here is never closed, so the "
                                 "messages below it were NOT read")


roots = [pathlib.Path(arg) for arg in sys.argv[1:]]
failures = 0
skipped_overall = []

for root in roots:
    if not root.exists():
        print(f"lint_messages: {root} does not exist; nothing to check.")
        continue
    if root.is_file():
        if root.suffix not in (".py", ".sh"):
            print(f"lint_messages: {root} is neither a Python nor a shell source; "
                  "nothing to check.")
            continue
        files = [root]
    else:
        files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix in (".py", ".sh")
        )
    if not files:
        print(f"lint_messages: no Python or shell sources in {root}; nothing to check.")
        continue

    messages = skipped = 0
    for path in files:
        sites, broken = collect(path)
        if broken:
            lineno, reason = broken
            print(f"FAIL  {path}:{lineno}  {reason}", file=sys.stderr)
            failures += 1
            continue

        for lineno, label, text, has_target, dumps_help in sites:
            messages += 1
            if not has_chinese(text):
                print(
                    f"SKIP  {path}:{lineno}  {label}: no Chinese in it -- read as a relay "
                    f"or a usage line, not a user-facing message"
                )
                skipped += 1
                skipped_overall.append(f"{path}:{lineno}  {label}")
                continue
            reasons = []
            if DIRECTIVE not in text and not dumps_help:
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

# 跳過的數量印成一段大聲的結論，不是摘要行裡的一個數字（#133）。`script/` 的執行期
# 輸出目前幾乎全是英文，所以「不含中文 → 轉述」那條規則對它幾乎全面生效——一份
# 「掃了、每則都跳過、0 violation」的報告看起來與真的檢查過一模一樣，而那正是
# 不變式 2 禁止的形狀。數量與比例一起印，因為「98 則裡跳過 3 則」與「98 則裡跳過
# 95 則」是兩件完全不同的事，而摘要行的那個數字分不出來。
if skipped_overall:
    total = len(skipped_overall)
    print(
        f"\nlint_messages: 上面有 {total} 則訊息不含中文，判準對它們一則都沒有生效。",
        file=sys.stderr,
    )
    print(
        "lint_messages: 那不是「檢查過而且通過」——用英文寫的、真的面向使用者的訊息"
        "會從「不含中文 → 轉述」這條底下溜過去。",
        file=sys.stderr,
    )
    print(
        "lint_messages: 下一步：把 script/ 的執行期輸出改成中文（ADR-00000028、#108），"
        "改完這份清單會自己縮短",
        file=sys.stderr,
    )

raise SystemExit(1 if failures else 0)
PY
}

main "$@"
