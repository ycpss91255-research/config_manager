"""core/inference — T12 型別推斷與歧義偵測.

`infer_types(資料)` 走訪解析後的資料，回傳「欄位路徑 → 型別」。型別是**當前值呈現的
型別**，作為日後修改時的一致性依據（#9）——不是作者原意，那無從得知，人工指定存在的
理由正是推斷做不到這件事（T12「不測」那一段）。

**v0.2.0 只做這一層，不產生完整 JSON Schema**（#9）：T12 的 `draft_schema` 與人工
指定型別留待之後的 milestone。

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


def _walk(value: object, path: str, out: dict[str, str]) -> None:
    if path:
        out[path] = _type_name(value)

    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
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
        # 以第一個元素代表整個陣列的元素型別。異質陣列以第一個為準，這是刻意的
        # 簡化：T12 只要求「推斷出元素型別」，統一多種型別是 schema 的工作，而
        # schema 不在 v0.2.0（#9）。
        _walk(items[0], element, out)


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


def find_ambiguous(text: str, fmt: str) -> list[Ambiguity]:
    if fmt not in _AMBIGUOUS_FORMATS:
        return []

    found: list[Ambiguity] = []
    for number, line in enumerate(text.splitlines(), start=1):
        value = _written_value(line)
        if value is None:
            continue
        for pattern, readings in _AMBIGUOUS:
            if pattern.match(value):
                found.append(Ambiguity(line=number, value=value, readings=readings))
                break
    return found


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
