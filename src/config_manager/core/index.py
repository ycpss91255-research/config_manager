"""core/index — 參數索引與搜尋（PDF §3.3）。

純邏輯，不做 I/O（ADR-00000011）：index 收已解析的資料，回 (uid, 參數路徑, 值)。
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
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


# 搜尋範圍（用語依 CONTEXT / UI-ELEMENTS；設計 §7.4.2 的五檔）。
SCOPE_CONFIG = "config 名稱"
SCOPE_TARGET = "目標路徑"
SCOPE_NAME = "參數名稱"
SCOPE_VALUE = "參數值"
SCOPE_ALL = "全部"

# 允許的搜尋範圍。守衛與訊息都讀這一份，所以「允許哪些」只有一個定義——訊息說
# 允許五個、程式卻只認四個，是這種對照最容易長出來的分歧。
ALLOWED_SCOPES = (SCOPE_CONFIG, SCOPE_TARGET, SCOPE_NAME, SCOPE_VALUE, SCOPE_ALL)
# 只有 `search`（索引內搜尋）認得的三檔；config 名稱與目標路徑是條目層級的，走 `search_configs`。
_INDEX_SCOPES = (SCOPE_NAME, SCOPE_VALUE, SCOPE_ALL)


def _as_text(value: Any) -> str:
    return str(value)


def search(index_entries: list[Entry], query: str, scope: str) -> list[Entry]:
    """在索引中依範圍搜尋，回傳命中的 (uid, 參數路徑, 值)。

    查無結果回空清單，不是例外。**範圍本身不在允許集合內則丟 UnknownScope**——
    那不是「查無結果」，是呼叫端給了一個不存在的範圍，兩者該做的處置相反。
    回同一個空清單的話，使用者看到「找不到符合的參數」，會以為那個參數不存在，
    然後去別的地方找（不變式 2、設計原則 N-2）。
    """
    _check_scope(scope, _INDEX_SCOPES)
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


def _check_scope(scope: str, allowed_scopes: tuple[str, ...]) -> None:
    if scope not in allowed_scopes:
        allowed = "／".join(allowed_scopes)
        raise UnknownScope(
            f"搜尋範圍非允許值：「{scope}」不是可用的搜尋範圍，允許值為 {allowed}。"
            f"下一步：改成其中一個；這不是查無結果，所以不會回空清單"
        )


@dataclass(frozen=True)
class Hit:
    """一份 config 的命中：哪些範圍命中（`matched`）、命中了哪些參數（`params`，路徑與值）。

    以 config 為單位而不是逐項目：介面的搜尋結果是左側樹的過濾（哪些 config 留下），展開時再定位
    到命中的那一列（§7.4.2）。
    """

    uid: str
    matched: tuple[str, ...]
    params: tuple[tuple[str, Any], ...] = field(default_factory=tuple)


def search_configs(
    configs: Iterable[tuple[str, str, str]], index_entries: list[Entry], query: str, scope: str
) -> list[Hit]:
    """五檔範圍的搜尋（§7.4.2）：config 名稱、目標路徑（條目層級）、參數名稱、參數值
    （索引層級）、全部（四者聯集）。`configs` 是 (uid, name, target)。回每份命中的 config 一筆
    Hit，依 configs 順序。

    指定範圍的用途是降噪：搜尋 `camera` 在「全部」下會同時命中 config 名稱與參數名稱，指定後只看
    想看的那類——所以指定範圍時其他範圍的命中一律排除，不是加權排序。
    """
    _check_scope(scope, ALLOWED_SCOPES)
    want = set(ALLOWED_SCOPES[:-1]) if scope == SCOPE_ALL else {scope}
    hits = [_hit_for(config, index_entries, query, want) for config in configs]
    return [hit for hit in hits if hit is not None]


def _hit_for(
    config: tuple[str, str, str], index_entries: list[Entry], query: str, want: set[str]
) -> Hit | None:
    """一份 config 在 `want` 這幾個範圍下的命中；一個範圍都沒中回 None。"""
    uid, name, target = config
    matched: list[str] = []
    if SCOPE_CONFIG in want and query in name:
        matched.append(SCOPE_CONFIG)
    if SCOPE_TARGET in want and query in target:
        matched.append(SCOPE_TARGET)
    params: list[tuple[str, Any]] = []
    for entry_uid, path, value in index_entries:
        if entry_uid != uid:
            continue
        by_name = SCOPE_NAME in want and query in path
        by_value = SCOPE_VALUE in want and query in _as_text(value)
        if by_name and SCOPE_NAME not in matched:
            matched.append(SCOPE_NAME)
        if by_value and SCOPE_VALUE not in matched:
            matched.append(SCOPE_VALUE)
        if by_name or by_value:
            params.append((path, value))
    if not matched:
        return None
    return Hit(uid=uid, matched=tuple(matched), params=tuple(params))
