"""api/drift — 偏離處置與寫出修復的端點（#29；設計 §5.4、§7.7、ADR-00000008）。

三個處置出口一支端點（`POST /api/configs/{uid}/resolve`，§3.5.3 的 resolve 動作）：
`overwrite`（以來源覆蓋目標）、`adopt`（將目標現況納入來源，走完整驗證）、`adopt_draft`
（先納入、待修正：目標現況載入草稿，含非法值只警告）。另有 `rewrite`（以來源重新寫出，#345）：只給
帶「未還原」、內容與來源一致的那一份。未部署的寫出修復是 `POST …/apply`。
不自動處置：這裡每一條都是人按了之後才跑。寫出目標的出口整個成功時回 `left_behind_cleared`（這次
確實收起的「未還原」，#345）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from config_manager.api.history import require_entry
from config_manager.api.checks import checks_for
from config_manager.api.browser import ticket_of
from config_manager.api.left_behind import write_failures
from config_manager.api.lock import Editing, owner_of
from config_manager.api.shapes import as_problem, drafts_view
from config_manager.core.drafts import Owner, Stage, adopt_draft
from config_manager.core.models import FileEntry, Permissions
from config_manager.core.validate import ERROR, check
from config_manager.io.allowed_roots import root_prefixes
from config_manager.io.drift import adoption, overwrite, rewrite
from config_manager.io.digest import read_target
from config_manager.io.errors import TargetExists, TargetReadError, WriterError
from config_manager.io.git import history
from config_manager.io.parsers import as_text
from config_manager.io.parsers import read_source as read_source_copy
from config_manager.io.preflight import read_config_list
from config_manager.io.promote import apply as apply_promotions
from config_manager.io.writer import write


class ResolveInput(BaseModel):
    """偏離處置的請求：選哪一個出口。"""

    action: Literal["overwrite", "adopt", "adopt_draft", "rewrite"]


def register_drift(app: FastAPI, repo: str, editing: Editing) -> None:
    """把偏離處置與寫出修復的端點掛上 app。抽出來的理由同 `register_history`（C901）。兩支都經
    `editing.write`：只接受持有編輯權的那個分頁（F23）。"""

    @app.post("/api/configs/{uid}/resolve")
    def resolve_drift(uid: str, payload: ResolveInput, request: Request) -> dict[str, object]:
        """處置一份偏離的 config（設計文件 §3.5.3、§5.4）。三個出口都由人選、都留下痕跡。"""
        with editing.write(request, "處置偏離") as identity:
            me = owner_of(identity, ticket_of(request))
            return _resolve(repo, me, editing, uid, payload.action)

    @app.post("/api/configs/{uid}/apply")
    def apply_missing(uid: str, request: Request) -> dict[str, object]:
        """未部署的寫出修復（設計文件 §5.4）：目標不存在時把來源寫到 target；repo 沒變、不留
        紀錄。目標已存在→409，不覆蓋。"""
        with editing.write(request, "寫出到目標"):
            return _apply_missing(repo, uid, editing)


def _permissions_of(repo: str, uid: str) -> tuple[FileEntry, Permissions]:
    config_list = read_config_list(repo)
    entry = require_entry(config_list, uid)
    return entry, entry.permissions or config_list.defaults.permissions


def _target_text(entry: FileEntry) -> str | None:
    """target 現況的文字；不存在回 None（未部署：呼叫端據此分流）。

    不走白名單：target 是清單檔裡的絕對路徑，比對與寫出都不靠白名單（§7.9、scan 亦然）——
    白名單管的是納管與瀏覽可以碰哪些目錄，不是已納管目標在哪。讀不到（權限等）→422 原樣訊息。
    """
    try:
        content = read_target(entry.target)  # 守門的讀取（F14）：具名管道不卡、連結不跟、有上限
        if content is None:
            return None
        return as_text(content, entry.format)
    except TargetReadError as error:
        # 目標不是一般檔案／太大／讀不出來：具名、帶下一步的 422，什麼都沒動（F14）。
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(
            status_code=422,
            detail=f"目標 {entry.target} 讀不到：{error}。下一步：確認服務對該檔有讀取權限",
        ) from error
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail=f"目標 {entry.target} 不是 UTF-8 文字，無法以 {entry.format} 納入或比對："
            f"{error}。"
            "下一步：以來源覆蓋目標，或先把該檔轉成 UTF-8",
        ) from error


def _resolve(repo: str, me: Owner, editing: Editing, uid: str, action: str) -> dict[str, object]:
    entry, permissions = _permissions_of(repo, uid)
    target_text = _target_text(entry)
    source_text = read_source_copy(repo, entry.source, entry.format)
    if target_text is None:
        raise HTTPException(
            status_code=409,
            detail=f"「{entry.name}@{entry.hostname}」的目標不存在（未部署），不是偏離。"
            "下一步：用寫出修復（POST /api/configs/{uid}/apply）",
        )
    if action == "rewrite":
        return _rewrite(repo, me, editing, uid, target_text == source_text)
    if target_text == source_text:
        raise _in_sync(entry)
    if action == "adopt_draft":
        return _adopt_into_draft(repo, me, editing.stage_box, entry, target_text)
    roots = root_prefixes(repo)
    try:
        # 納入來源的回滾錯誤另記「未還原」、回物件格式的 500（#345）。
        with write_failures(editing.marks):
            if action == "overwrite":
                overwrite(repo, entry, permissions, me.label, roots)
            else:
                _reject_invalid(repo, entry, target_text)
                apply_promotions(repo, [adoption(entry, permissions, target_text)], me.label, roots)
    except TargetReadError as error:
        # 以來源覆蓋／採納前拍現場就發現目標不是一般檔案：拒絕整個動作、目標與 repo 都沒動（F14）。
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _written(repo, editing, entry, action)


def _written(repo: str, editing: Editing, entry: FileEntry, action: str) -> dict[str, object]:
    """寫出目標的處置整個成功：回最新那一筆紀錄，並收起這一份同路徑的「未還原」（#345）。"""
    return {
        "uid": entry.uid,
        "action": action,
        "record": _latest(repo, entry.uid),
        "left_behind_cleared": editing.marks.cleared([(entry.uid, entry.target)]),
    }


