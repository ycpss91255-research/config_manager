"""core/inference — T12 型別推斷與歧義偵測.

`infer_types(資料)` 走訪解析後的資料，回傳「欄位路徑 → 型別」。型別是**當前值呈現的
型別**，作為日後修改時的一致性依據（#9）——不是作者原意，那無從得知，人工指定存在的
理由正是推斷做不到這件事（T12「不測」那一段）。

`draft_schema(型別對照)` 把那張對照轉成 JSON Schema 骨架（#38）。骨架**只鎖型別**：不寫
必填、不擋多出來的 key、不寫範圍——現場多加一個參數後「納入現況」仍要走得通，要更嚴由
開發者自己編輯 schema 檔。看不出型別的欄位（空陣列的元素、null、日期）不鎖，不是猜一個。
人工指定型別是 #285。

欄位路徑的寫法：巢狀以 `.` 相接（`outer.inner`），陣列元素以 `[]` 標記
（`items[]`、`servers[].host`）。這樣一個路徑字串就定位得到任一層的值，納管介面
逐欄位顯示解析型別時直接用它當鍵。

`find_ambiguous(原文, format)` 回答同一個問題的另一半（#10）：**這個值讀起來有沒有
兩種意思**。`no` 是布林還是字串、`0755` 是八進位還是檔案權限、`1.10` 的尾數 0 會不會
消失——推斷給得出一個答案，但給不出「這個答案唯一」。列出來讓人確認，**不阻擋匯入、
也不自動修正**：靜默改寫消掉的是訊號，不是麻煩（不變式 2）。

核心層不做 I/O：資料由呼叫端（`core/parse` 的解析結果）傳進來（CLAUDE.md 分層）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_UNKNOWN = "unknown"


def infer_types(data: object) -> dict[str, str]:
    types: dict[str, str] = {}
    _walk(data, "", types)
    return types


def _escape_key(key: str) -> str:
    r"""把 key 裡的字面 `.` 跳脫成 `\.`，才不會與路徑分隔用的 `.` 撞號（#219）。

    路徑鍵以 `.` 相接扁平化；key 本身含 `.`（如 `a.b`）不跳脫的話，會與巢狀 `a`→`b`
    產生的路徑 `a.b` 撞成同一鍵、靜默覆蓋、遺失型別。消費端（前端型別樹）以「未跳脫的
    點」切段、並把 `\.` 還原成 `.`。key 含字面反斜線是 config 幾乎不會出現的邊界，不在此處理。
    """
    return key.replace(".", "\\.")


def _walk(value: object, path: str, out: dict[str, str]) -> None:
    if path:
        out[path] = _type_name(value)

    if isinstance(value, Mapping):
        for key, item in value.items():
            escaped = _escape_key(str(key))
            child = f"{path}.{escaped}" if path else escaped
            _walk(item, child, out)
        return

    # str／bytes 也是 Sequence，但它們是純量，不是陣列。
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        element = f"{path}[]"
        items = list(value)
        if not items:
            # 空陣列推不出元素型別——標示為未知，不是猜一個（T12）。
            out[element] = _UNKNOWN
            return
        # 每個元素都看：只看第一個的話，`[0, 0.5]` 會記成 int，骨架就擋掉 0.5（#38）。
        # 同一路徑在不同元素上型別不同時由 `_unify` 決定——能說是同一種就說，說不了標未知。
        merged: dict[str, str] = {}
        for item in items:
            found: dict[str, str] = {}
            _walk(item, element, found)
            for key, name in found.items():
                merged[key] = _unify(merged.get(key), name)
        out.update(merged)


def _unify(seen: str | None, name: str) -> str:
    """同一欄位在陣列的不同元素上出現的兩個型別，合成一個。

    一樣就沿用；整數與浮點混用是浮點陣列（`[0, 0.5]`）；其餘對不起來的標未知——不拿其中
    一個代表全部。
    """
    if seen is None or seen == name:
        return name
    if {seen, name} == {"int", "float"}:
        return "float"
    return _UNKNOWN


# 比對順序就是這張表的順序，而順序是有意義的：**bool 必須排在 int 前面**，因為 Python
# 的 bool 是 int 的子類別——反過來的話 True 會被記成 int，型別一致性檢查從此對布林欄位
# 失效。資料，不是邏輯：多認一種型別就加一列。
_BY_TYPE: tuple[tuple[type, str], ...] = (
    (bool, "bool"),
    (int, "int"),
    (float, "float"),
    (str, "string"),
    (Mapping, "dict"),
)


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    for kind, name in _BY_TYPE:
        if isinstance(value, kind):
            return name
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return "list"
    return _UNKNOWN


# ── schema 骨架（T12 的 draft_schema，#38）────────────────────────────────────

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

# 推斷的型別名 → JSON Schema 的型別名。不在表上的（null、unknown）不鎖型別：當下的值看
# 不出作者要什麼，鎖了就是替人猜。資料，不是邏輯。
_JSON_TYPES: dict[str, str] = {
    "bool": "boolean",
    "int": "integer",
    "float": "number",
    "string": "string",
    "dict": "object",
    "list": "array",
}

# 路徑裡「沒被跳脫的點」才是階層分隔；`\.` 是 key 本身的字面點（#219）。
_SEGMENT_DOT = re.compile(r"(?<!\\)\.")


def draft_schema(types: Mapping[str, str]) -> dict[str, object]:
    """由「欄位路徑 → 型別」產生 JSON Schema 骨架。`types` 是 `infer_types` 的結果（前序：
    父層先於子層），頂層是物件。
    """
    root: dict[str, object] = {"$schema": _SCHEMA_DIALECT, "type": "object"}
    nodes: dict[str, dict[str, object]] = {"": root}
    for path, name in types.items():
        node: dict[str, object] = {}
        if name in _JSON_TYPES:
            node["type"] = _JSON_TYPES[name]
        parent, key = _split_parent(path)
        if key is None:
            nodes[parent]["items"] = node
        else:
            properties = nodes[parent].get("properties")
            if not isinstance(properties, dict):
                properties = nodes[parent]["properties"] = {}
            properties[key] = node
        nodes[path] = node
    return root


def _split_parent(path: str) -> tuple[str, str | None]:
    """把欄位路徑拆成（父層路徑, 自己的 key）；陣列元素（`…[]`）沒有 key，回 None。"""
    if path.endswith("[]"):
        return path[:-2], None
    dots = [match.start() for match in _SEGMENT_DOT.finditer(path)]
    # `a[].b` 的父層是 `a[]`；最後一個階層分隔之前的整段就是父層路徑。
    parent, leaf = (path[: dots[-1]], path[dots[-1] + 1 :]) if dots else ("", path)
    return parent, leaf.replace("\\.", ".")


# ── 歧義偵測（T12 的 find_ambiguous，#10）─────────────────────────────────────


@dataclass(frozen=True)
class Ambiguity:
    """一個讀起來有兩種意思的值。

    `line` 是 1 起算的行號、`value` 是**原樣**的值文字、`readings` 是它可以被讀成
    哪幾種。回報原樣的值而不是改寫後的建議——**不自動修正**是 #10 的驗收條件，也是
    不變式 2：靜默改寫消掉的是訊號，不是麻煩。
    """

    line: int
    value: str
    readings: tuple[str, ...]


# 歧義的寫法與它們可能的讀法。資料，不是邏輯：多認一種寫法就加一列。
# `true`／`false` 不在表上——兩個 YAML 版本都讀成布林，沒有歧義。
_AMBIGUOUS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (
        re.compile(r"^(?:y|n|yes|no|on|off)$", re.IGNORECASE),
        ("布林（YAML 1.1 讀成 true／false）", "字串（YAML 1.2 讀成原文）"),
    ),
    (
        re.compile(r"^0\d+$"),
        ("八進位整數（YAML 1.1）", "十進位整數", "字串（例如檔案權限 0755）"),
    ),
    (
        # 小數點後至少兩位且結尾是 0：`1.10` 讀成浮點數之後尾數的 0 會消失。
        # `1.0` 不在此列——它讀成浮點數再寫回來還是 `1.0`，沒有損失。
        re.compile(r"^\d+\.\d+0$"),
        ("浮點數（尾數的 0 會消失）", "字串（保留原樣）"),
    ),
)

# 只有 yaml 有這類歧義：json／toml 的型別由寫法決定（引號、字面值），ini 全是字串，
# raw 根本不解析。其餘格式回空清單，不是「還沒支援」，是那裡不存在這個問題。
_AMBIGUOUS_FORMATS = frozenset({"yaml"})


# 區塊純量標頭：`|`／`>`，可帶 chomping（`-`／`+`）與明示縮排指示（數字）。其後縮排更深
# 的行是**字面文字**，不是 YAML 的鍵值——不能拿去比對歧義（#219）。
_BLOCK_HEADER = re.compile(r"^[|>][+-]?\d*$")

# flow 集合（`[…]`／`{…}`）內文的粗略切詞：以括號、逗號、冒號、空白切。刻意不是真正的 flow
# 解析器——那要處理巢狀、引號內逗號、跨行 flow（後者會直接打破本模組的逐行模型）。
_FLOW_TOKENS = re.compile(r"[\[\]{}(),:\s]+")
_FLOW_READINGS = (
    "此行的 flow 集合可能含跨版本歧義值（如 no／yes／0755／尾數為 0 的小數）",
    "請人工確認整行——工具不逐元素解析 flow 集合（#258）",
)


def _flow_has_ambiguous_token(value: str) -> bool:
    """flow 集合內文（粗略以分隔符切）是否含任一疑似跨版本歧義的 token。

    刻意 fail-closed 且寧可過度標記：加引號的 token 因帶引號不吻合錨定 pattern 而自然被排除
    （作者已表明是字串），未加引號的 no／0755 等會命中。跨行／巢狀 flow 也以「多標一次確認」
    化解，而不是誤解析（不變式 4：模糊就要人工確認，#258）。
    """
    return any(
        token and any(pattern.match(token) for pattern, _ in _AMBIGUOUS)
        for token in _FLOW_TOKENS.split(value)
    )


def _scalar_ambiguity(number: int, value: str) -> Ambiguity | None:
    """一個已寫下的值的歧義（沒有則 None）。呼叫端已排除空行、只有鍵、引號與區塊標頭。

    flow 集合（`[…]`／`{…}`）整段拿去比對錨定 pattern 永遠不吻合，內部的 no／0755 因此漏標
    （#258）；不逐元素精準解析（見 _flow_has_ambiguous_token），改 fail-closed 標整行請人工確認。
    """
    if value.startswith(("[", "{")):
        if _flow_has_ambiguous_token(value):
            return Ambiguity(line=number, value=value, readings=_FLOW_READINGS)
        return None
    for pattern, readings in _AMBIGUOUS:
        if pattern.match(value):
            return Ambiguity(line=number, value=value, readings=readings)
    return None


def find_ambiguous(text: str, fmt: str) -> list[Ambiguity]:
    if fmt not in _AMBIGUOUS_FORMATS:
        return []

    found: list[Ambiguity] = []
    block_indent: int | None = None  # 不在區塊純量內時為 None；否則為標頭行的縮排
    # 只按 \n（含 \r\n）切行，不用 str.splitlines()——後者還會在 \x0b\x0c\x1c\x1d\x1e\x85 等處斷行，
    # 使行號比解析器（ruamel／json／configobj，皆按 \n）與編輯器所見多算，回報的歧義行號因而偏移
    # （#257）。rstrip 掉 \r 讓 CRLF 檔的行內容與 LF 檔一致。
    for number, raw in enumerate(text.split("\n"), start=1):
        line = raw.rstrip("\r")
        if block_indent is not None:
            if not line.strip():
                continue  # 空行留在區塊內：config 的腳本／內嵌文字常含空行（#219）
            if _indent(line) > block_indent:
                continue  # 縮排更深＝區塊純量的字面內文，不掃
            block_indent = None  # 縮排回到標頭層級以下：區塊結束，這一行照常處理

        value = _written_value(line)
        if value is None:
            continue
        if _BLOCK_HEADER.match(value):
            block_indent = _indent(line)  # 這行的值是區塊標頭，其後縮排更深者為字面內文
            continue
        ambiguity = _scalar_ambiguity(number, value)
        if ambiguity is not None:
            found.append(ambiguity)
    return found


def _indent(line: str) -> int:
    """行首的空白數（區塊純量以縮排界定內文範圍）。"""
    return len(line) - len(line.lstrip())


def _written_value(line: str) -> str | None:
    """這一行寫下的純量值；空行、註解、只有鍵、或已加引號時回 None。

    逐行掃描是刻意選的鈍工具：行號只有原文拿得到（json／tomlkit／configobj 的解析器
    都不給行號），而「指名行號」是 #10 的驗收條件。代價寫在 T12 那一段。
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    # 行內註解：YAML 規定 # 前面要有空白，所以以「空白＋#」切開就夠。
    body = re.split(r"\s#", stripped, maxsplit=1)[0].strip()

    is_item = body.startswith("- ")
    if is_item:
        body = body[2:].strip()
    if ":" in body:
        body = body.split(":", 1)[1].strip()
    elif not is_item:
        # 既不是清單項目也不是「鍵: 值」——這一行沒有寫下純量值。
        return None

    if not body:
        return None
    # 已經加引號就沒有歧義——作者已經表明它是字串。
    if body[0] in "\"'":
        return None
    return body
