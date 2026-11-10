"""api/left_behind — 「未還原」標記的存放處、回滾錯誤的回應格式、`GET /api/left-behind`（#345）。

標記掛在 app 上（`Editing.marks`，和草稿同一種 app 級的存放），未持久化：服務重啟會遺失，遺失不代表
問題已處理。會寫入的端點都在 `editing.write` 的鎖裡改它；讀取不拿鎖——每次改動都換掉整個不可變的
`Marks`，讀到的不是舊的一份就是新的一份。

只有這幾個地方動標記：回滾錯誤（記事件）、同一份之後經介面的寫入整個成功（收起，回
`left_behind_cleared`）、解除納管（停止追蹤，回 `left_behind_untracked`）。設定庫那一筆不自動收起，
本版也沒有手動「已核對」收起按鈕。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from subprocess import CalledProcessError

from fastapi import FastAPI, HTTPException

from config_manager.core.left_behind import (
    Failure,
    Marks,
    clear_written,
    outcome_of,
    record,
    repo_view,
    target_view,
    untrack,
)
from config_manager.io.errors import PromoteLeftBehind, WriterError
from config_manager.io.preflight import read_config_list


class MarksBox:
    """一個 app 的「未還原」標記。"""

    def __init__(self) -> None:
        self.marks = Marks()

    def rollback_error(self, error: PromoteLeftBehind) -> HTTPException:
        """把一次回滾錯誤記成事件，回要丟出的 500：`detail` 是物件（kind rollback、outcome…）。"""
        facts = error.facts
        failure = Failure(
            operation=facts.operation,
            left_behind=facts.left_behind,
            repo=facts.repo,
            original=facts.original,
            uids=facts.uids,
        )
        self.marks = record(self.marks, failure, _now())
        return HTTPException(status_code=500, detail=rollback_detail(error))

    def cleared(self, written: Iterable[tuple[str, str]]) -> list[str]:
        """一次整個成功的寫入寫出了 (uid, 目標路徑)：收起路徑相同的標記，回依序收起的 uid。"""
        self.marks, uids = clear_written(self.marks, written)
        return uids

    def untracked(self, uid: str) -> list[str]:
        """解除納管：停止追蹤這一份，回被停止追蹤的 uid（沒有標記就是空的）。"""
        self.marks, uids = untrack(self.marks, uid)
        return uids

    def target(self, uid: str) -> dict[str, object] | None:
        return target_view(self.marks.targets.get(uid))

    def target_path(self, uid: str) -> str | None:
        mark = self.marks.targets.get(uid)
        return None if mark is None else mark.path

    @property
    def has_repo_mark(self) -> bool:
        return self.marks.repo is not None


@contextmanager
def write_failures(box: MarksBox) -> Iterator[None]:
    """寫入的失敗怎麼回（進版、處置、退版共用）：回滾錯誤記「未還原」事件、回物件格式的 500
    （#345）；其他寫出／記錄失敗（回滾完整）照舊回字串的 500。"""
    try:
        yield
    except PromoteLeftBehind as error:
        raise box.rollback_error(error) from error
    except (WriterError, CalledProcessError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


def rollback_detail(error: PromoteLeftBehind) -> dict[str, object]:
    """回滾錯誤的 500 `detail`（#345）。`outcome`：有任一項已知失敗就是 not_restored；`left_behind`
    與 `repo` 各自保留自己的結果。`message` 是回滾錯誤訊息那一整段（F37 定稿，不改字）。"""
    facts = error.facts
    repo_state = None if facts.repo is None else facts.repo[0]
    return {
        "kind": "rollback",
        "outcome": outcome_of(facts.left_behind, repo_state),
        "message": str(error),
        "operation": facts.operation,
        "left_behind": [
            {"uid": uid, "path": path, "step": step, "reason": reason}
            for uid, path, step, reason in facts.left_behind
        ],
        "repo": None if facts.repo is None else {"state": facts.repo[0], "detail": facts.repo[1]},
        "original": facts.original,
    }


def register_left_behind(app: FastAPI, repo: str, box: MarksBox) -> None:
    """`GET /api/left-behind`：所有標記與設定庫那一筆（#345）。不寫入，不經 `editing.write`。"""

    @app.get("/api/left-behind")
    def left_behind() -> dict[str, object]:
        """各份的標記（名稱與主機取清單上現在的）與設定庫那一筆（整體結果與各事件）。"""
        marks = box.marks
        entries = {entry.uid: entry for entry in read_config_list(repo).files}
        targets: list[dict[str, object]] = []
        for uid, mark in marks.targets.items():
            entry = entries.get(uid)
            targets.append({
                "uid": uid,
                "name": None if entry is None else entry.name,
                "hostname": None if entry is None else entry.hostname,
                **(target_view(mark) or {}),
            })
        return {"targets": targets, "repo": repo_view(marks.repo)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
