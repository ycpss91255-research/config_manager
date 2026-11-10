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
回去）、或 reset 之後核對不到設定庫完整退回，才丟 `PromoteLeftBehind`（F37：說出原本的失敗、
沒還原的項目與失敗的那一步、設定庫的實際回復結果，並依結果給恢復步驟）。
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from config_manager.core.drafts import Promotion
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import (
    PromoteLeftBehind,
    RollbackFacts,
    WriterError,
    failure_reason,
    original_failure,
)
from config_manager.io.git import head, record, reset_hard, stage, tracked_clean
from config_manager.io.parsers import as_bytes
from config_manager.io.source import group_name, owner_name
from config_manager.io.digest import read_target
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
    batch = _Batch(repo, author, tuple(allowed_roots), companions or {})
    before_head = head(repo)
    try:
        for plan in promotions:
            batch.apply_one(plan)
    except BaseException as failure:
        _rollback(repo, before_head, batch.touched, failure, promotions)
        raise
    return [plan.uid for plan in promotions]


@dataclass
class _Batch:
    """一次進版共用的東西，與回滾要還原的清單（`touched`：寫出過的目標、寫回 repo 的同行檔案）。"""

    repo: str
    author: str
    roots: tuple[str, ...]
    companions: Mapping[str, Companions]
    touched: list[_Snapshot] = field(default_factory=list)

    def apply_one(self, plan: Promotion) -> None:
        """一份 config：寫來源複本與同行檔案 → stage → record → 寫目標。"""
        replace_atomically(os.path.join(self.repo, plan.source), as_bytes(plan.text))
        beside = self.companions.get(plan.uid, ())
        for relative, text in beside:
            # 回滾時 reset 只還原 git 追蹤的檔案；這次才新寫的檔案要另外記下來拿掉。
            absolute = os.path.join(self.repo, relative)
            self.touched.append(_snapshot(absolute))
            _place(absolute, text)
        stage(self.repo, plan.source, *(relative for relative, _ in beside))
        record(self.repo, plan.uid, plan.kind, plan.summary, self.author)
        # 先拍、寫成功才列入要還原的：write 原子，失敗＝目標沒動，回滾不該再去重寫它（F29）。
        # 記下是哪一份 config 的目標：還原失敗時「未還原」標記要掛在那一份上（#345）。
        before = replace(_snapshot(plan.target), config=plan.uid)
        write(plan.target, plan.text, plan.permissions, self.roots)
        self.touched.append(before)


@dataclass(frozen=True)
class _Snapshot:
    """一個目標在進版前的樣子：位元組（不存在＝None）與 owner／group／mode——回滾據以還原。"""

    path: str
    content: bytes | None
    uid: int = 0
    gid: int = 0
    mode: int = 0
    config: str | None = None  # 這是哪一份 config 的目標；設定庫裡的附帶檔案是 None（#345）


def _snapshot(path: str) -> _Snapshot:
    # 守門的讀取（F14）：目標是具名管道、裝置、連結或太大 → 具名例外，整批在寫出之前停下；
    # `apply` 的回滾把前面已寫的還原，不留半批。
    content = read_target(path)
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


# 回滾訊息裡的操作名稱（F37）：進版、將目標現況納入來源、退版共用這一套寫出與回滾，第一行要寫實際的
# 那一個，不一律寫「進版」。由這批進版資料的 kind 判斷。
_OPERATIONS = {"adopt": "將目標現況納入來源", "revert": "退版"}
_PROMOTE = "進版"

# 還原一份目標時可能失敗的那幾步（F37：寫出失敗的是哪一步，不引用暫存檔路徑）。
_REMOVE_STEP = "拿掉這次才新建的檔案"
_WRITE_BACK_STEP = "寫回操作前的內容"

