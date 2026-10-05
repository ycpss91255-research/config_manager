"""api/drift — 偏離處置與寫出修復的端點（#29；設計 §5.4、§7.7、ADR-00000008）。

三個處置出口一支端點（`POST /api/configs/{uid}/resolve`，§3.5.3 的 resolve 動作）：
`overwrite`（以來源覆蓋目標）、`adopt`（將目標現況納入來源，走完整驗證）、`adopt_draft`
（先納入、待修正：目標現況載入草稿，含非法值只警告）。未部署的寫出修復是 `POST …/apply`。
不自動處置：這裡每一條都是人按了之後才跑。
"""

from __future__ import annotations

from subprocess import CalledProcessError
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config_manager.api.history import require_entry
from config_manager.api.checks import checks_for
from config_manager.api.session import Identity
from config_manager.api.shapes import as_problem, drafts_view
from config_manager.core.drafts import Stage, adopt_draft
from config_manager.core.models import FileEntry, Permissions
from config_manager.core.validate import ERROR, check
from config_manager.io.allowed_roots import root_prefixes
from config_manager.io.drift import adoption, overwrite
from config_manager.io.errors import PromoteLeftBehind, WriterError
from config_manager.io.git import history
from config_manager.io.parsers import as_text
from config_manager.io.parsers import read_source as read_source_copy
from config_manager.io.preflight import read_config_list
from config_manager.io.promote import apply as apply_promotions
from config_manager.io.repo import read_or_none
from config_manager.io.writer import write


class ResolveInput(BaseModel):
    """偏離處置的請求：選哪一個出口。"""

    action: Literal["overwrite", "adopt", "adopt_draft"]


def register_drift(
    app: FastAPI, repo: str, held: dict[str, Identity], stage_box: dict[str, Stage]
) -> None:
    """把偏離處置與寫出修復的端點掛上 app。抽出來的理由同 `register_history`（C901）。"""

    @app.post("/api/configs/{uid}/resolve")
    def resolve_drift(uid: str, payload: ResolveInput) -> dict[str, object]:
        """處置一份偏離的 config（設計文件 §3.5.3、§5.4）。三個出口都由人選、都留下痕跡。"""
        return _resolve(repo, held, stage_box, uid, payload.action)

    @app.post("/api/configs/{uid}/apply")
    def apply_missing(uid: str) -> dict[str, object]:
        """未部署的寫出修復（設計文件 §5.4）：把來源寫到 target。repo 沒變、不留紀錄。"""
        return _apply_missing(repo, uid)


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
        content = read_or_none(entry.target)
        if content is None:
            return None
        return as_text(content, entry.format)
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


def _resolve(
    repo: str, held: dict[str, Identity], stage_box: dict[str, Stage], uid: str, action: str
) -> dict[str, object]:
    identity = held.get("identity")
    if identity is None:
        raise HTTPException(
            status_code=409,
            detail="尚未設定身分，無法處置偏離——處置會留下變更紀錄，需要作者。"
            "下一步：先 POST /api/session 設定姓名與 email",
        )
    entry, permissions = _permissions_of(repo, uid)
    roots = root_prefixes(repo)
    target_text = _target_text(entry)
    source_text = read_source_copy(repo, entry.source, entry.format)
    if target_text is None:
        raise HTTPException(
            status_code=409,
            detail=f"「{entry.name}@{entry.hostname}」的目標不存在（未部署），不是偏離。"
            "下一步：用寫出修復（POST /api/configs/{uid}/apply）",
        )
    if target_text == source_text:
        raise HTTPException(
            status_code=409,
            detail=f"「{entry.name}@{entry.hostname}」目標與來源一致，沒有偏離可處置。"
            "下一步：按「檢查差異」重新掃描",
        )
    try:
        if action == "overwrite":
            overwrite(repo, entry, permissions, identity.git_author, roots)
            return {"uid": uid, "action": action, "record": _latest(repo, uid)}
        if action == "adopt":
            _reject_invalid(repo, entry, target_text)
            plan = adoption(entry, permissions, target_text)
            apply_promotions(repo, [plan], identity.git_author, roots)
            return {"uid": uid, "action": action, "record": _latest(repo, uid)}
    except (PromoteLeftBehind, WriterError, CalledProcessError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    # adopt_draft：內容照載，每個問題當警告回去；進版前須改正（T18）。不碰 repo、不碰 target。
    stage, warnings = adopt_draft(
        stage_box["stage"], uid, target_text, entry.format, checks_for(repo, entry)
    )
    stage_box["stage"] = stage
    return {
        "uid": uid,
        "action": action,
        "warnings": [as_problem(problem) for problem in warnings],
        **drafts_view(stage),
    }


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


def _apply_missing(repo: str, uid: str) -> dict[str, object]:
    """寫出修復：來源寫到 target。target 已在時仍可寫（等於再套一次來源），不留紀錄。"""
    entry, permissions = _permissions_of(repo, uid)
    try:
        source_text = read_source_copy(repo, entry.source, entry.format)
        write(entry.target, source_text, permissions, root_prefixes(repo))
    except (WriterError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"uid": uid, "target": entry.target}
