"""io/drift — 偏離處置的寫入面（#29；設計 §5.4、§7.7、ADR-00000008）。

偏離是「有人繞過介面改了 target」的異常訊號，系統不自動處置（設計原則 N-2）；這裡只提供
人選定之後的兩個會動到 repo 的出口：

- **以來源覆蓋目標**（`overwrite`）：來源複本寫回 target。repo 內容不變、git 沒東西可提交，但
  「誰在何時決定丟掉現場修改」要留在歷史上（A3）——記一筆**空 commit**（`record(allow_empty)`，
  kind `cfg`）。record 失敗就把 target 還原成覆蓋前的位元組，處置與紀錄成對（不變式 2）。
- **將目標現況納入來源**（`adopt`）：target 的內容當一筆 kind `adopt` 的進版資料交給
  `io/promote.apply`（`adoption` 組進版資料）——寫來源複本→記錄→寫出 target，失敗整批回滾。
  第 1 層驗證在 API 層先擋（含非法值不進來源）；「先納入、待修正」不在這裡，那是
  `core/drafts.adopt_draft`、不碰 I/O。

未部署的寫出修復（`POST …/apply`）只是 `io/writer.write`，不在此——沒有偏離、不留紀錄。
"""

from __future__ import annotations

import os

from config_manager.core.drafts import Promotion
from config_manager.core.models import FileEntry, Permissions
from config_manager.io.atomic import replace_atomically
from config_manager.io.git import record_empty
from config_manager.io.parsers import read_source as read_source_copy
from config_manager.io.repo import read_or_none
from config_manager.io.writer import write

OVERWRITE_SUMMARY = "以來源覆蓋目標（捨棄現場修改）"
ADOPT_SUMMARY = "納入現場調整"


def overwrite(
    repo: str, entry: FileEntry, permissions: Permissions, author: str, roots: tuple[str, ...]
) -> None:
    """來源複本寫回 target，並記一筆空的 cfg 紀錄。紀錄沒成就把 target 還原。"""
    text = read_source_copy(repo, entry.source, entry.format)
    before = read_or_none(entry.target)
    write(entry.target, text, permissions, roots)
    try:
        record_empty(repo, entry.uid, "cfg", OVERWRITE_SUMMARY, author)
    except BaseException:
        if before is None:
            if os.path.lexists(entry.target):
                os.remove(entry.target)
        else:
            replace_atomically(entry.target, before)
        raise


def adoption(entry: FileEntry, permissions: Permissions, target_text: str) -> Promotion:
    """target 現況成為來源：組成一筆 kind `adopt` 的進版資料，交給 `io/promote.apply`
    （寫來源→記錄→寫出，失敗整批回滾）。"""
    return Promotion(
        uid=entry.uid,
        source=entry.source,
        target=entry.target,
        text=target_text,
        permissions=permissions,
        summary=ADOPT_SUMMARY,
        kind="adopt",
    )
