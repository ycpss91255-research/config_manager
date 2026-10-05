"""api/lock — 編輯階段的端點（#33；§7.2.2、ADR-00000014；T13）。

一次只有一個編輯階段。頁面載入時取得（`POST /api/session/lock`），定期續期（`…/renew`），關閉頁面
時釋放（`DELETE`）；已被占用回 409 並說出持有者姓名、email、開始時間，前端據此顯示唯讀。不提供
強制接管——持有者逾時後自然釋放（部署模式 `CM_SESSION_TIMEOUT` 秒；開發模式不逾時，模式判定是 #47）。

這四支是本 repo 對 §3.5.3 的追加（比照 GET /api/session／#122）。時鐘由 `clock` 注入（T13：讀系統
時間的實作測不了逾時）；預設 UTC 現在。**階段逾時釋放時草稿一併清除**，並在下一次取得的回應裡
回報「有 N 份草稿被清除」，不靜默丟棄。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from config_manager.api.browser import Browsers
from config_manager.api.errors import InvalidAuthor
from config_manager.api.session import (
    EditingSession,
    Identity,
    SessionExpired,
    SessionHeld,
    SessionLock,
    author,
)
from config_manager.core.roles import USER, permits
from config_manager.api.shapes import drafts_view
from config_manager.core.drafts import Stage, discard

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TokenInput(BaseModel):
    """續期／釋放：這個分頁持有的階段識別碼。`resume` 只用於續期：頁面重新整理後回來接續時
    設為 true，後端會換一個新的識別碼（見 `SessionLock.resume`）。"""

    token: str
    resume: bool = False


class SessionInput(BaseModel):
    """身分輸入的請求主體。角色預設為一般使用者（預設值落向安全，不變式 4）。"""

    name: str
    email: str
    role: str = USER



@dataclass
class LockBox:
    """階段鎖與「上次回收清掉幾份草稿」的容器；掛在 app 上（同 browsers／stage_box）。"""

    lock: SessionLock
    clock: Clock
    cleared_drafts: int = 0


def register_session(
    app: FastAPI, browsers: Browsers, stage_box: dict[str, Stage], box: LockBox
) -> None:
    """把身分（POST／GET /api/session）與編輯階段的端點掛上 app。身分與階段是兩件事（T13），但
    設身分要看階段有沒有被別人持有，所以接線放在一起。抽出來的理由同 `register_history`（C901）。

    身分屬於各自的瀏覽器（#288）：`browsers` 以瀏覽器票為鍵記身分，端點以
    `Depends(browsers.current)` 取「這個請求的瀏覽器」宣告過的身分。"""

    @app.post("/api/session")
    def set_session(
        payload: SessionInput, request: Request, response: Response
    ) -> dict[str, str]:
        """設定使用者身分（設計文件 §3.5.3）。

        **這不是登入。** 沒有密碼、不驗證、角色是自我宣告（ADR-00000020）。身分記在這個瀏覧器的
        票上；第一次來會發票（回應的 Set-Cookie）。
        """
        identity = _checked_identity(box, stage_box, payload)
        browsers.declare(request, response, identity)
        return _as_session(identity)

    @app.get("/api/session")
    def get_session(
        identity: Identity | None = Depends(browsers.current),
    ) -> dict[str, str] | None:
        """這個瀏覽器目前的身分，尚未輸入則回 null。"""
        return _as_session(identity) if identity else None

    register_lock(app, browsers, stage_box, box)


def register_lock(
    app: FastAPI, browsers: Browsers, stage_box: dict[str, Stage], box: LockBox
) -> None:
    """把編輯階段的四支端點掛上 app。"""

    @app.get("/api/session/lock")
    def lock_status() -> dict[str, object]:
        """誰在編輯（無人→`held: false`）。不回識別碼。"""
        sweep(stage_box, box)
        return _status(box)

    @app.post("/api/session/lock")
    def acquire_lock(identity: Identity | None = Depends(browsers.current)) -> dict[str, object]:
        """以這個瀏覽器的身分取得編輯階段；已被占用→409（持有者姓名、email、開始時間）。"""
        sweep(stage_box, box)
        if identity is None:
            raise HTTPException(
                status_code=409,
                detail="尚未設定身分，無法取得編輯階段。"
                "下一步：先 POST /api/session 設定姓名與 email",
            )
        try:
            session = box.lock.acquire(identity, box.clock())
        except SessionHeld as error:
            raise HTTPException(status_code=409, detail=_held_detail(error)) from error
        cleared, box.cleared_drafts = box.cleared_drafts, 0
        return {**_session_view(session), "token": session.token, "cleared_drafts": cleared}

    @app.post("/api/session/lock/renew")
    def renew_lock(payload: TokenInput) -> dict[str, object]:
        """續期；階段已失效→410（不替你重新取得，前端轉唯讀並說明）。回應帶這個分頁接下來要用
        的識別碼（`token`）：一般續期不變，`resume`（重新整理後接續）會換新的。"""
        # 先在 API 層 sweep：逾時的階段要在這裡清草稿、清身分並記下數量——SessionLock 自己的
        # sweep 只會把階段丟掉，之後就沒人知道有東西該清。
        sweep(stage_box, box)
        try:
            keep = box.lock.resume if payload.resume else box.lock.renew
            session = keep(payload.token, box.clock())
        except SessionExpired as error:
            raise HTTPException(status_code=410, detail=str(error)) from error
        return {**_session_view(session), "token": session.token}

    @app.post("/api/session/lock/release")
    async def release_lock_beacon(request: Request) -> dict[str, object]:
        """頁面關閉時以 `navigator.sendBeacon` 釋放。beacon 只能 POST，且為了不觸發跨來源預檢
        （關頁當下預檢多半來不及）以 text/plain 送——所以這裡自己把 body 當 JSON 解析。"""
        try:
            token = json.loads(await request.body())["token"]
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(
                status_code=422,
                detail="釋放編輯階段的請求內容讀不出識別碼（需要 {\"token\": …}）。"
                "下一步：送出這個分頁取得階段時拿到的 token",
            ) from error
        return {"released": box.lock.release(str(token))}

    @app.delete("/api/session/lock")
    def release_lock(payload: TokenInput) -> dict[str, object]:
        """持有者主動釋放（正常關閉頁面）。身分留著：重新整理也會觸發釋放，同一個人回來不必再填。"""
        return {"released": box.lock.release(payload.token)}


def sweep(stage_box: dict[str, Stage], box: LockBox) -> None:
    """回收逾時的階段：草稿一併清除並記下數量（下次取得時回報）。身分不隨階段清掉（見 release）。"""
    for _expired in box.lock.sweep(box.clock()):
        box.cleared_drafts += len(stage_box["stage"].drafts)
        stage_box["stage"] = discard(stage_box["stage"])


def _checked_identity(
    lock_box: LockBox, stage_box: dict[str, Stage], payload: SessionInput
) -> Identity:
    """設定身分的檢查。422：值不合法（訊息已是可行動的樣子，原樣傳）；409：別人正持有編輯階段
    ——後來者此時進不了編輯，帶持有者資訊讓前端以唯讀進清單並說明是誰（#33、#288 的條件 7）；
    持有者本人再設一次不擋（他的另一個分頁、或重新選角色）。"""
    try:
        identity = author(payload.name, payload.email, payload.role)
    except InvalidAuthor as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    occupied = held_by_other(lock_box, identity, stage_box)
    if occupied is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "held", "message": str(occupied),
                "holder": {"name": occupied.holder.name, "email": occupied.holder.email},
                "started_at": occupied.started_at.isoformat(),
            },
        )
    return identity


def _as_session(identity: Identity) -> dict[str, str]:
    """身分在畫面上需要的欄位。git_author 一併回傳，讓「紀錄上會是誰」看得見。"""
    return {
        "name": identity.name,
        "email": identity.email,
        "role": identity.role,
        "git_author": identity.git_author,
    }


def held_by_other(
    box: LockBox, identity: Identity, stage_box: dict[str, Stage]
) -> SessionHeld | None:
    """別人正持有編輯階段時，換身分會把持有者的變更紀錄掛到別人頭上——擋在設定身分這一步。
    同一個人（姓名＋email 相同）再設一次不擋（第二個分頁：之後取得階段會被 409、轉唯讀）。"""
    sweep(stage_box, box)
    current = box.lock.current
    if current is None:
        return None
    if (current.holder.name, current.holder.email) == (identity.name, identity.email):
        return None
    return SessionHeld(current.holder, current.started_at)


def require_permission(identity: Identity | None, action: str) -> Identity:
    """要求這個瀏覽器的身分被允許做 `action`，回傳該身分，否則以具名 HTTP 錯誤擋下。

    `action` 是 `core/roles` 的動作代號（同時就是介面上的那句話）：誰能做什麼只問那一張表
    （#47），這裡不寫 `role == "developer"`。白名單維護（含候選數預覽，#206）、型別指定（含產生
    schema 骨架，#38）、屬性編輯都走這道門檻：**沒有身分 → 409**（先設身分）、**不被允許 → 403**。
    開發／部署模式不進來：角色與環境正交，部署環境下開發者仍能維護白名單。
    """
    if identity is None:
        raise HTTPException(
            status_code=409,
            detail=f"尚未設定身分，無法{action}。下一步：先 POST /api/session 設定姓名與 email",
        )
    if not permits(identity.role, action):
        raise HTTPException(
            status_code=403,
            detail=f"只有開發者能{action}。下一步：以開發者身分進入，或請開發者代為處理",
        )
    return identity


def _held_detail(error: SessionHeld) -> dict[str, object]:
    return {
        "kind": "held",
        "message": str(error),
        "holder": {"name": error.holder.name, "email": error.holder.email},
        "started_at": error.started_at.isoformat(),
    }


def _session_view(session: EditingSession) -> dict[str, object]:
    return {
        "holder": {"name": session.holder.name, "email": session.holder.email},
        "started_at": session.started_at.isoformat(),
        "renewed_at": session.renewed_at.isoformat(),
    }


def _status(box: LockBox) -> dict[str, object]:
    current = box.lock.current
    if current is None:
        return {"held": False, "timeout_seconds": _timeout_seconds(box)}
    return {"held": True, **_session_view(current), "timeout_seconds": _timeout_seconds(box)}


def _timeout_seconds(box: LockBox) -> float:
    return box.lock.timeout.total_seconds()


__all__ = [
    "LockBox", "SessionInput", "drafts_view", "register_lock", "register_session",
    "require_permission", "sweep", "utc_now",
]
