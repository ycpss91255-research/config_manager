"""api/drafts — 草稿與進版的端點（三段式的儲存／捨棄／進版，ADR-00000022；#18／#19／#21）。

`GET`／`POST`／`DELETE /api/drafts`、`DELETE /api/drafts/{uid}`、`POST /api/promote`。從
`api/routes` 搬出來（#41）：那個檔已貼近單檔行數上限（C0302），而儲存與進版要接的檢查越來越多
——第 1 層、schema（#39）、跨欄位規則與略過的理由（#41／#42）。
"""

from __future__ import annotations

from subprocess import CalledProcessError

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from config_manager.api.checks import checks_for
from config_manager.api.history import require_entry, source_unreadable
from config_manager.api.schema import as_specified
from config_manager.api.browser import Browsers
from config_manager.api.session import Identity
from config_manager.api.shapes import as_problem, drafts_view
from config_manager.core.drafts import Stage, discard, promote, save_draft
from config_manager.core.errors import (
    DraftInvalid,
    DraftNotFound,
    OverrideRequired,
    PromoteInvalid,
    ReasonInvalid,
    SyntaxParse,
    UnknownPath,
)
from config_manager.core.parse import dump, parse, set_value
from config_manager.core.problem import WARNING
from config_manager.core.validate import check
from config_manager.io.allowed_roots import root_prefixes
from config_manager.io.errors import PromoteLeftBehind, WriterError
from config_manager.io.parsers import read_source as read_source_copy
from config_manager.io.preflight import read_config_list
from config_manager.io.promote import apply as apply_promotions


class DraftInput(BaseModel):
    """儲存草稿的請求：哪份 config（uid）與欄位表送來的改動（路徑 → 新值，#18／#21）。

    送的是**改動**不是整份文字：介面是帶型別的欄位表、不是文字編輯器（ADR-00000013）；伺服器
    拿來源複本當底、以 core/parse.set_value 套上改動再 dump，註解與未改欄位的格式因此保留。
    路徑文法與型別樹一致（`a.b\\.c[2]`）。值由 JSON 帶進來：double 請送小數（如 1.0），
    寫出一定帶小數點。
    """

    uid: str
    edits: dict[str, object]
    # 規則代號 → 略過那條警告的理由（#42）。內容違反規則時，沒有理由就存不成。
    overrides: dict[str, str] = {}


def register_drafts(
    app: FastAPI, repo: str, browsers: Browsers, stage_box: dict[str, Stage]
) -> None:
    """把草稿與進版的端點（三段式的儲存／捨棄／進版，ADR-00000022）掛上 app（#18／#19）。

    抽成一個模組的理由同 `api/history`：`routes.py` 已貼近單檔行數上限（C0302）。
    `stage_box` 是目前階段的容器（不可變 `Stage` 每次換新的進去），`browsers` 給儲存與進版取作者
    （這個瀏覽器的身分，#288）。
    """

    @app.get("/api/drafts")
    def list_drafts() -> dict[str, object]:
        """目前階段裡的草稿（uid 與 format），供工具列顯示「進版 (N)」（#22）。"""
        return drafts_view(stage_box["stage"])

    @app.post("/api/drafts")
    def save_one_draft(
        payload: DraftInput, identity: Identity | None = Depends(browsers.current)
    ) -> dict[str, object]:
        """儲存草稿：把欄位表送來的改動套到來源複本上、跑三層驗證、存進編輯階段（#18／#21）。
        不產生變更紀錄、不寫到目標——那是進版的事。"""
        return _save_draft(repo, identity, stage_box, payload)

    @app.delete("/api/drafts")
    def discard_all_drafts() -> dict[str, object]:
        """捨棄變更（全域）：清空全部草稿，來源與目標皆不動。"""
        return _discard_drafts(stage_box, None)

    @app.delete("/api/drafts/{uid}")
    def discard_one_draft(uid: str) -> dict[str, object]:
        """捨棄變更（單一 config）。"""
        return _discard_drafts(stage_box, uid)

    @app.post("/api/promote")
    def promote_all_drafts(
        identity: Identity | None = Depends(browsers.current),
    ) -> dict[str, object]:
        """進版（全域動作）：全部草稿一次驗證、記錄、寫出，整批原子（#19、ADR-00000022）。"""
        return _promote_all(repo, root_prefixes(repo), identity, stage_box)


