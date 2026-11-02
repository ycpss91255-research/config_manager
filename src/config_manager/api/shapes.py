"""api/shapes — 幾個端點共用的回應形狀（草稿檢視、驗證問題）。

抽出來是為了 `api/routes`（草稿）與 `api/drift`（偏離處置的先納入、待修正）都要回同一種
形狀而不互相 import（routes 掛 drift 的端點，反向 import 會成環）。純形狀轉換，無 I/O。
"""

from __future__ import annotations

import hashlib

from config_manager.core.drafts import Draft, Owner, Stage
from config_manager.core.validate import Problem


def drafts_view(stage: Stage, viewer: Owner | None = None) -> dict[str, object]:
    """目前階段裡的草稿：`{count, drafts:[{uid, format, sha, saved_by, touched_by}]}`。工具列
    「進版 (N)」與樹上的標記據此。`sha` 是草稿文字的 sha256（進版確認的簽章用）；`saved_by`／
    `touched_by` 相對於看的人（`viewer`）標 `mine`／`another_browser`（ADR-00000032），
    **不含票**。"""
    return {
        "count": len(stage.drafts),
        "drafts": [draft_view(draft, viewer) for draft in stage.drafts.values()],
    }


def draft_view(draft: Draft, viewer: Owner | None) -> dict[str, object]:
    return {
        "uid": draft.uid,
        "format": draft.fmt,
        "sha": draft_sha(draft),
        "saved_by": None if draft.saved_by is None else owner_view(draft.saved_by, viewer),
        "touched_by": [owner_view(who, viewer) for who in draft.touched_by],
    }


def draft_sha(draft: Draft) -> str:
    """草稿文字的 sha256（十六進位）。頁面在確認框開著時記下它，進版時帶回來比對
    （ADR-00000032）。"""
    return hashlib.sha256(draft.text.encode("utf-8")).hexdigest()


def owner_view(owner: Owner, viewer: Owner | None) -> dict[str, object]:
    """一個參與者給畫面看的樣子：姓名、email，和看的人是不是同一個瀏覽器身分（`mine`）、是不是同名同
    email 但另一個瀏覽器（`another_browser`）。票不出去。"""
    return {
        "name": owner.name,
        "email": owner.email,
        "mine": viewer is not None and owner == viewer,
        "another_browser": viewer is not None and owner != viewer and owner.same_person_as(viewer),
    }


def as_problem(problem: Problem) -> dict[str, object]:
    """一個驗證問題：行號／訊息／建議／嚴重度／全部行號，第 2 層的另帶欄位路徑（`path`，#39；
    第 1 層為 null）——介面據此標示那一列；第 3 層的警告另帶規則代號（`rule`，#41）——介面據此
    讓人對著那一條填略過的理由。"""
    return {
        "line": problem.line,
        "message": problem.message,
        "suggestion": problem.suggestion,
        "severity": problem.severity,
        "lines": list(problem.lines),
        "path": problem.path,
        "rule": problem.rule,
    }
