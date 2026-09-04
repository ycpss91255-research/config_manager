"""core/state — 四種狀態的判定（PDF §5.4）。

純邏輯，不做 I/O（ADR-00000011）：目標是否存在與兩邊的雜湊由呼叫者算好傳入。
"""

import enum


class State(str, enum.Enum):
    """一份 config 的狀態（CONTEXT）。

    「未納管」由清單成員在呼叫 decide 之前判定；其餘三種由 decide 比較目標與來源判定。
    """

    UNMANAGED = "unmanaged"
    IN_SYNC = "in_sync"
    DRIFT = "drift"
    MISSING = "missing"


def decide(target_exists: bool, target_hash: str | None, source_hash: str) -> State:
    """比較目標與來源，判定狀態。先看存在性，再看內容。"""
    if not target_exists:
        return State.MISSING
    if target_hash == source_hash:
        return State.IN_SYNC
    return State.DRIFT
