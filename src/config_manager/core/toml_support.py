"""core/toml_support — 清單檔與白名單設定檔共用的 TOML 欄位工具。

兩份 TOML 設定檔（config-list.toml 的 T1、allowed-roots.toml 的 T23）都要在轉成資料
模型前，對照鍵集攔下未知欄位並指名行號（防止由設定檔注入內部欄位，不變式 2）。行號
定位與攔截邏輯相同、只有丟出的具名例外不同（`UnknownField` vs `RootsUnknownField`），
故抽在這裡共用、由呼叫端傳入例外類別。
"""

import re
from collections.abc import Iterable
from typing import NamedTuple

import tomlkit
from tomlkit import items

_BodyItem = tuple[items.Key | None, items.Item]


class Source(NamedTuple):
    """待搜尋的設定檔原文，加上行號搜尋的起始行。

    `start` 讓呼叫端把搜尋限縮到某個區段（如某個 [[files]] 條目）之內：條目層誤放的鍵可能
    與檔案較前處的合法同名鍵相撞，全域取第一個會指到那個無關的合法出現處（#218）。預設
    `Source(text)`（start=1）等同全檔搜尋，既有呼叫端不受影響。
    """

    text: str
    start: int = 1


def find_line(text: str, key: str, start: int = 1) -> int | None:
    """回傳 `key = ...` 出現在第 `start` 行（含）之後的行號（1 起算），找不到回 None。"""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for lineno, line in enumerate(text.splitlines(), 1):
        if lineno >= start and pattern.match(line):
            return lineno
    return None


def reject_unknown(
    keys: Iterable[str],
    allowed: set[str],
    source: Source,
    where: str,
    error: type[Exception],
) -> None:
    """對照 `allowed` 鍵集，任一未知的 `key` 都丟 `error`，訊息指名鍵與行號。

    `source` 帶的 `start` 把行號搜尋限縮在呼叫端指定的區段內（見 `Source`／`find_line`）。
    """
    for key in keys:
        if key not in allowed:
            line = find_line(source.text, key, source.start)
            loc = f"第 {line} 行" if line is not None else where
            raise error(
                f"無法辨識的欄位「{key}」（{loc}）；{where} 不接受此欄位。"
                f"下一步：刪掉該欄位，或改成 {where} 接受的欄位名"
            )


# ── AoT 條目移除時的 tomlkit trivia 搬移（config-list.toml 與 allowed-roots.toml 共用）──────
# 兩份設定檔都是 [[…]] 的 AoT，都撞同一個陷阱：tomlkit 把一筆的前導空白/註解掛在**前一筆**的
# 尾端，天真 del 會連同下一筆倖存條目的前導註解一起刪掉（#218/#257）。抽在這裡共用，避免兩邊
# 各留一份而漂移（pylint duplicate-code 也擋重複）。


def take_trailing_items(body: list[_BodyItem], stop: int) -> list[_BodyItem]:
    """取走 body[:stop] 尾端那段沒有鍵的項目（空白與註解），回傳被取走的項目。

    只動尾端，容器內「鍵→位置」對照不受影響——被取走的項目本來就沒有鍵。
    """
    start = stop
    while start > 0 and body[start - 1][0] is None:
        start -= 1
    run = body[start:stop]
    del body[start:stop]
    return run


def take_trailing_trivia(body: list[_BodyItem], stop: int) -> str:
    """回傳 take_trailing_items 取走那段的原文。"""
    return "".join(item.as_string() for _, item in take_trailing_items(body, stop))


def prepend_indent(table: items.Table, trivia: str) -> None:
    """把一段原文接到 table 的前導縮排前面（AoT 的表頭就從這裡渲染）。"""
    if trivia:
        table.trivia.indent = trivia + table.trivia.indent


def body_before_aot(doc: "tomlkit.TOMLDocument", key: str) -> tuple[list[_BodyItem], int]:
    """找出名為 `key` 的 AoT 前方那段空白與註解所在的容器 body 與位置。

    第一筆條目的前導註解不在 AoT 裡，而在前一個容器的最深處（tomlkit 的形狀）。
    """
    body: list[_BodyItem] = doc.body
    index = next(
        position
        for position, (item_key, _) in enumerate(body)
        if item_key is not None and item_key.key == key
    )
    while index > 0:
        previous = body[index - 1][1]
        if not isinstance(previous, items.Table):
            break
        body = previous.value.body
        index = len(body)
    return body, index


def adopt_leading_trivia(doc: "tomlkit.TOMLDocument", aot: "items.AoT", key: str) -> None:
    """把 AoT 每一筆上方的空白與註解，搬到那一筆自己身上（只在真的要刪某筆時呼叫）。

    搬完之後每一筆自帶前導註解，刪除就只是刪除、不牽連下一筆。搬移不改變輸出的任何一個位元組。
    呼叫端保證至少有一筆（有東西可刪才會走到這裡）。
    """
    tables = list(aot.body)
    if not tables:
        return
    body, index = body_before_aot(doc, key)
    prepend_indent(tables[0], take_trailing_trivia(body, index))
    for previous, current in zip(tables, tables[1:], strict=False):
        previous_body: list[_BodyItem] = previous.value.body
        prepend_indent(current, take_trailing_trivia(previous_body, len(previous_body)))
