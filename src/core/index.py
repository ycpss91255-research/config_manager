"""core/index — 參數索引與搜尋（PDF §3.3）。

純邏輯，不做 I/O（ADR-00000011）：index 收已解析的資料，回 (uid, 參數路徑, 值)。
"""

from typing import Any

Entry = tuple[str, str, Any]


def index(uid: str, data: dict[str, Any]) -> list[Entry]:
    """把一份 config 的已解析資料展平為 (uid, 參數路徑, 值) 清單；只索引葉值。"""
    return [(uid, path, value) for path, value in _flatten_value(data, "")]


def _flatten_value(value: Any, path: str) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        items: list[tuple[str, Any]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            items.extend(_flatten_value(child, child_path))
        return items
    if isinstance(value, list):
        items = []
        for i, elem in enumerate(value):
            items.extend(_flatten_value(elem, f"{path}[{i}]"))
        return items
    return [(path, value)]


# 搜尋範圍（用語依 CONTEXT / UI-ELEMENTS）。
SCOPE_NAME = "參數名稱"
SCOPE_VALUE = "參數值"
SCOPE_ALL = "全部"


def _as_text(value: Any) -> str:
    return str(value)


def search(index_entries: list[Entry], query: str, scope: str) -> list[Entry]:
    """在索引中依範圍搜尋，回傳命中的 (uid, 參數路徑, 值)。查無結果回空清單。"""
    if scope == SCOPE_NAME:
        return [entry for entry in index_entries if query in entry[1]]
    if scope == SCOPE_VALUE:
        return [entry for entry in index_entries if query in _as_text(entry[2])]
    if scope == SCOPE_ALL:
        return [
            entry
            for entry in index_entries
            if query in entry[1] or query in _as_text(entry[2])
        ]
    return []


def reindex(index_entries: list[Entry], uid: str, data: dict[str, Any]) -> list[Entry]:
    """重新索引一份 config：先移除該 uid 的舊項目，再加入重新展平的結果。

    更新最容易漏（既有項目未清就重加）。此函式一步完成移除＋加入。
    """
    kept = [entry for entry in index_entries if entry[0] != uid]
    return kept + index(uid, data)


def unindex(index_entries: list[Entry], uid: str) -> list[Entry]:
    """解除納管：移除該 uid 的所有索引項目，其他 uid 不受影響。"""
    return [entry for entry in index_entries if entry[0] != uid]
