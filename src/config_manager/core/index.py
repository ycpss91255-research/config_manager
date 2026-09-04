"""core/index — 參數索引與搜尋（PDF §3.3）。

純邏輯，不做 I/O（ADR-00000011）：index 收已解析的資料，回 (uid, 參數路徑, 值)。
"""

from typing import Any

from config_manager.core.errors import UnknownScope

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

# 允許的搜尋範圍。守衛與訊息都讀這一份，所以「允許哪些」只有一個定義——訊息說
# 允許三個、程式卻只認兩個，是這種對照最容易長出來的分歧。
ALLOWED_SCOPES = (SCOPE_NAME, SCOPE_VALUE, SCOPE_ALL)


def _as_text(value: Any) -> str:
    return str(value)


def search(index_entries: list[Entry], query: str, scope: str) -> list[Entry]:
    """在索引中依範圍搜尋，回傳命中的 (uid, 參數路徑, 值)。

    查無結果回空清單，不是例外。**範圍本身不在允許集合內則丟 UnknownScope**——
    那不是「查無結果」，是呼叫端給了一個不存在的範圍，兩者該做的處置相反。
    回同一個空清單的話，使用者看到「找不到符合的參數」，會以為那個參數不存在，
    然後去別的地方找（不變式 2、設計原則 N-2）。
    """
    if scope not in ALLOWED_SCOPES:
        allowed = "／".join(ALLOWED_SCOPES)
        raise UnknownScope(
            f"搜尋範圍非允許值：「{scope}」不是可用的搜尋範圍，允許值為 {allowed}。"
            f"下一步：改成其中一個；這不是查無結果，所以不會回空清單"
        )
    if scope == SCOPE_NAME:
        return [entry for entry in index_entries if query in entry[1]]
    if scope == SCOPE_VALUE:
        return [entry for entry in index_entries if query in _as_text(entry[2])]
    # 只剩 SCOPE_ALL——上面的守衛保證 scope 是三者之一，所以這裡不再有「都不是」
    # 的出口可以掉進去。
    return [
        entry
        for entry in index_entries
        if query in entry[1] or query in _as_text(entry[2])
    ]


def reindex(index_entries: list[Entry], uid: str, data: dict[str, Any]) -> list[Entry]:
    """重新索引一份 config：先移除該 uid 的舊項目，再加入重新展平的結果。

    更新最容易漏（既有項目未清就重加）。此函式一步完成移除＋加入。
    """
    kept = [entry for entry in index_entries if entry[0] != uid]
    return kept + index(uid, data)


def unindex(index_entries: list[Entry], uid: str) -> list[Entry]:
    """解除納管：移除該 uid 的所有索引項目，其他 uid 不受影響。"""
    return [entry for entry in index_entries if entry[0] != uid]
