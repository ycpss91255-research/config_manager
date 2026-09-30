"""core/drafts — T18 草稿（#18）。進版 `promote` 留給 #19。

草稿是「已在介面編輯但尚未進版的內容」，存於**編輯階段**、不在來源也不在目標（CONTEXT.md）。
三段式：編輯 → 儲存（草稿）→ 進版（記錄＋寫出，原子），ADR-00000022。**草稿不是第五種狀態**
——四種狀態比較的是目標與來源，草稿與之正交，這裡完全不看它們。

核心層純邏輯、不做 I/O：`Stage` 是不可變的資料結構，每個操作回**新的**階段。儲存不產生變更
紀錄、不寫到目標位置——這裡沒有任何 I/O 可做，那是結構上的保證，不是靠約定。API 層把目前的
階段放在 app 的 `held`（單一編輯階段，ADR-00000014；階段生命週期的 acquire／renew／sweep 是
#33，逾時清草稿在那裡接線——#18 的 D1）。

內容是改好的**文字**＋format：第 1 層驗證（core/validate）要靠它們。
- `save_draft` **擋第 1 層**（error 級；warning 如 `1.10` 不擋）：人正在編輯，壞內容不該存。
- `adopt_draft` **不擋、只回警告**：偏離處置的「先納入、待修正」要把目標現況撈進來修，
  拒載等於叫人先用 vim 修好——那正是要避免的。兩者的分界就在這裡。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from config_manager.core.errors import DraftInvalid, DraftNotFound
from config_manager.core.validate import ERROR, Problem, check


@dataclass(frozen=True)
class Draft:
    """一份草稿：哪個 config（uid）、改好的文字、它的 format。"""

    uid: str
    text: str
    fmt: str


@dataclass(frozen=True)
class Stage:
    """編輯階段裡的全部草稿（uid → Draft）。不可變：操作一律回新的 Stage。"""

    drafts: Mapping[str, Draft] = field(default_factory=lambda: MappingProxyType({}))


def save_draft(stage: Stage, uid: str, text: str, fmt: str) -> Stage:
    """存一份草稿。第 1 層驗證有 error 級問題就丟 `DraftInvalid`、**不存**（階段不變）。"""
    problems = [problem for problem in check(text, fmt) if problem.severity == ERROR]
    if problems:
        first = problems[0]
        raise DraftInvalid(
            f"草稿「{uid}」沒通過第 1 層驗證（第 {first.line} 行：{first.message}），沒有存下。"
            f"下一步：{first.suggestion}；共 {len(problems)} 處，逐條修正後再儲存",
            problems,
        )
    return _with(stage, Draft(uid, text, fmt))


def adopt_draft(stage: Stage, uid: str, target_text: str, fmt: str) -> tuple[Stage, list[Problem]]:
    """把目標現況載入草稿（偏離處置的「先納入、待修正」）。內容照載，回傳每個問題當警告。

    不因非法而拒載——但這份草稿仍受進版約束：含非法值時整批不進版（#19）。
    """
    return _with(stage, Draft(uid, target_text, fmt)), check(target_text, fmt)


def discard(stage: Stage, uid: str | None = None) -> Stage:
    """捨棄變更：指名 uid 就只丟那一份，不指名就清空。指名的 uid 沒草稿 → `DraftNotFound`。"""
    if uid is None:
        return Stage()
    if uid not in stage.drafts:
        raise DraftNotFound(
            f"階段裡沒有「{uid}」的草稿，沒有東西可以捨棄。"
            "下一步：確認 uid 是否拿錯，或這份草稿是否已經捨棄／進版"
        )
    return Stage(MappingProxyType({k: v for k, v in stage.drafts.items() if k != uid}))


def _with(stage: Stage, draft: Draft) -> Stage:
    return Stage(MappingProxyType({**stage.drafts, draft.uid: draft}))
