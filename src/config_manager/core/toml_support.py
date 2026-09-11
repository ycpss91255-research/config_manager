"""core/toml_support — 清單檔與白名單設定檔共用的 TOML 欄位工具。

兩份 TOML 設定檔（config-list.toml 的 T1、allowed-roots.toml 的 T23）都要在轉成資料
模型前，對照鍵集攔下未知欄位並指名行號（防止由設定檔注入內部欄位，不變式 2）。行號
定位與攔截邏輯相同、只有丟出的具名例外不同（`UnknownField` vs `RootsUnknownField`），
故抽在這裡共用、由呼叫端傳入例外類別。
"""

import re
from collections.abc import Iterable


def find_line(text: str, key: str) -> int | None:
    """回傳 `key = ...` 出現的行號（1 起算），找不到回 None。"""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for lineno, line in enumerate(text.splitlines(), 1):
        if pattern.match(line):
            return lineno
    return None


def reject_unknown(
    keys: Iterable[str],
    allowed: set[str],
    text: str,
    where: str,
    error: type[Exception],
) -> None:
    """對照 `allowed` 鍵集，任一未知的 `key` 都丟 `error`，訊息指名鍵與行號。"""
    for key in keys:
        if key not in allowed:
            line = find_line(text, key)
            loc = f"第 {line} 行" if line is not None else where
            raise error(
                f"無法辨識的欄位「{key}」（{loc}）；{where} 不接受此欄位。"
                f"下一步：刪掉該欄位，或改成 {where} 接受的欄位名"
            )
