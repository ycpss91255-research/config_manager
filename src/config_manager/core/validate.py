"""core/validate — T3 驗證。第 1 層（#16）在這裡，第 2 層（#39）在 `core/schema_check`。

第 1 層是**硬擋**（CONTEXT.md「驗證層級」）：能否解析，以及所有值是否符合正規形式白名單。
`check(text, fmt) -> [Problem]`，空清單＝通過。每個問題帶行號與修正建議——三要素的
「在哪裡」與「該怎麼改」；「檔案」由呼叫端補上，核心層不知道路徑（CLAUDE.md 分層）。

依格式套有意義的規則（#16 的 D3，寫在 TEST-PLAN T3）：
- yaml：全套——2 空白縮排（拒 Tab／非 2 的倍數）、尾隨空白、重複 key、正規形式白名單。
- toml／json／ini：能否解析＋重複 key＋尾隨空白。**縮排規則不套**——對這三者縮排沒有語意。
- raw：不解析，沒有可驗的東西，一律通過。

重複 key **列出所有出現行號**（不是只報第二次）。底層解析器做不到這件事：ruamel／tomlkit／
configobj 遇重複直接拋錯、只帶第二次出現的行；json.loads 更是靜默取後者。所以這裡自己做
一次依格式範圍（yaml 同一父層、toml 同一表、json 同一物件、ini 同一區段）的原始掃描。

逐行掃描是刻意選的鈍工具（與 core/inference 同一個理由）：行號只有原文拿得到。
只按 `\n` 切行（不用 str.splitlines()），行號才與解析器和編輯器一致（#257）。

第 2 層（schema，#39）在 `core/schema_check`：`check` 收到 `schema` 才跑，問題與第 1 層的
併在同一張清單。第 3 層（跨欄位規則，#41）在 `core/rules`：`check` 收到 `rules` 才跑，回的是
警告（可填理由略過，#42）。核心層不做 I/O。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from config_manager.core.errors import SyntaxParse
from config_manager.core.parse import parse
from config_manager.core.problem import ERROR, WARNING, Problem
from config_manager.core.rules import Rules, check_rules
from config_manager.core.schema_check import check_schema

__all__ = ["ERROR", "WARNING", "Problem", "check"]


def check(
    text: str,
    fmt: str,
    rules: Rules | None = None,  # 第 3 層（#41）：給了就依它檢查，回的是警告
    schema: Mapping[str, object] | None = None,  # 第 2 層（#39）：給了就依它檢查
) -> list[Problem]:
    """第 1 層驗證，給了 `schema` 再加第 2 層、給了 `rules` 再加第 3 層。回傳依行號排序的問題
    清單，空清單＝通過。第 3 層的問題是警告（`severity` 為 warning、帶 `rule`），不是硬擋。"""
    if fmt == "raw":
        return []

    lines = text.split("\n")
    # 重複 key 的掃描要在 parse 之前：ruamel／tomlkit／configobj 遇重複 key 會直接拋錯、且只帶
    # 第二次出現的行，若先 parse 就永遠走不到「列出所有出現行號」這條契約（json.loads 則是
    # 靜默取後者，非掃不可）。
    duplicates = _duplicate_keys(lines, fmt)
    try:
        parse(text, fmt)
    except SyntaxParse as error:
        # 解析不下去就沒有值可以驗。若原因正是重複 key，回掃描結果（全部行號）而非只指一行
        # 的解析錯誤；其餘語法錯誤照回，行號沿用解析器的。
        return duplicates or [Problem(error.line, str(error), "修正該行的語法後重試")]

    problems = _trailing_whitespace(lines)
    problems += duplicates
    if fmt == "yaml":
        problems += _yaml_indentation(lines)
        problems += _yaml_canonical_forms(lines)
    if schema is not None:
        # 解析得過才有值可以驗；第 1 層的其他問題（尾隨空白、正規形式）不影響第 2 層照跑，
        # 兩層的問題一次列完，不必修一輪才看得到下一輪。
        problems += check_schema(text, fmt, schema)
    if rules is not None:
        problems += check_rules(text, fmt, rules)
    return sorted(problems, key=lambda p: (p.line or 0, p.message))


# ── 尾隨空白（全部格式）─────────────────────────────────────────────────────

_TRAILING = re.compile(r"[ \t]+$")


def _trailing_whitespace(lines: list[str]) -> list[Problem]:
    return [
        Problem(
            number,
            f"第 {number} 行結尾有多餘的空白",
            "刪掉行尾的空白或 Tab",
        )
        for number, line in enumerate(lines, start=1)
        if _TRAILING.search(line.rstrip("\r"))
    ]


# ── 重複 key（依格式範圍）──────────────────────────────────────────────────

_Scope = tuple[object, ...]


def _duplicate_keys(lines: list[str], fmt: str) -> list[Problem]:
    seen = _key_occurrences(lines, fmt)
    problems = []
    for (_scope, key), numbers in seen.items():
        if len(numbers) > 1:
            listed = "、".join(str(n) for n in numbers)
            problems.append(
                Problem(
                    numbers[0],
                    f"key「{key}」重複出現於第 {listed} 行",
                    "只保留一個；後者會靜默覆蓋前者，留下哪一個要由人決定",
                    lines=tuple(numbers),
                )
            )
    return problems


def _key_occurrences(lines: list[str], fmt: str) -> dict[tuple[_Scope, str], list[int]]:
    if fmt == "yaml":
        return _yaml_keys(lines)
    if fmt == "toml":
        return _toml_keys(lines)
    if fmt == "json":
        return _json_keys("\n".join(lines))
    return _ini_keys(lines)


def _record(
    seen: dict[tuple[_Scope, str], list[int]], scope: _Scope, key: str, number: int
) -> None:
    seen.setdefault((scope, key), []).append(number)


# yaml：範圍是同一個父層。以縮排堆疊追蹤父鍵；`- ` 清單項目各自是一個範圍（不同項目同名
# 不是重複）；區塊純量（`|`／`>`）的內文是字面文字，不掃（同 core/inference，#219）。
_YAML_KEY = re.compile(
    r"^(?P<indent>[ \t]*)(?P<item>- )?(?P<key>[^\s#'\"\[{&*!|>-][^:#]*?):(?:\s|$)"
)
_YAML_BLOCK = re.compile(r":\s*[|>][+-]?\d*\s*(?:#.*)?$")
# 破折號單獨一行：項目的鍵從下一行開始。
_YAML_BARE_ITEM = re.compile(r"^[ \t]*-\s*(?:#.*)?$")


def _yaml_keys(lines: list[str]) -> dict[tuple[_Scope, str], list[int]]:
    seen: dict[tuple[_Scope, str], list[int]] = {}
    stack: list[tuple[int, object]] = []  # (縮排, 鍵或清單項目標記)
    block_indent: int | None = None
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None
        match = _YAML_KEY.match(line)
        on_item_line = match is not None and bool(match.group("item"))
        if on_item_line or _YAML_BARE_ITEM.match(line):
            _open_item(stack, indent, number)
        if match is None:
            continue
        # `- key: v` 的鍵落在破折號之後兩格；項目的其餘鍵寫在同一個縮排。
        key_indent = indent + 2 if on_item_line else indent
        while stack and stack[-1][0] >= key_indent:
            stack.pop()
        key = match.group("key").strip()
        _record(seen, tuple(name for _, name in stack), key, number)
        stack.append((key_indent, key))
        if _YAML_BLOCK.search(line):
            block_indent = indent
    return seen


def _open_item(stack: list[tuple[int, object]], dash_indent: int, number: int) -> None:
    """清單項目開始：收掉比破折號深的層與同一層的上一個項目，放上這個項目自己的範圍標記。

    標記留在堆疊上，直到這個項目結束——項目裡**每一個**鍵（不只緊接在 `- ` 後面那個）都落在
    它的範圍裡，不同項目的同名鍵才不會被當成重複。與破折號同縮排的**鍵**不收：那是清單的父鍵
    （YAML 允許項目與父鍵寫在同一層），項目仍在它底下。
    """
    while stack and (
        stack[-1][0] > dash_indent
        or (stack[-1][0] == dash_indent and isinstance(stack[-1][1], tuple))
    ):
        stack.pop()
    stack.append((dash_indent, ("item", number)))


# toml：範圍是同一個表。`[[陣列表]]` 每次出現都是新的一筆，以表頭行號區分。
_TOML_TABLE = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")
_TOML_KEY = re.compile(r"^\s*(?P<key>[A-Za-z0-9_.\-]+|\"[^\"]*\"|'[^']*')\s*=")


def _toml_keys(lines: list[str]) -> dict[tuple[_Scope, str], list[int]]:
    seen: dict[tuple[_Scope, str], list[int]] = {}
    scope: _Scope = ()
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        header = _TOML_TABLE.match(line)
        if header:
            name = header.group(2)
            scope = (name, number) if header.group(1) == "[[" else (name,)
            continue
        match = _TOML_KEY.match(line)
        if match:
            _record(seen, scope, match.group("key"), number)
    return seen


# json：範圍是同一個物件。json.loads 對重複 key 靜默取後者，所以要自己走一遍字元：
# 追蹤字串、`{`／`}` 的巢狀，字串後接 `:` 者是鍵。每個 `{` 以其起點位置當物件識別。
def _json_keys(text: str) -> dict[tuple[_Scope, str], list[int]]:
    seen: dict[tuple[_Scope, str], list[int]] = {}
    objects: list[object] = []
    line = 1
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n":
            line += 1
        elif char == "{":
            objects.append(index)
        elif char == "}":
            if objects:
                objects.pop()
        elif char == '"':
            end = _json_string_end(text, index)
            literal = text[index + 1 : end]
            after = _skip_space(text, end + 1)
            if after < len(text) and text[after] == ":" and objects:
                _record(seen, (objects[-1],), literal, line)
            line += text.count("\n", index, end)
            index = end
        index += 1
    return seen


def _json_string_end(text: str, start: int) -> int:
    """回傳從 `start`（開頭引號）起的字串結尾引號位置；處理反斜線跳脫。"""
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index
        index += 1
    return len(text) - 1


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


# ini：範圍是同一個區段。
_INI_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*(?:[#;].*)?$")
_INI_KEY = re.compile(r"^\s*(?P<key>[^=:\[#;\s][^=:]*?)\s*[=:]")


def _ini_keys(lines: list[str]) -> dict[tuple[_Scope, str], list[int]]:
    seen: dict[tuple[_Scope, str], list[int]] = {}
    scope: _Scope = ()
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        section = _INI_SECTION.match(line)
        if section:
            scope = (section.group(1).strip(),)
            continue
        match = _INI_KEY.match(line)
        if match:
            _record(seen, scope, match.group("key"), number)
    return seen


# ── yaml 縮排：只接受 2 個空白；拒 Tab、空白與 Tab 混用、非 2 的倍數 ───────────


def _yaml_indentation(lines: list[str]) -> list[Problem]:
    problems = []
    block_indent: int | None = None
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        leading = line[: len(line) - len(line.lstrip(" \t"))]
        indent = len(leading)
        if block_indent is not None:
            if indent > block_indent:
                continue  # 區塊純量的字面內文，縮排任意
            block_indent = None
        if "\t" in leading:
            problems.append(
                Problem(number, f"第 {number} 行以 Tab 縮排", "改成空白縮排，每層 2 個空白")
            )
        elif indent % 2 != 0:
            problems.append(
                Problem(
                    number,
                    f"第 {number} 行縮排 {indent} 個空白，不是 2 的倍數",
                    "每層縮排 2 個空白",
                )
            )
        if _YAML_BLOCK.search(line):
            block_indent = indent
    return problems


# ── yaml 正規形式白名單 ──────────────────────────────────────────────────────
# 比對順序有意義（bool 先於 int 先於 float 先於 null）。資料，不是邏輯：多認一種寫法就加一列。
_CANONICAL: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (
        re.compile(r"^(?:y|n|yes|no|on|off)$", re.IGNORECASE),
        "布林只接受 true／false",
        "改寫成 true 或 false；若本意是字串，加上引號",
        ERROR,
    ),
    (
        re.compile(r"^0\d+$"),
        "整數不接受前導零（會被讀成八進位或十進位而各家不同）",
        "改寫成十進位；若是檔案權限之類的代號，加上引號成字串",
        ERROR,
    ),
    (
        re.compile(r"^0x[0-9a-fA-F]+$"),
        "整數不接受十六進位寫法",
        "改寫成十進位",
        ERROR,
    ),
    (
        re.compile(r"^[+-]?\d+(?:_\d+)+$"),
        "整數不接受底線分隔",
        "把底線拿掉",
        ERROR,
    ),
    (
        re.compile(r"^\+\d+$"),
        "整數不接受前置的加號",
        "把加號拿掉",
        ERROR,
    ),
    (
        re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)[eE][+-]?\d+$"),
        "浮點數不接受科學記號",
        "改寫成一般小數",
        ERROR,
    ),
    (
        re.compile(r"^[+-]?\.\d+$"),
        "浮點數不接受省略整數部分（如 .5）",
        "補上整數部分，如 0.5",
        ERROR,
    ),
    (
        re.compile(r"^[+-]?\d+\.$"),
        "浮點數不接受省略小數部分（如 1.）",
        "補上小數部分，如 1.0",
        ERROR,
    ),
    (
        re.compile(r"^[+-]?\.?(?:inf|nan)$", re.IGNORECASE),
        "不接受 inf／nan",
        "改用具體的數值，或以字串表達並在使用端處理",
        ERROR,
    ),
    (
        re.compile(r"^\d+\.\d+0$"),
        "尾隨零的小數（如 1.10）解析後尾數會退掉，易與版本號字串混淆",
        "若是版本號請加引號成字串；若是數值請去掉尾隨的 0",
        WARNING,
    ),
    (
        re.compile(r"^(?:~|Null|NULL)$"),
        "null 只接受小寫的 null",
        "改寫成 null",
        ERROR,
    ),
)

_YAML_VALUE = re.compile(r"^(?:[^\s#'\"\[{&*!|>-][^:#]*?:|-)\s*(?P<value>.*?)\s*$")


def _yaml_canonical_forms(lines: list[str]) -> list[Problem]:
    problems = []
    block_indent: int | None = None
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None
        value = _written_scalar(stripped)
        if value is None:
            if _YAML_BLOCK.search(line):
                block_indent = indent
            continue
        problem = _canonical_problem(number, value)
        if problem is not None:
            problems.append(problem)
    return problems


def _written_scalar(stripped: str) -> str | None:
    """這一行寫下的裸純量；沒有值、已加引號、或是集合／區塊／錨點等結構時回 None。"""
    body = re.split(r"\s#", stripped, maxsplit=1)[0].strip()
    match = _YAML_VALUE.match(body)
    if match is None:
        return None
    value = match.group("value")
    if not value or value[0] in "\"'[{|>&*!":
        return None
    return value


def _canonical_problem(number: int, value: str) -> Problem | None:
    for pattern, what, suggestion, severity in _CANONICAL:
        if pattern.match(value):
            return Problem(number, f"第 {number} 行的值「{value}」：{what}", suggestion, severity)
    return None
