"""core/index — 參數索引與搜尋（PDF §3.3）。

純邏輯，不做 I/O（ADR-00000011）：index 收已解析的資料，回 (uid, 參數路徑, 值)。
"""

from typing import Any

Entry = tuple[str, str, Any]


def index(uid: str, data: dict[str, Any]) -> list[Entry]:
    """把一份 config 的已解析資料展平為 (uid, 參數路徑, 值) 清單；只索引葉值。"""
    return [(uid, path, value) for path, value in _flatten(data)]


def _flatten(data: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            items.extend(_flatten(value, path))
        else:
            items.append((path, value))
    return items