# 「已完整退回」時，第 4 步的草稿說明（各操作不同）；其他三種情況用 _DRAFTS_HOLD。
_DRAFTS_RESTORED = {
    _PROMOTE: "4. 草稿：這次要進版的草稿都還在，沒有被清掉。恢復處理後，重新核對目前的來源、"
    "目標及全部草稿，再決定是否進版——避免舊草稿蓋掉剛恢復或納入的內容。",
    "將目標現況納入來源": "4. 草稿沒有因為這次失敗而改變。恢復處理後，重新核對目前的來源、"
    "目標及全部草稿，再決定下一步。",
    "退版": "4. 這一份在退版前本來就沒有草稿，這次失敗也沒有產生草稿。恢復處理後，"
    "重新核對目前的來源與目標，再決定下一步。",
}
_DRAFTS_HOLD = {
    _PROMOTE: "4. 草稿：這次要進版的草稿都還在，沒有被清掉；設定庫確認之前先不要進版。",
    "將目標現況納入來源": "4. 草稿沒有因為這次失敗而改變；設定庫確認之前先不要進版、覆蓋或納入。",
    "退版": "4. 這一份在退版前本來就沒有草稿，這次失敗也沒有產生草稿；"
    "設定庫確認之前先不要進版、覆蓋或納入。",
}
_HOLD_OFF = (
    "3. 先由維護者確認設定庫內容與歷史，確認可採用的來源版本後，再決定恢復方式；"
    "目前先不要進版、覆蓋或納入。"
)


class _RestoreFailed(Exception):
    """還原一個目標（或設定庫裡的附帶檔案）時失敗：記下失敗的那一步與原本的例外。"""

    def __init__(self, step: str, error: BaseException) -> None:
        super().__init__(step)
        self.step = step
        self.error = error

    def step_text(self) -> str:
        """失敗的那一步，含被拒或失敗（例「把擁有者改回 svc:svc時被拒」）。"""
        verb = "時被拒" if isinstance(self.error, PermissionError) else "時失敗"
        return f"{self.step}{verb}"

    def describe(self) -> str:
        return f"{self.step_text()}（{failure_reason(self.error)}）"


def _restore(before: _Snapshot) -> None:
    """把一個目標回寫成拍下的內容與 owner／group／mode；進版前不存在的就刪掉（沒有就什麼都不做）。

    owner／mode 在 rename 前套在暫存檔上（和 `io/writer` 同一條路）：設不回去就不換上去，
    目標維持現狀、例外交給呼叫端列為殘留——不靜默留成服務帳號擁有的 0600（不變式 2）。
    失敗時丟 `_RestoreFailed`，說出是哪一步（F37）。
    """
    if before.content is None:
        if os.path.lexists(before.path):
            try:
                os.remove(before.path)
            except OSError as error:
                raise _RestoreFailed(_REMOVE_STEP, error) from error
        return

    step = [_WRITE_BACK_STEP]

    def _restore_metadata(temporary: str) -> None:
        step[0] = f"把擁有者改回 {owner_name(before.uid)}:{group_name(before.gid)}"
        os.chown(temporary, before.uid, before.gid)
        step[0] = f"把權限改回 {before.mode:04o}"
        os.chmod(temporary, before.mode)
        step[0] = _WRITE_BACK_STEP

    try:
        replace_atomically(before.path, before.content, finalize=_restore_metadata)
    except (OSError, WriterError) as error:
        raise _RestoreFailed(step[0], error) from error


@dataclass(frozen=True)
class _RepoOutcome:
    """回滾後設定庫的實際回復結果（F37，四擇一）。

    `restored`＝附帶檔案全還原、reset 成功、HEAD 等於操作前、受追蹤的工作區與索引乾淨；
    `head_only`＝HEAD 相符但工作區與索引核對不了；`not_restored`＝已知沒退回（含 HEAD 相符但附帶
    檔案沒還原）；`unknown`＝退回後讀不到 HEAD。`detail` 是原因（`restored` 時是 commit 前 7 碼）。
    """

    state: str
    detail: str


