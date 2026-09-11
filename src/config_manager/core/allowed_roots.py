"""core/allowed_roots — 白名單設定檔（allowed-roots.toml）的載入與寫回（§7.9, #202）。

純邏輯，不做 I/O（ADR-00000011）：load/dump 收字串、不讀磁碟。讀檔與 preflight 在
io（T15），realpath 正規化與可見性也在 io（realpath 是 I/O，比照 T4 把連結解析留給
I/O 層）。與 config_list（T1，清單檔本身）分開：那是另一個檔、另一套 schema。
"""

from pathlib import PurePosixPath

import tomlkit
from tomlkit import items

from config_manager.core.errors import (
    DuplicatePrefix,
    InvalidPrefix,
    RootsDumpMismatch,
    RootsUnknownField,
)
from config_manager.core.models import AllowedRoot, AllowedRoots
from config_manager.core.toml_support import reject_unknown

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


def dump(allowed: AllowedRoots, original: str) -> str:
    """把白名單設定檔寫回文字，保留原樣（註解、順序、引號樣式）。

    原樣資訊為原始檔文字；以 tomlkit 重新解析後在其上**追加**模型裡有、而原文沒有的根，
    未觸動的部分逐位元組保留。範圍只到追加（#202）——移除與改動是 #15。

    **寫出之前先自我驗證**，與 `load` 同一組完整性檢查（比照 config_list #181）：檢查不
    通過時丟具名例外，且不產生輸出。原樣資訊以 `prefix` 定位既有根（白名單根沒有 uid，
    prefix 就是識別碼）；有一筆缺 prefix 或兩筆共用 prefix 時丟 `RootsDumpMismatch`。
    """
    _check_integrity(allowed)

    doc = tomlkit.parse(original)
    roots = doc.get("roots")
    if roots is None:
        roots = tomlkit.aot()
        doc["roots"] = roots

    existing = _index_by_prefix(roots)
    for root in allowed.roots:
        if root.prefix not in existing:
            roots.append(_root_to_table(root))

    return tomlkit.dumps(doc)


def _index_by_prefix(roots: "items.AoT") -> dict[str, int]:
    index: dict[str, int] = {}
    for position, table in enumerate(roots.body):
        prefix = table.get("prefix")
        if prefix is None:
            raise RootsDumpMismatch(
                f"原樣資訊第 {position + 1} 筆白名單根缺 prefix，無法以 prefix 定位。"
                "下一步：補上該筆的 prefix，或改用一份合法的白名單設定檔"
            )
        if prefix in index:
            raise RootsDumpMismatch(
                f"原樣資訊的前綴「{prefix}」重複，dump 以 prefix 定位就對不回去。"
                "下一步：先移除重複，或改用一份合法的白名單設定檔"
            )
        index[prefix] = position
    return index


def _root_to_table(root: AllowedRoot) -> items.Table:
    table = tomlkit.table()
    table["prefix"] = root.prefix
    if root.added_by:
        table["added_by"] = root.added_by
    if root.added_at:
        table["added_at"] = root.added_at
    return table


def _check_unknown_fields(doc: "tomlkit.TOMLDocument", text: str) -> None:
    """在轉為資料模型前，對照鍵集攔下未知欄位並指名行號。"""
    reject_unknown(doc.keys(), _TOP_KEYS, text, "白名單設定檔頂層", RootsUnknownField)
    roots = doc.get("roots")
    if roots is not None:
        for root in roots:
            reject_unknown(root.keys(), _ROOT_KEYS, text, "白名單根條目", RootsUnknownField)


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