def _save_draft(
    repo: str, identity: Identity | None, stage_box: dict[str, Stage], payload: DraftInput
) -> dict[str, object]:
    """儲存草稿的邏輯（#18／#21）：以來源複本為底套上改動、跑驗證、存進階段。不記錄、不寫目標
    ——那是進版的事。

    第 1 層一律跑；這份有 schema 再跑第 2 層（#39）——兩者有問題就是硬擋，422。這份有規則再跑
    第 3 層（#41）：違反規則而請求沒帶理由→409，detail 的 `kind` 是 `override_required`、
    `problems` 列出那幾條警告（各帶 `rule`），介面據此讓人逐條填理由再送（#42）。存成後回草稿
    清單，另帶這份內容的全部警告（`warnings`，含已填理由略過的）。
    """
    if identity is None:
        raise HTTPException(
            status_code=409,
            detail="尚未設定身分，無法儲存草稿。下一步：先 POST /api/session 設定姓名與 email",
        )
    entry = require_entry(read_config_list(repo), payload.uid)
    checks = checks_for(repo, entry, payload.overrides)
    try:
        parsed = parse(read_source_copy(repo, entry.source), entry.format)
        for path, value in as_specified(checks.schema, payload.edits).items():
            set_value(parsed, path, value)
        text = dump(parsed)
    except UnknownPath as error:
        # 路徑指不到值：送錯的請求 → 422，訊息已含路徑與下一步。
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, SyntaxParse) as error:
        # 來源複本讀不到或解析不了：伺服器端資料的問題，非請求端能修——帶訊息的 500。
        raise source_unreadable(entry, error) from error
    try:
        stage_box["stage"] = save_draft(stage_box["stage"], payload.uid, text, entry.format, checks)
    except ReasonInvalid as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OverrideRequired as error:
        # 不是硬擋：填了理由再送就存得成。409 而非 422——內容沒有錯，是還缺使用者的一個決定。
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "override_required",
                "message": str(error),
                "uid": payload.uid,
                "file": entry.source,
                "problems": [as_problem(problem) for problem in error.problems],
            },
        ) from error
    except DraftInvalid as error:
        # 驗證沒過：422，逐條問題（行號／欄位路徑／訊息／建議）原樣回給介面標示在那一列。
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(error),
                "uid": payload.uid,
                "file": entry.source,
                "problems": [as_problem(problem) for problem in error.problems],
            },
        ) from error
    found = check(
        text, entry.format, rules=checks.rules, schema=checks.schema, lintrc=checks.lintrc
    )
    warnings = [as_problem(problem) for problem in found if problem.severity == WARNING]
    return {**drafts_view(stage_box["stage"]), "warnings": warnings}


def _discard_drafts(stage_box: dict[str, Stage], uid: str | None) -> dict[str, object]:
    try:
        stage_box["stage"] = discard(stage_box["stage"], uid)
    except DraftNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return drafts_view(stage_box["stage"])


def _promote_all(
    repo: str, roots: tuple[str, ...], identity: Identity | None, stage_box: dict[str, Stage]
) -> dict[str, object]:
    """進版的邏輯（#19）：core promote 全部驗證 → io apply 寫出＋記錄（失敗整批回滾）→ 清空草稿。"""
    if identity is None:
        raise HTTPException(
            status_code=409,
            detail="尚未設定身分，無法進版——變更紀錄需要作者。"
            "下一步：先 POST /api/session 設定姓名與 email",
        )
    stage = stage_box["stage"]
    if not stage.drafts:
        raise HTTPException(
            status_code=409,
            detail="沒有草稿可進版。下一步：先儲存至少一份草稿（POST /api/drafts）",
        )
    config_list = read_config_list(repo)
    # 進版時依**此刻**的 schema 與規則重驗（#39／#41）：草稿存下之後 schema 被收緊、或規則才
    # 加上而草稿沒有略過它的理由，都在這裡被擋。
    checks = {
        entry.uid: checks_for(repo, entry)
        for entry in config_list.files
        if entry.uid in stage.drafts
    }
    try:
        plans = promote(stage, config_list, checks)
    except PromoteInvalid as error:
        # 任一份沒過整批不進版；指出哪一份（uid）的哪些參數。
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(error),
                "uid": error.uid,
                "file": next((e.source for e in config_list.files if e.uid == error.uid), None),
                "problems": [as_problem(problem) for problem in error.problems],
            },
        ) from error
    try:
        promoted = apply_promotions(repo, plans, identity.git_author, roots)
    except (PromoteLeftBehind, WriterError, CalledProcessError, OSError) as error:
        # 寫出／記錄／回滾失敗：伺服器側的錯，帶訊息的 500（PromoteLeftBehind 指名殘留）。
        raise HTTPException(status_code=500, detail=str(error)) from error
    stage_box["stage"] = discard(stage)  # 進版後草稿清空（T18）
    return {"promoted": promoted, "count": 0}
