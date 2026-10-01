"""io/schema — 替一份已納管的 config 產生 schema 骨架，存進 config-repo 的 `.schemas/`（#38）。

JSON Schema 描述的是**解析後的資料結構**，所以 yaml／toml／json／ini 都走同一條路：讀來源
複本 → `core/parse` 解析 → `core/inference` 推斷型別、轉成骨架 → 寫成 `.schemas/<uid>.json`。
`.schemas/` 沿用 JSON Schema 這個既有標準的稱呼，不代表只適用於 JSON 檔（設計 §3.4）。

檔名用 uid：名稱可改（§4.3），uid 永不變，改名不會讓 schema 對不上它的 config。

**由開發者手動觸發，納管時不自動產生**（#38 動工前定案）：推斷只看得到當下的值，自動產生
等於替每一份檔案悄悄加上可能猜錯的硬擋。已有 schema 的不覆寫——那份可能被收緊過。

編排同 `io/unmanage`：先算好骨架與新的清單檔文字，再寫、再 stage、再記一筆 `meta`（只改
清單檔欄位、內容未動，§2.3）；寫入步驟任一步失敗就回滾到產生前，回滾也失敗才丟
`SchemaLeftBehind`。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping

from config_manager.core.config_list import dump
from config_manager.core.inference import draft_schema, infer_types
from config_manager.core.models import FileEntry
from config_manager.core.parse import parse, values
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import (
    SchemaExists,
    SchemaLeftBehind,
    SchemaNotFound,
    SchemaUnavailable,
)
from config_manager.io.git import record, stage, unstage
from config_manager.io.parsers import read_source
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.repo import load_list, read_or_none, undo, write_config_list

SCHEMAS_DIR = ".schemas"


def schema_relpath(uid: str) -> str:
    """`uid` 那份 config 的 schema 在 repo 內的相對路徑（填進 `FileEntry.schema_path` 的值）。"""
    return f"{SCHEMAS_DIR}/{uid}.json"


def draft_skeleton(repo: str, uid: str, author: str) -> FileEntry:
    """替 `uid` 那份 config 產生 schema 骨架，回傳更新後的條目（`schema_path` 指向骨架）。"""
    original, current = load_list(repo)
    entry = next((item for item in current.files if item.uid == uid), None)
    if entry is None:
        raise SchemaNotFound(
            f"清單檔裡沒有 uid「{uid}」的條目，無從產生 schema。"
            "下一步：重新整理清單，確認該 config 仍在納管中"
        )
    if entry.schema_path is not None:
        raise SchemaExists(
            f"「{entry.name}@{entry.hostname}」已經有 schema（{entry.schema_path}），不覆寫。"
            "下一步：要調整就直接編輯該檔"
        )
    relative = schema_relpath(uid)
    text = json.dumps(_skeleton(repo, entry), ensure_ascii=False, indent=2) + "\n"
    # 先算好新文字（dump 的完整性檢查在此），此刻 repo 一個位元組都還沒動。
    updated = entry.model_copy(update={"schema_path": relative})
    files = [updated if item.uid == uid else item for item in current.files]
    new_text = dump(current.model_copy(update={"files": files}), original)

    absolute = os.path.join(repo, relative)
    # 清單檔沒指到、檔案卻在（先前回滾未竟的殘留）：拍下來，回滾時放回原樣。
    stray = read_or_none(absolute)
    try:
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        replace_atomically(absolute, text.encode("utf-8"))
        write_config_list(repo, new_text)
        stage(repo, relative, CONFIG_LIST_NAME)
        record(repo, uid, "meta", "產生 schema 骨架", author)
    except BaseException as failure:
        _rollback(repo, relative, original, stray, failure)
        raise
    return updated


def _skeleton(repo: str, entry: FileEntry) -> dict[str, object]:
    """由來源複本推導骨架；沒有結構可推導的丟 `SchemaUnavailable`。"""
    label = f"{entry.name}@{entry.hostname}"
    if entry.format == "raw":
        raise SchemaUnavailable(
            f"「{label}」的格式是 raw（不解析、只做版控），沒有結構可以產生 schema。"
            "下一步：這份維持未驗證；要把關就以可解析的格式重新納管"
        )
    data = values(parse(read_source(repo, entry.source), entry.format))
    if not isinstance(data, Mapping):
        raise SchemaUnavailable(
            f"「{label}」的頂層不是物件（整份是一個陣列或單一值），沒有欄位可以產生 schema。"
            "下一步：這份維持未驗證；要把關就把內容改成「鍵: 值」的結構"
        )
    return draft_schema(infer_types(data))


def _rollback(
    repo: str, relative: str, list_text: str, stray: bytes | None, failure: BaseException
) -> None:
    """還原到產生前：索引退回 HEAD、清單檔寫回原文、骨架檔拿掉（原本有殘留就放回）。"""
    absolute = os.path.join(repo, relative)

    def _restore_schema() -> None:
        if stray is not None:
            replace_atomically(absolute, stray)
        elif os.path.lexists(absolute):
            os.remove(absolute)

    steps: list[tuple[str, Callable[[], None]]] = [
        ("索引", lambda: unstage(repo, relative, CONFIG_LIST_NAME)),
        (CONFIG_LIST_NAME, lambda: write_config_list(repo, list_text)),
        (relative, _restore_schema),
    ]
    leftover = undo(steps)
    if leftover:
        raise SchemaLeftBehind(
            f"產生 schema 骨架中途失敗後回滾未竟，殘留：{'；'.join(leftover)}。"
            f"原本的失敗：{failure}。"
            "下一步：先照原本的失敗處理，再手動核對清單檔與該 schema 檔是否回到產生前"
        ) from failure
