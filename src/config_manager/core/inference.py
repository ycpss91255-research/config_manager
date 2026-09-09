"""core/inference — T12 型別推斷.

`infer_types(資料)` 走訪解析後的資料，回傳「欄位路徑 → 型別」。型別是**當前值呈現的
型別**，作為日後修改時的一致性依據（#9）——不是作者原意，那無從得知，人工指定存在的
理由正是推斷做不到這件事（T12「不測」那一段）。

**v0.2.0 只做這一層，不產生完整 JSON Schema**（#9）：T12 的 `draft_schema` 與人工
指定型別留待之後的 milestone。

欄位路徑的寫法：巢狀以 `.` 相接（`outer.inner`），陣列元素以 `[]` 標記
（`items[]`、`servers[].host`）。這樣一個路徑字串就定位得到任一層的值，納管介面
逐欄位顯示解析型別時直接用它當鍵。

核心層不做 I/O：資料由呼叫端（`core/parse` 的解析結果）傳進來（CLAUDE.md 分層）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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