def _in_sync(entry: FileEntry) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"「{entry.name}@{entry.hostname}」目標與來源一致，沒有偏離可處置。"
        "下一步：按「檢查差異」重新掃描",
    )


def _adopt_into_draft(
    repo: str, me: Owner, stage_box: dict[str, Stage], entry: FileEntry, target_text: str
) -> dict[str, object]:
    """先納入、待修正：內容照載，每個問題當警告回去；進版前須改正（T18）。不碰 repo、不碰 target，
    所以不收起任何「未還原」（`left_behind_cleared` 恆為空）。"""
    stage, warnings = adopt_draft(
        stage_box["stage"], entry.uid, target_text, entry.format,
        replace(checks_for(repo, entry), owner=me),
    )
    stage_box["stage"] = stage
    return {
        "uid": entry.uid,
        "action": "adopt_draft",
        "warnings": [as_problem(problem) for problem in warnings],
        "left_behind_cleared": [],
        **drafts_view(stage, me),
    }


def _rewrite(repo: str, me: Owner, editing: Editing, uid: str, in_sync: bool) -> dict[str, object]:
    """以來源重新寫出（#345）：四個條件都成立才寫，否則 409、什麼都不寫。整個成功才收起標記。"""
    entry, permissions = _permissions_of(repo, uid)
    _refuse_rewrite(editing, entry, in_sync)
    try:
        # 寫出失敗、記錄失敗（放回位元組也失敗）：500、標記保留，不宣稱收起。
        with write_failures(editing.marks):
            rewrite(repo, entry, permissions, me.label, root_prefixes(repo))
    except TargetReadError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _written(repo, editing, entry, "rewrite")


