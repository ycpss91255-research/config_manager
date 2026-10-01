"""core/locate — 欄位路徑 → 原文行號（第 2 層驗證的「在哪裡」，#39）。

schema 驗的是解析後的資料，問題落在「哪個欄位」；人要改的是檔案，需要「第幾行」。解析後的
資料不一定還帶著行號（tomlkit、json、configobj 都不帶），所以這裡另外對原文掃一遍，建一張
「欄位路徑 → 行號」的表。

- yaml／json：交給 ruamel 的 round-trip 載入，它替每個鍵與清單元素記下位置。JSON 是 YAML 的
  子集，同一條路走得通。
- toml：逐行掃表頭（`[表]`／`[[陣列表]]`）與 `鍵 =`。寫在行內的表與陣列（`a = {…}`、`[1, 2]`）
  裡面的值不另外定位。
- ini：逐行掃區段與鍵。

**定位不到確切位置時，退回最近的上層**（缺少的必填欄位指到它該在的那個物件；行內表裡的值
指到那一行）；連上層都沒有就回 None——不猜一個行號（不變式 2）。

核心層不做 I/O：原文由呼叫端傳進來。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

Parts = tuple[str | int, ...]


def locator(text: str, fmt: str) -> Callable[[Sequence[str | int]], int | None]:
    """回一個「欄位路徑 → 行號」的查詢函式。行號 1 起算；定位不到回 None。"""
    found = _lines_by_path(text, fmt)

    def line_of(parts: Sequence[str | int]) -> int | None:
        key: Parts = tuple(parts)
        while key not in found:
            if not key:
                return None
            key = key[:-1]
        return found[key]

    return line_of


def _lines_by_path(text: str, fmt: str) -> dict[Parts, int]:
    if fmt in ("yaml", "json"):
        return _yaml_lines(text)
    if fmt == "toml":
        return _toml_lines(text.split("\n"))
    if fmt == "ini":
        return _ini_lines(text.split("\n"))
    return {}


def _yaml_lines(text: str) -> dict[Parts, int]:
    found: dict[Parts, int] = {}
    try:
        _walk(YAML(typ="rt").load(text), (), found)
    except YAMLError:
        # 走到這裡的內容已經過了第 1 層的解析；ruamel 仍讀不下去（JSON 與 YAML 的邊角差異）時
        # 不給行號，問題照樣回報，只是少了「第幾行」。
        return {}
    return found


def _walk(node: object, prefix: Parts, found: dict[Parts, int]) -> None:
    if isinstance(node, CommentedMap):
        children: list[tuple[str | int, object]] = list(node.items())
    elif isinstance(node, CommentedSeq):
        children = list(enumerate(node))
    else:
        return
    positions = node.lc.data or {}
    for key, child in children:
        path = (*prefix, key)
        if key in positions:
            found[path] = positions[key][0] + 1
        _walk(child, path, found)


# toml：表頭與鍵。`[[陣列表]]` 每出現一次是陣列的下一個元素。
_TOML_TABLE = re.compile(r"^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")
_TOML_NAME = r"""(?:[A-Za-z0-9_\-]+|"[^"]*"|'[^']*')"""
_TOML_KEY = re.compile(rf"^\s*(?P<key>{_TOML_NAME}(?:\s*\.\s*{_TOML_NAME})*)\s*=")
_TOML_SEGMENT = re.compile(r"\"[^\"]*\"|'[^']*'|[^.\s]+")


def _toml_segments(dotted: str) -> tuple[str, ...]:
    return tuple(piece.strip("\"'") for piece in _TOML_SEGMENT.findall(dotted))


def _toml_lines(lines: list[str]) -> dict[Parts, int]:
    found: dict[Parts, int] = {}
    indices: dict[tuple[str, ...], int] = {}  # 陣列表（以表頭寫的名字為鍵）目前數到第幾個元素

    def expand(names: tuple[str, ...]) -> Parts:
        path: list[str | int] = []
        for size, name in enumerate(names, start=1):
            path.append(name)
            if names[:size] in indices:
                path.append(indices[names[:size]])
        return tuple(path)

    table: Parts = ()
    for number, raw in enumerate(lines, start=1):
        header = _TOML_TABLE.match(raw)
        if header:
            names = _toml_segments(header.group(2))
            if header.group(1) == "[[":
                indices[names] = indices.get(names, -1) + 1
            table = expand(names)
            found.setdefault(table, number)
            continue
        key = _TOML_KEY.match(raw)
        if key:
            found.setdefault((*table, *_toml_segments(key.group("key"))), number)
    return found


# ini：`[區段]`（configobj 以括號層數表示巢狀：`[[子區段]]`）與 `鍵 = 值`。
_INI_SECTION = re.compile(r"^\s*(\[+)\s*(.+?)\s*\]+\s*(?:[#;].*)?$")
_INI_KEY = re.compile(r"^\s*(?P<key>[^#;\[=:\s][^=:]*?)\s*[=:]")


def _ini_lines(lines: list[str]) -> dict[Parts, int]:
    found: dict[Parts, int] = {}
    sections: list[str] = []
    for number, raw in enumerate(lines, start=1):
        header = _INI_SECTION.match(raw)
        if header:
            sections = [*sections[: len(header.group(1)) - 1], header.group(2)]
            found.setdefault(tuple(sections), number)
            continue
        key = _INI_KEY.match(raw)
        if key:
            found.setdefault((*sections, key.group("key")), number)
    return found