def _reset_and_check(repo: str, before_head: str) -> _RepoOutcome:
    try:
        reset_hard(repo, before_head)
    except (OSError, subprocess.CalledProcessError) as error:
        return _RepoOutcome("not_restored", f"reset 失敗：{failure_reason(error)}")
    try:
        now = head(repo)
    except (OSError, subprocess.CalledProcessError) as error:
        return _RepoOutcome("unknown", f"退回後讀不到 HEAD：{failure_reason(error)}")
    if now != before_head:
        return _RepoOutcome(
            "not_restored", f"HEAD 是 {now[:7]}，不是操作前的 {before_head[:7]}"
        )
    try:
        clean = tracked_clean(repo)
    except (OSError, subprocess.CalledProcessError) as error:
        return _RepoOutcome("head_only", failure_reason(error))
    if not clean:
        return _RepoOutcome("not_restored", "工作樹或索引還有未提交的變更")
    return _RepoOutcome("restored", before_head[:7])


def _repo_lines(operation: str, outcome: _RepoOutcome) -> list[str]:
    """設定庫那一行：依實際回復結果四擇一，不先寫成功再補一句警告。"""
    if outcome.state == "restored":
        return [
            f"設定庫：已退回{operation}前（HEAD 回到 {outcome.detail}，"
            "工作樹與索引沒有未提交的變更，附帶檔案都已還原），"
            f"這次{operation}沒有留下紀錄。"
        ]
    if outcome.state == "head_only":
        return [
            "設定庫：HEAD 已回到操作前的 commit；工作樹、索引與附帶檔案是否完整還原，仍需核對。",
            f"核對工作樹與索引時失敗：{outcome.detail}。",
        ]
    if outcome.state == "unknown":
        return [
            f"設定庫：無法確認是否已退回{operation}前（{outcome.detail}）；"
            "請維護者核對設定庫的 HEAD、工作樹、索引與最近的紀錄。"
        ]
    return [
        f"設定庫：沒能完整退回{operation}前（{outcome.detail}）；"
        "歷史、工作樹或附帶檔案可能留著這次沒完成的內容。"
    ]


def _steps(operation: str, restored: bool, targets_left: bool) -> list[str]:
    """恢復步驟：先保存現況、再把原本的失敗交維護者；只有已完整退回才給覆蓋或納入（定稿 11/9）。"""
    what = "上列未還原的目標檔" if targets_left else "這次涉及的目標檔"
    first = (
        f"1. 先保存現況：請維護者把{what}，連同它的擁有者與權限，備份到指定的位置；"
        "同時保存這段錯誤訊息，以及「檢視差異」顯示的差異。只記下畫面上的值不能代替完整備份。"
    )
    if not restored:
        first += "並請維護者核對設定庫的 HEAD、工作樹、索引、附帶檔案與最近的紀錄。"
    second = (
        "2. 原本的失敗交給維護者判斷怎麼處理；擁有者、權限與提權設定由維護者決定，"
        f"不要為了讓{operation}通過而直接改擁有者或權限。"
    )
    if not restored:
        return [first, second, _HOLD_OFF, _DRAFTS_HOLD[operation]]
    third = (
        f"3. 決定保留哪一邊（在清單上打開這份目標所屬的 config）：確定要恢復來源（{operation}前）的"
        "內容：按「以來源覆蓋目標」；要保留目標現在的內容：按「將目標現況納入來源」；"
        f"兩者都會照清單設定擁有者與權限，不保證回到{operation}前的擁有者與權限。"
    )
    return [first, second, third, _DRAFTS_RESTORED[operation]]


