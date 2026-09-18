"""core/toml_support — 清單檔與白名單設定檔共用的 TOML 欄位工具。

兩份 TOML 設定檔（config-list.toml 的 T1、allowed-roots.toml 的 T23）都要在轉成資料
模型前，對照鍵集攔下未知欄位並指名行號（防止由設定檔注入內部欄位，不變式 2）。行號
定位與攔截邏輯相同、只有丟出的具名例外不同（`UnknownField` vs `RootsUnknownField`），
故抽在這裡共用、由呼叫端傳入例外類別。
"""

import re
from collections.abc import Iterable
from typing import NamedTuple


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
