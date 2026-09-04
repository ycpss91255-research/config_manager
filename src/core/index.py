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
