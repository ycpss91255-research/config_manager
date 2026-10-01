"""core/problem — 一個驗證問題的形狀（T3）。

獨立成一個模組，是因為三層驗證各自產生它：`core/validate`（第 1 層與 `check` 入口）、
`core/schema_check`（第 2 層）。放在 `core/validate` 裡會讓第 2 層反過來 import 入口而成環。
"""

from __future__ import annotations

from dataclasses import dataclass

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Problem:
    """一個驗證問題。

    `line` 是主要行號（1 起算；給不出行號時為 None）；`lines` 在重複 key 這類「同一個問題
    出現在多行」時列出全部行號。`severity` 是 error（硬擋）或 warning（列出來讓人確認、不擋）。
    `path` 是欄位路徑（第 2 層才有；文法與欄位表的參數列一致：點號串接、key 內的字面點跳脫成
    `\\.`、list 元素 `[i]`），介面據此標示那一列。訊息與建議皆為中文（ADR-00000028）。
    """

    line: int | None
    message: str
    suggestion: str
    severity: str = ERROR
    lines: tuple[int, ...] = ()
    path: str | None = None

    @property
    def where(self) -> str:
        """給人看的位置：「第 N 行」；給不出行號時照實說，不印成「第 None 行」。"""
        return f"第 {self.line} 行" if self.line is not None else "行號不明"
