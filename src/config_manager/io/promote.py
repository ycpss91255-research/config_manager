"""io/promote — 進版的寫出、記錄與失敗回復（#19；T9 系統層驗批次回滾）。

核心 `core/drafts.promote` 只產生一批 `Promotion` 並保證「全部驗證才進版」；這裡把它們真的做
出來。每份 config：寫來源複本到 repo → stage → record（kind 依 Promotion，進版 `cfg`、退版
`revert`，每份一筆）→ 以權限寫出部署
目標。記錄與寫出成對、整批原子（ADR-00000006、ADR-00000022）：

**第 k 份失敗 → 前 k−1 份已寫出的目標檔案還原為進版前內容、全部已產生的變更紀錄一併撤銷。**
目標靠寫前拍下的位元組**與 owner／group／mode** 還原（`replace_atomically` 加 finalize，和寫出
走同一條路；F29：只放回位元組會讓目標變成暫存檔的 0600、服務帳號擁有）；**只還原真的寫出過的
目標**——`write` 本身原子，失敗那一份（與其後的）目標根本沒動，回滾不去碰它（否則 root 擁有的
目標會被換成服務帳號的檔）；repo 靠 `reset_hard` 回進版前的 HEAD——commit 與來源複本一次退回，
不逐筆 revert（那會各留一筆「退回」紀錄，與「整批不進版」不符）。回滾本身失敗（含 owner 設不
回去）才丟 `PromoteLeftBehind`（同時說出原本的失敗與沒還原的目標）。
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from config_manager.core.drafts import Promotion
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import PromoteLeftBehind, WriterError
from config_manager.io.git import head, record, reset_hard, stage
from config_manager.io.parsers import as_bytes
from config_manager.io.repo import read_or_none
from config_manager.io.writer import write


# 一份 config 進版時要一起寫回 repo 的其他檔案：（repo 內相對路徑, 新內容；None＝拿掉）。
Companions = Sequence[tuple[str, str | None]]


def apply(
    repo: str,
    promotions: Sequence[Promotion],
    author: str,
    allowed_roots: Iterable[str],
    companions: Mapping[str, Companions] | None = None,
) -> list[str]:
    """逐份寫出並記錄；任一步失敗就整批回滾後重拋原本的例外。回傳已進版的 uid（依序）。

    作者記為進版者：adopt_draft 撈進來的偏離內容於是被重新掛到真人身上（T18）。

    `companions` 是 uid → 那份 config 要**在同一筆紀錄裡**一起寫回 repo 的其他檔案（退版時
    跟著回到那一版的 schema 與清單檔，#39）。它們與來源複本同進同退：失敗回滾時一起還原。
    """
    roots = tuple(allowed_roots)
    extras = companions or {}
    before_head = head(repo)
    touched: list[_Snapshot] = []
    try:
        for plan in promotions:
            replace_atomically(os.path.join(repo, plan.source), as_bytes(plan.text))
            beside = extras.get(plan.uid, ())
            for relative, text in beside:
                # 回滾時 reset 只還原 git 追蹤的檔案；這次才新寫的檔案要另外記下來拿掉。
                absolute = os.path.join(repo, relative)
                touched.append(_snapshot(absolute))
                _place(absolute, text)
            stage(repo, plan.source, *(relative for relative, _ in beside))
            record(repo, plan.uid, plan.kind, plan.summary, author)
            # 先拍、寫成功才列入要還原的：write 原子，失敗＝目標沒動，回滾不該再去重寫它（F29）。
            before = _snapshot(plan.target)
            write(plan.target, plan.text, plan.permissions, roots)
            touched.append(before)
    except BaseException as failure:
        _rollback(repo, before_head, touched, failure)
        raise
    return [plan.uid for plan in promotions]


@dataclass(frozen=True)
class _Snapshot:
    """一個目標在進版前的樣子：位元組（不存在＝None）與 owner／group／mode——回滾據以還原。"""

    path: str
    content: bytes | None
    uid: int = 0
    gid: int = 0
    mode: int = 0


def _snapshot(path: str) -> _Snapshot:
    content = read_or_none(path)
    if content is None:
        return _Snapshot(path, None)
    info = os.stat(path)
    return _Snapshot(path, content, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))


def _place(absolute: str, text: str | None) -> None:
    """把一個 repo 內的檔案寫成 `text`；`text` 是 None 就拿掉它（本來就不在則什麼都不做）。"""
    if text is None:
        if os.path.lexists(absolute):
            os.remove(absolute)
        return
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    replace_atomically(absolute, text.encode("utf-8"))


def _restore(before: _Snapshot) -> None:
    """把一個目標回寫成拍下的內容與 owner／group／mode；進版前不存在的就刪掉（沒有就什麼都不做）。

    owner／mode 在 rename 前套在暫存檔上（和 `io/writer` 同一條路）：設不回去就不換上去，
    目標維持現狀、例外交給呼叫端列為殘留——不靜默留成服務帳號擁有的 0600（不變式 2）。
    """
    if before.content is None:
        if os.path.lexists(before.path):
            os.remove(before.path)
        return

    def _restore_metadata(temporary: str) -> None:
        os.chown(temporary, before.uid, before.gid)
        os.chmod(temporary, before.mode)

    replace_atomically(before.path, before.content, finalize=_restore_metadata)


def _rollback(
    repo: str,
    before_head: str,
    touched: Sequence[_Snapshot],
    failure: BaseException,
) -> None:
    """還原到進版前：寫出過的目標（與一起寫回 repo 的其他檔案）逐個回寫拍下的內容與 owner／mode
    （原本不存在的就刪），repo reset 回進版前 HEAD。

    任一步還原不了就記下，最後大聲失敗——不蓋掉原本的失敗（`__cause__` 指向它），也不讓
    「目標已是新內容、紀錄卻沒了」的半套狀態悄悄留著。
    """
    leftover: list[str] = []
    for before in reversed(touched):
        try:
            _restore(before)
        except (OSError, WriterError) as error:
            leftover.append(f"{before.path}（{error}）")
    try:
        reset_hard(repo, before_head)
    except (OSError, subprocess.CalledProcessError) as error:
        leftover.append(f"repo 的 HEAD／來源複本（{error}）")

    if leftover:
        raise PromoteLeftBehind(
            f"進版中途失敗後回滾未竟，未還原：{'；'.join(leftover)}。原本的失敗：{failure}。"
            "下一步：先照原本的失敗處理，再手動把上列目標還原成進版前內容並核對 git 歷史"
        ) from failure
