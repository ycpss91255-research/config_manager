"""core/left_behind — 「未還原」標記的純邏輯（#345）。不做 I/O。

「未還原」是**某次寫入操作失敗、回滾時這份目標的還原步驟失敗**的診斷標記，不是新的檔案狀態：清單上
的狀態照舊只由內容比對決定。還原步驟失敗＝可能留下部分變更，不是證明內容都沒還原。

回滾的結果分兩種，與回滾錯誤訊息的第一行對應（`io/promote`）：**還原步驟失敗**（已知失敗，可能在目標
或設定庫）與**回滾結果無法確認**（還原步驟都沒有失敗，但事後核對設定庫做不完；只會在設定庫）。

每次失敗是一個**事件**。標記保留所有事件、依時間排列——第一次與最近一次的時刻、操作、原因各自配對，
不把新事件的欄位寫進舊事件。設定庫那一筆任一次沒能完整退回，整體就是沒能完整退回；之後的無法確認
不覆蓋那次的原因與時刻。

標記放在服務的記憶體（api 層掛在 app 上），未持久化：服務重啟會遺失，遺失不代表問題已處理。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

NOT_RESTORED = "not_restored"
UNCONFIRMED = "unconfirmed"

# 回滾錯誤裡還原步驟失敗的一項：(config 的 uid；設定庫裡的附帶檔案是 None, 路徑, 那一步, 原因)。
LeftItem = tuple[str | None, str, str, str]


@dataclass(frozen=True)
class TargetEvent:
    """一份目標的一次還原步驟失敗。`step` 含被拒／失敗（例「把擁有者改回 svc:svc時被拒」）。"""

    at: str
    operation: str
    step: str
    reason: str
    original: str


@dataclass(frozen=True)
class TargetMark:
    uid: str
    path: str
    events: tuple[TargetEvent, ...]


@dataclass(frozen=True)
class RepoEvent:
    """設定庫的一次回滾問題：沒能完整退回（not_restored）或回滾結果無法確認（unconfirmed）。"""

    at: str
    operation: str
    state: str
    detail: str
    uids: tuple[str, ...]


@dataclass(frozen=True)
class RepoMark:
    events: tuple[RepoEvent, ...]

    @property
    def state(self) -> str:
        """整體結果：任一次沒能完整退回就是沒能完整退回。"""
        if any(event.state == NOT_RESTORED for event in self.events):
            return NOT_RESTORED
        return UNCONFIRMED


@dataclass(frozen=True)
class Failure:
    """一次回滾錯誤帶來的事實（`io/promote` 的 `PromoteLeftBehind` 給的）。

    `left_behind`：還原步驟失敗的每一項；uid 是 None 的是設定庫裡的附帶檔案，算在設定庫那一筆。
    `repo`：(結果, 原因)，已完整退回是 None。`uids`：這次操作涉及的 config。
    """

    operation: str
    left_behind: tuple[LeftItem, ...]
    repo: tuple[str, str] | None
    original: str
    uids: tuple[str, ...]


@dataclass(frozen=True)
class Marks:
    targets: Mapping[str, TargetMark] = field(default_factory=dict)
    repo: RepoMark | None = None


def record(marks: Marks, failure: Failure, at: str) -> Marks:
    """把一次回滾錯誤記成事件：還原步驟失敗的目標各加一個事件；設定庫有問題就加一個設定庫事件。"""
    targets = dict(marks.targets)
    for uid, path, step, reason in failure.left_behind:
        if uid is None:
            continue
        event = TargetEvent(at, failure.operation, step, reason, failure.original)
        before = targets.get(uid)
        events = (before.events if before is not None else ()) + (event,)
        targets[uid] = TargetMark(uid, path, events)
    repo = marks.repo
    if failure.repo is not None:
        state, detail = failure.repo
        event_ = RepoEvent(at, failure.operation, state, detail, failure.uids)
        repo = RepoMark((repo.events if repo is not None else ()) + (event_,))
    return Marks(targets, repo)


def clear_written(marks: Marks, written: Iterable[tuple[str, str]]) -> tuple[Marks, list[str]]:
    """一次**整個成功**的寫入寫出了 (uid, 目標路徑)：路徑與標記記的相同才收起。

    回 (新的標記, 依寫出順序收起的 uid)。寫之前就沒有標記的不列；路徑不同的不收（標記記的是那一個
    目標）。設定庫那一筆不在這裡收——它不自動收起。
    """
    targets = dict(marks.targets)
    cleared: list[str] = []
    for uid, path in written:
        mark = targets.get(uid)
        if mark is not None and mark.path == path:
            del targets[uid]
            cleared.append(uid)
    return replace(marks, targets=targets), cleared


def untrack(marks: Marks, uid: str) -> tuple[Marks, list[str]]:
    """解除納管：拿掉這一份的標記，記為停止追蹤（不算收起，目標沒有被還原）。"""
    if uid not in marks.targets:
        return marks, []
    targets = {key: mark for key, mark in marks.targets.items() if key != uid}
    return replace(marks, targets=targets), [uid]


def outcome_of(left_behind: Sequence[LeftItem], repo_state: str | None) -> str:
    """整體結果：有任一項已知失敗（還原步驟失敗，或設定庫沒能完整退回）就是 not_restored。"""
    if left_behind or repo_state == NOT_RESTORED:
        return NOT_RESTORED
    return UNCONFIRMED


def target_view(mark: TargetMark | None) -> dict[str, object] | None:
    if mark is None:
        return None
    return {
        "path": mark.path,
        "events": [
            {"at": e.at, "operation": e.operation, "step": e.step, "reason": e.reason,
             "original": e.original}
            for e in mark.events
        ],
    }


def repo_view(mark: RepoMark | None) -> dict[str, object] | None:
    if mark is None:
        return None
    return {
        "state": mark.state,
        "events": [
            {"at": e.at, "operation": e.operation, "state": e.state, "detail": e.detail,
             "uids": list(e.uids)}
            for e in mark.events
        ],
    }