def _left_behind_message(
    operation: str,
    leftover: Sequence[tuple[str, bool]],
    failure: BaseException,
    outcome: _RepoOutcome,
) -> str:
    """回滾沒有乾淨收尾時的訊息（F37）：已知還原失敗寫「回滾沒有完全還原」並列出失敗項目；
    沒有已知失敗、只是設定庫核對不完整時寫「回滾結果無法確認」——不把未確認寫成已確定失敗。
    只有一個「下一步：」。`leftover` 每項是（說明, 是不是設定庫裡的檔案）。"""
    known = bool(leftover) or outcome.state == "not_restored"
    lines = [
        f"{operation}沒有完成，而且回滾沒有完全還原。"
        if known
        else f"{operation}沒有完成，回滾結果無法確認。"
    ]
    targets_left = any(not inside for _, inside in leftover)
    if leftover:
        subject = "這份目標" if len(leftover) == 1 and targets_left else "上列項目"
        items = "；".join(text for text, _ in leftover)
        lines.append(
            f"未還原：{items}。{subject}可能留下部分變更，內容、擁有者與權限需要重新核對。"
        )
    reason, advice = original_failure(failure)
    lines.append(f"原本的失敗：{reason}。")
    if advice:
        lines.append(f"原本的失敗附的建議（給維護者參考）：{advice}。")
    lines.extend(_repo_lines(operation, outcome))
    lines.append("下一步：")
    lines.extend(_steps(operation, outcome.state == "restored", targets_left))
    return "\n".join(lines)


def _inside(repo: str, path: str) -> bool:
    return os.path.abspath(path).startswith(os.path.abspath(repo) + os.sep)


def _rollback(
    repo: str,
    before_head: str,
    touched: Sequence[_Snapshot],
    failure: BaseException,
    promotions: Sequence[Promotion],
) -> None:
    """還原到操作前：寫出過的目標（與一起寫回 repo 的其他檔案）逐個回寫拍下的內容與 owner／mode
    （原本不存在的就刪），repo reset 回操作前 HEAD，再核對設定庫真的回去了。

    有已知還原失敗、或設定庫沒能確認完整退回，就大聲失敗——不蓋掉原本的失敗（`__cause__` 指向它），
    也不讓「目標已是新內容、紀錄卻沒了」的半套狀態悄悄留著。全部還原且設定庫核對完整時什麼都不丟，
    由 `apply` 把原本的失敗原樣往上拋。丟出時帶結構化的事實（`RollbackFacts`，#345）。
    """
    operation = _operation_of(promotions)
    leftover: list[tuple[str, bool]] = []
    items: list[tuple[str | None, str, str, str]] = []
    for before in reversed(touched):
        try:
            _restore(before)
        except _RestoreFailed as failed:
            inside = _inside(repo, before.path)
            label = "（設定庫裡的檔案）" if inside else ""
            leftover.append((f"{before.path}{label}——{failed.describe()}", inside))
            items.append(
                (before.config, before.path, failed.step_text(), failure_reason(failed.error))
            )
    outcome = _reset_and_check(repo, before_head)
    if any(inside for _, inside in leftover):  # HEAD 相符也不算完整退回
        extra = "" if outcome.state == "restored" else f"；{outcome.detail}"
        outcome = _RepoOutcome("not_restored", f"附帶檔案沒能還原，見上列{extra}")
    if leftover or outcome.state != "restored":
        message = _left_behind_message(operation, leftover, failure, outcome)
        facts = RollbackFacts(
            operation=operation,
            left_behind=tuple(items),
            repo=_repo_fact(outcome),
            original=original_failure(failure)[0],
            uids=tuple(plan.uid for plan in promotions),
        )
        raise PromoteLeftBehind(message, facts) from failure


def _repo_fact(outcome: _RepoOutcome) -> tuple[str, str] | None:
    """設定庫的結果給「未還原」標記用的兩種（#345）：沒能完整退回（not_restored）或回滾結果無法確認
    （unconfirmed＝只核對到 HEAD、讀不到 HEAD）；已完整退回是 None。"""
    if outcome.state == "restored":
        return None
    if outcome.state == "not_restored":
        return "not_restored", outcome.detail
    if outcome.state == "head_only":
        return "unconfirmed", f"只核對到 HEAD；核對工作樹與索引時失敗：{outcome.detail}"
    return "unconfirmed", outcome.detail


def _operation_of(promotions: Sequence[Promotion]) -> str:
    kinds = {plan.kind for plan in promotions}
    return next((name for kind, name in _OPERATIONS.items() if kind in kinds), _PROMOTE)
