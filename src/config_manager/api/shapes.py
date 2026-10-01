"""api/shapes — 幾個端點共用的回應形狀（草稿檢視、驗證問題）。

抽出來是為了 `api/routes`（草稿）與 `api/drift`（偏離處置的先納入、待修正）都要回同一種
形狀而不互相 import（routes 掛 drift 的端點，反向 import 會成環）。純形狀轉換，無 I/O。
"""

from __future__ import annotations

from config_manager.core.drafts import Stage
from config_manager.core.validate import Problem


def drafts_view(stage: Stage) -> dict[str, object]:
    """目前階段裡的草稿：`{count, drafts:[{uid, format}]}`。工具列「進版 (N)」與樹上的標記據此。"""
    return {
        "count": len(stage.drafts),
        "drafts": [{"uid": draft.uid, "format": draft.fmt} for draft in stage.drafts.values()],
    }


def as_problem(problem: Problem) -> dict[str, object]:
    """一個驗證問題：行號／訊息／建議／嚴重度／全部行號，第 2 層的另帶欄位路徑（`path`，#39；
    第 1 層為 null）——介面據此標示那一列。"""
    return {
        "line": problem.line,
        "message": problem.message,
        "suggestion": problem.suggestion,
        "severity": problem.severity,
        "lines": list(problem.lines),
        "path": problem.path,
    }
