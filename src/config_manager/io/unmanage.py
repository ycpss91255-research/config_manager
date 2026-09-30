"""io/unmanage — 解除納管（#28；設計 §5.5 圖 9、§2.3 的 `unmanage`）。

解除納管把一份 config 從清單檔移除、來源複本自 repo 拿掉，並記一筆 `unmanage` 紀錄。
**不刪 target**：解除管理不該改變系統當前行為，只是系統不再追蹤它（§5.5）。歷史內容仍在 git
裡，需要時翻得到——所以來源複本是從工作區與索引移除、以 commit 記下，不是改寫歷史。

編排同 `io/onboard`（#172／#173）：先算好新的清單檔文字（`core.dump` 保留其餘條目的原樣、
連同該條目的註解一起拿掉），再寫、再刪、再 stage、再 record；寫入步驟任一步失敗就回滾到
解除前（清單檔還原、來源複本放回、索引退回 HEAD），回滾也失敗才丟 `UnmanageLeftBehind`。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from pydantic import ValidationError
from tomlkit.exceptions import TOMLKitError

from config_manager.core.config_list import dump, load
from config_manager.core.errors import ConfigListError
from config_manager.core.models import FileEntry
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import (
    ConfigListUnparsable,
    UnmanageLeftBehind,
    UnmanageNotFound,
    WriterError,
)
from config_manager.io.git import record, stage, unstage
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.repo import read_or_none, write_config_list


def unmanage(repo: str, uid: str, author: str) -> FileEntry:
    """把 `uid` 那筆從管理中解除，回傳被解除的條目。target 一個位元組都不動。"""
    list_path = os.path.join(repo, CONFIG_LIST_NAME)
    try:
        with open(list_path, encoding="utf-8") as handle:
            original = handle.read()
        current = load(original)
    except (UnicodeDecodeError, TOMLKitError, ValidationError, ConfigListError) as error:
        raise ConfigListUnparsable(
            f"清單檔無法解析：{list_path}——{error}。下一步：依訊息指出的位置修正該檔",
            file=list_path,
        ) from error
    entry = next((item for item in current.files if item.uid == uid), None)
    if entry is None:
        raise UnmanageNotFound(
            f"清單檔裡沒有 uid「{uid}」的條目，沒有東西可以解除。"
            "下一步：重新整理清單，確認該 config 仍在納管中"
        )
    # 先算好新文字（dump 的完整性檢查在此），此刻 repo 一個位元組都還沒動。
    kept = [item for item in current.files if item.uid != uid]
    new_text = dump(current.model_copy(update={"files": kept}), original)

    source_path = os.path.join(repo, entry.source)
    # 來源複本已經不在（repo 被手動動過）時仍該能解除，回滾時也沒有東西要放回。
    source_bytes = read_or_none(source_path)
    try:
        write_config_list(repo, new_text)
        if source_bytes is not None:
            os.remove(source_path)
        stage(repo, entry.source, CONFIG_LIST_NAME)
        record(repo, uid, "unmanage", f"解除管理（{entry.name}@{entry.hostname}）", author)
    except BaseException as failure:
        _rollback(repo, entry.source, original, source_bytes, failure)
        raise
    return entry


def _rollback(
    repo: str, source: str, list_text: str, source_bytes: bytes | None, failure: BaseException
) -> None:
    """還原到解除前：索引退回 HEAD、清單檔寫回原文、來源複本放回。還原不了的逐一記下、大聲失敗。"""
    steps: list[tuple[str, Callable[[], None]]] = [
        ("索引", lambda: unstage(repo, source, CONFIG_LIST_NAME)),
        (CONFIG_LIST_NAME, lambda: write_config_list(repo, list_text)),
    ]
    if source_bytes is not None:
        restore = source_bytes
        steps.append((source, lambda: replace_atomically(os.path.join(repo, source), restore)))
    leftover = []
    for label, step in steps:
        try:
            step()
        except (OSError, WriterError, subprocess.CalledProcessError) as error:
            leftover.append(f"{label}（{error}）")
    if leftover:
        raise UnmanageLeftBehind(
            f"解除納管中途失敗後回滾未竟，殘留：{'；'.join(leftover)}。原本的失敗：{failure}。"
            "下一步：先照原本的失敗處理，再手動核對清單檔與該來源複本是否回到解除前"
        ) from failure