def _refuse_rewrite(editing: Editing, entry: FileEntry, in_sync: bool) -> None:
    """重新寫出的四個條件（#345）：這一份有「未還原」、標記記的路徑與清單上現在的相同、沒有設定庫
    那一筆、內容與來源一致。沒有標記時照舊（一致就是沒有偏離可處置）。"""
    name = f"「{entry.name}@{entry.hostname}」"
    marked_path = editing.marks.target_path(entry.uid)
    if marked_path is None and in_sync:
        raise _in_sync(entry)
    if marked_path is None:
        raise HTTPException(
            status_code=409,
            detail=f"{name}沒有「未還原」標示，不是重新寫出的情況。"
            "下一步：用差異檢視決定保留哪一邊",
        )
    if marked_path != entry.target:
        raise HTTPException(
            status_code=409,
            detail=f"{name}的「未還原」標示記的是 {marked_path}，清單上現在是 {entry.target}，"
            "不重新寫出。下一步：請維護者核對這份 config 的目標設定",
        )
    if editing.marks.has_repo_mark:
        raise HTTPException(
            status_code=409,
            detail=f"{name}：設定庫有未退回或回滾結果無法確認的警示，不提供重新寫出。"
            "下一步：先由維護者核對設定庫內容與歷史，確認可採用的來源版本",
        )
    if not in_sync:
        raise HTTPException(
            status_code=409,
            detail=f"{name}的目標現在與來源不同，不是重新寫出的情況。"
            "下一步：用差異檢視決定保留哪一邊",
        )


def _reject_invalid(repo: str, entry: FileEntry, target_text: str) -> None:
    """將現況納入來源要走完整驗證：第 1 層、以及這份有 schema 時的第 2 層（#39），有 error 級
    問題就拒，說明原因與下一步（A3）。"""
    checks = checks_for(repo, entry)
    found = check(
        target_text, entry.format, rules=checks.rules, schema=checks.schema, lintrc=checks.lintrc
    )
    problems = [p for p in found if p.severity == ERROR]
    broken = [p for p in found if p.rule is not None]
    if not problems and broken:
        # 違反規則的現況：這裡不另開一個填理由的入口（#42 的定案）——指去走「先納入、待修正」，
        # 在草稿上儲存時填理由，理由才會跟著進版寫進變更紀錄。
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "override_required",
                "message": f"目標現況違反了規則 {broken[0].rule}（{broken[0].message}），"
                "不直接納入來源。下一步：改走「先納入、待修正」把現況載入草稿，"
                "調整數值或在儲存時填寫略過的理由，再進版",
                "uid": entry.uid,
                "file": entry.target,
                "problems": [as_problem(problem) for problem in broken],
            },
        )
    if problems:
        first = problems[0]
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"目標現況沒通過驗證（{first.where}：{first.message}），"
                f"不納入來源。下一步：{first.suggestion}；"
                "或改走「先納入、待修正」把現況載入草稿修正",
                "uid": entry.uid,
                "file": entry.target,
                "problems": [as_problem(problem) for problem in problems],
            },
        )


def _latest(repo: str, uid: str) -> dict[str, object]:
    change = history(repo, uid)[0]
    return {
        "sha": change.sha, "kind": change.kind, "summary": change.summary,
        "author": change.author, "at": change.at,
    }


def _apply_missing(repo: str, uid: str, editing: Editing) -> dict[str, object]:
    """寫出修復：目標**不存在**時把來源寫到 target，不留紀錄。

    目標那個位置已經有東西（一般檔、目錄、符號連結）→ 409，一個位元組都不動。這條路不留紀錄
    （但同樣只接受持有編輯權的分頁，F23），所以它不能覆蓋任何既有內容——那會是「無紀錄地改掉現場」。要以來源蓋過既有的檔案，
    走 `resolve` 的 `overwrite`（要身分、留一筆紀錄；介面上先確認）。「在不在」由寫入那一步
    自己原子地判定（`only_if_missing`），不是先看再寫。
    """
    entry, permissions = _permissions_of(repo, uid)
    try:
        source_text = read_source_copy(repo, entry.source, entry.format)
        write(entry.target, source_text, permissions, root_prefixes(repo), only_if_missing=True)
    except TargetExists as error:
        raise HTTPException(
            status_code=409,
            detail=f"目標檔案已經存在（{entry.target}），沒有寫出——寫出修復只補回不存在的檔案，"
            "不覆蓋既有內容。下一步：按「檢查差異」確認狀態；內容與來源不同（偏離）的話，"
            "到差異檢視選「以來源覆蓋目標」（會先確認，並留下變更紀錄）",
        ) from error
    except (WriterError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {
        "uid": uid,
        "target": entry.target,
        "left_behind_cleared": editing.marks.cleared([(uid, entry.target)]),
    }
