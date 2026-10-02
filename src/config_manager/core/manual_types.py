"""core/manual_types — 人工指定的型別（T12，#285、ADR-00000021）。

推斷只看得到當下的值：`timeout: 5` 被推斷成整數，作者要的可能是浮點——之後改成 `5.5` 就出事。
人工指定是更正推斷的那條路。**指定寫進該 config 的 schema**：它就是 schema 的一部分，不另開
override 檔（那會有兩個真實來源），於是自動隨版控、隨退版回退，第 2 層驗證也直接照它擋。

schema 裡怎麼分得出「人工指定」與「自動產生」：指定過的欄位多一個標記 `x-manual-type`，裡面
記著指定之前那個欄位的型別（`before`；原本沒有型別就是 null）。JSON Schema 會忽略不認得的
關鍵字，所以標記不影響驗證。**清除＝回到指定之前**：型別改回 `before`，標記拿掉；那份 schema
若是為了這次指定才建立、清完什麼都不剩，就整份不要（不留空殼擋住之後產生骨架）。逐欄位清除，
不動同一份 schema 的其他指定，也不動開發者在那個欄位上另外寫的範圍與說明。

核心層不做 I/O：schema 以解析後的 dict 傳進來、回新的 dict（不改傳入的那份）。
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping

from config_manager.core.errors import PathNotSpecifiable, TypeNotSpecified, UnknownTypeName
from config_manager.core.inference import draft_schema

MARK = "x-manual-type"

# 可指定的型別（API／推斷用的名字）→ JSON Schema 的型別名。容器（dict／list）不能指定。
_TO_JSON: dict[str, str] = {
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "string": "string",
}
_FROM_JSON = {json_name: name for name, json_name in _TO_JSON.items()}
TYPE_NAMES = tuple(_TO_JSON)

_SEGMENT_DOT = re.compile(r"(?<!\\)\.")
# 整份 schema 只剩這兩個鍵＝沒有任何規則，等同沒有 schema。
_BLANK_KEYS = frozenset({"$schema", "type"})


def specify(schema: Mapping[str, object] | None, path: str, type_name: str) -> dict[str, object]:
    """把 `path` 那個欄位的型別指定成 `type_name`，回新的 schema。

    `schema` 是 None（這份 config 還沒有 schema）時，建立一份只含這個欄位的——不順手把其餘
    欄位也鎖起來。同一個欄位再指定一次，`before` 仍是最初指定之前的型別。
    """
    if type_name not in _TO_JSON:
        raise UnknownTypeName(
            f"型別「{type_name}」不能指定給「{path}」。"
            f"下一步：改用 {'／'.join(TYPE_NAMES)} 其中之一"
        )
    new = copy.deepcopy(dict(schema)) if schema is not None else draft_schema({})
    node = new
    for key in _keys(path):
        properties = node.setdefault("properties", {})
        if not isinstance(properties, dict) or not isinstance(properties.setdefault(key, {}), dict):
            raise PathNotSpecifiable(
                f"schema 裡「{path}」這條路徑上的「{key}」不是物件，沒辦法在它底下指定型別。"
                "下一步：請開發者先把 schema 檔裡這一段改成 { } 的形式"
            )
        node = properties[key]
    mark = node.get(MARK)
    before = mark.get("before") if isinstance(mark, Mapping) else node.get("type")
    node["type"] = _TO_JSON[type_name]
    node[MARK] = {"before": before}
    return new


def clear(schema: Mapping[str, object], path: str) -> dict[str, object] | None:
    """清除 `path` 的人工指定，回新的 schema；清完整份什麼規則都不剩就回 None（不要這份了）。"""
    new = copy.deepcopy(dict(schema))
    chain: list[tuple[dict[str, object], str]] = []  # (父節點, 這一段的 key)
    node: object = new
    for key in _keys(path):
        properties = node.get("properties") if isinstance(node, dict) else None
        if not isinstance(properties, dict) or key not in properties:
            node = None
            break
        chain.append((properties, key))
        node = properties[key]
    if not isinstance(node, dict) or not isinstance(node.get(MARK), Mapping):
        raise TypeNotSpecified(
            f"「{path}」沒有人工指定的型別，沒有東西可以清除。"
            "下一步：重新整理這份 config，確認要清除的是標著「已指定」的那一列"
        )
    before = node.pop(MARK).get("before")
    if before is None:
        node.pop("type", None)
    else:
        node["type"] = before
    _prune(new, chain)
    return None if set(new) <= _BLANK_KEYS else new


def manual_types(schema: Mapping[str, object] | None) -> dict[str, str]:
    """schema 裡人工指定過的欄位：欄位路徑 → 型別（API／推斷用的名字）。沒有 schema 回空的。

    路徑與 `infer_types` 同一套文法，單筆內容據此把推斷的型別蓋成指定的、欄位表標示「已指定」。
    """
    found: dict[str, str] = {}
    _collect(schema or {}, "", found)
    return found


def _collect(node: object, path: str, found: dict[str, str]) -> None:
    if not isinstance(node, Mapping):
        return
    if path and isinstance(node.get(MARK), Mapping):
        found[path] = _FROM_JSON.get(str(node.get("type")), "unknown")
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        for key, child in properties.items():
            escaped = str(key).replace(".", "\\.")
            _collect(child, f"{path}.{escaped}" if path else escaped, found)


def _keys(path: str) -> list[str]:
    """欄位路徑拆成一段一段的 key（跳脫的點還原）。清單元素不能個別指定。"""
    if not path or "[" in path:
        raise PathNotSpecifiable(
            f"「{path}」不能個別指定型別——清單的元素型別是整個清單共用的。"
            "下一步：只對不在清單裡的單一參數指定型別"
        )
    return [piece.replace("\\.", ".") for piece in _SEGMENT_DOT.split(path)]


def _prune(root: dict[str, object], chain: list[tuple[dict[str, object], str]]) -> None:
    """由下往上拿掉清除後變成空殼的節點（當初為了指定才建立的那幾層）。"""
    owners: list[dict[str, object]] = [root]
    for properties, key in chain[:-1]:
        child = properties[key]
        if isinstance(child, dict):
            owners.append(child)
    for (properties, key), owner in zip(reversed(chain), reversed(owners), strict=True):
        if properties[key] != {}:
            return
        del properties[key]
        if not properties:
            del owner["properties"]
