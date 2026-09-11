"""core/allowed_roots — 白名單設定檔（allowed-roots.toml）的載入與寫回（§7.9, #202）。

純邏輯，不做 I/O（ADR-00000011）：load/dump 收字串、不讀磁碟。讀檔與 preflight 在
io（T15），realpath 正規化與可見性也在 io（realpath 是 I/O，比照 T4 把連結解析留給
I/O 層）。與 config_list（T1，清單檔本身）分開：那是另一個檔、另一套 schema。
"""

import re
from collections.abc import Iterable
from pathlib import PurePosixPath

import tomlkit

from config_manager.core.errors import (
    DuplicatePrefix,
    InvalidPrefix,
    RootsUnknownField,
)
from config_manager.core.models import AllowedRoots

# 白名單設定檔的允許鍵集。結構驗證（必填、型別）交給 pydantic；這裡只擋未知欄位、
# 指名行號（防止由設定檔注入內部欄位，比照 config_list）。
_TOP_KEYS = {"roots_version", "roots"}
_ROOT_KEYS = {"prefix", "added_by", "added_at"}


def load(text: str) -> AllowedRoots:
    """把白名單設定檔的原始文字解析為已驗證的資料模型。"""
    doc = tomlkit.parse(text)
    _check_unknown_fields(doc, text)
    allowed = AllowedRoots.model_validate(doc.unwrap())
    _check_integrity(allowed)
    return allowed


def _find_line(text: str, key: str) -> int | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for lineno, line in enumerate(text.splitlines(), 1):
        if pattern.match(line):
            return lineno
    return None


def _reject_unknown(
    keys: Iterable[str], allowed: set[str], text: str, where: str
) -> None:
    for key in keys:
        if key not in allowed:
            line = _find_line(text, key)
            loc = f"第 {line} 行" if line is not None else where
            raise RootsUnknownField(
                f"無法辨識的欄位「{key}」（{loc}）；{where} 不接受此欄位。"
                f"下一步：刪掉該欄位，或改成 {where} 接受的欄位名"
            )


def _check_unknown_fields(doc: "tomlkit.TOMLDocument", text: str) -> None:
    """在轉為資料模型前，對照鍵集攔下未知欄位並指名行號。"""
    _reject_unknown(doc.keys(), _TOP_KEYS, text, "白名單設定檔頂層")
    roots = doc.get("roots")
    if roots is not None:
        for root in roots:
            _reject_unknown(root.keys(), _ROOT_KEYS, text, "白名單根條目")


def _check_prefix(prefix: str) -> None:
    """前綴的字面檢查：絕對路徑、不含 .. 逃逸（realpath 留給 io）。"""
    if not prefix.startswith("/"):
        raise InvalidPrefix(
            f"前綴「{prefix}」不是絕對路徑。"
            "下一步：改成以 / 開頭的絕對路徑"
        )
    if ".." in PurePosixPath(prefix).parts:
        raise InvalidPrefix(
            f"前綴「{prefix}」含 .. 路徑段，會逃逸到預期目錄外。"
            "下一步：改成不含 .. 的正規路徑"
        )


def _check_integrity(allowed: AllowedRoots) -> None:
    """prefix 唯一與字面合法（重複前綴是靜默的設定錯誤，訊息指出是哪兩筆）。"""
    seen: dict[str, int] = {}
    for index, root in enumerate(allowed.roots):
        _check_prefix(root.prefix)
        if root.prefix in seen:
            first = seen[root.prefix] + 1
            raise DuplicatePrefix(
                f"前綴「{root.prefix}」重複："
                f"第 {first} 筆與第 {index + 1} 筆共用同一個前綴。"
                "下一步：刪掉其中一筆"
            )
        seen[root.prefix] = index
