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

from config_manager.core.errors import DraftInvalid, DraftNotFound, PromoteInvalid
from config_manager.core.models import ConfigList, Permissions
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


def save_draft(
    stage: Stage, uid: str, text: str, fmt: str, schema: Mapping[str, object] | None = None
) -> Stage:
    """存一份草稿。驗證有 error 級問題就丟 `DraftInvalid`、**不存**（階段不變）。

    第 1 層一律跑；這份 config 有 schema（`schema`）時再跑第 2 層（#39），兩層都是硬擋。
    """
    problems = [p for p in check(text, fmt, schema=schema) if p.severity == ERROR]
    if problems:
        first = problems[0]
        raise DraftInvalid(
            f"草稿「{uid}」沒通過驗證（{first.where}：{first.message}），沒有存下。"
            f"下一步：{first.suggestion}；共 {len(problems)} 處，逐條修正後再儲存",
            problems,
        )
    return _with(stage, Draft(uid, text, fmt))


def adopt_draft(
    stage: Stage, uid: str, target_text: str, fmt: str, schema: Mapping[str, object] | None = None
) -> tuple[Stage, list[Problem]]:
    """把目標現況載入草稿（偏離處置的「先納入、待修正」）。內容照載，回傳每個問題當警告。

    不因非法而拒載——但這份草稿仍受進版約束：含非法值（含不符 schema，#39）時整批不進版（#19）。
    """
    return _with(stage, Draft(uid, target_text, fmt)), check(target_text, fmt, schema=schema)


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


# ── 進版（promote，#19）──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Promotion:
    """一份要進版的 config：寫回哪裡（repo 內來源複本與部署目標）、寫什麼、用什麼權限、紀錄怎麼說。

    核心 `promote` 只產生這批資料並保證「全部驗證才進版」；實際寫出、記錄與失敗回復牽涉 I/O，
    在 `io/promote`（T9 系統層驗批次回滾）。`summary` 進變更紀錄主旨（kind `cfg`，介面顯示為
    「修改參數」，CONTEXT.md 變更紀錄類型）。
    """

    uid: str
    source: str
    target: str
    text: str
    permissions: Permissions
    summary: str
    # 變更紀錄的類型：進版是 cfg；退版把舊版內容當一筆 revert 寫回（#24），走同一條 apply。
    kind: str = "cfg"


def promote(
    stage: Stage,
    config_list: ConfigList,
    schemas: Mapping[str, Mapping[str, object]] | None = None,
) -> list[Promotion]:
    """把階段裡**全部**草稿一次驗證，回傳每份的進版資料；任一份沒過就丟 `PromoteInvalid`。

    進版是全域動作、整批原子（ADR-00000022、ADR-00000006）：不做「先進通過的那幾份」，所以
    先把每一份都驗完才回傳任何東西。adopt_draft 撈進來的壞內容在這裡被擋——改乾淨才進得了版，
    且作者記為進版者（偏離內容因此重新掛到真人身上，由呼叫端傳作者）。權限用條目自己的、
    沒寫就用清單檔的 defaults（T1 的語意）。沒有草稿時回空清單，要不要當錯由呼叫端決定。

    `schemas` 是 uid → 那份 config **此刻**的 schema（#39）：進版時重驗，草稿存下之後 schema 被
    收緊、或 adopt 進來的內容不符 schema，都在這裡被擋。沒列到的 uid 只過第 1 層。
    """
    schemas = schemas or {}
    entries = {entry.uid: entry for entry in config_list.files}
    plans: list[Promotion] = []
    for uid, draft in stage.drafts.items():
        entry = entries.get(uid)
        if entry is None:
            raise PromoteInvalid(
                f"草稿「{uid}」對應的條目已不在清單檔裡，整批不進版。"
                "下一步：捨棄這份草稿，或確認該 config 是否已被解除管理",
                uid,
            )
        found = check(draft.text, draft.fmt, schema=schemas.get(uid))
        problems = [p for p in found if p.severity == ERROR]
        if problems:
            first = problems[0]
            raise PromoteInvalid(
                f"「{entry.name}@{entry.hostname}」（{uid}）{first.where}沒通過驗證："
                f"{first.message}，整批不進版。下一步：{first.suggestion}；修正後再進版",
                uid,
                problems,
            )
        plans.append(
            Promotion(
                uid=uid,
                source=entry.source,
                target=entry.target,
                text=draft.text,
                permissions=entry.permissions or config_list.defaults.permissions,
                summary=f"修改參數（{entry.name}@{entry.hostname}）",
            )
        )
    return plans
