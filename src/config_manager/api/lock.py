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
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TypeVar, cast
from datetime import datetime, timedelta, UTC

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from config_manager.api.browser import Browsers, ticket_of
from config_manager.api.errors import InvalidAuthor
from config_manager.api.session import (
    EditingSession,
    Identity,
    SessionExpired,
    SessionHeld,
    SessionLock,
    author,
    session_gone,
)
from config_manager.core.roles import USER, permits
from config_manager.api.shapes import drafts_view
from config_manager.core.drafts import Stage, discard

Clock = Callable[[], datetime]

# 會寫入的請求帶這個分頁的編輯階段識別碼的標頭（F23）。放標頭不放網址：不進伺服器紀錄與瀏覽紀錄；
# 不放 body：沒有 body 的寫入（解除納管、進版、捨棄…）不必為它新增一個。
EDITING_HEADER = "x-cm-editing-token"


def utc_now() -> datetime:
    return datetime.now(UTC)


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


class ExitInput(BaseModel):
    """退出（F3）的請求主體。`confirmed`：人已確認會丟掉的東西；`unsaved`：送請求的分頁的
    區塊裡有還沒儲存的改動（只有頁面知道）；`token`：送請求的分頁持有的階段識別碼（沒有＝
    這個分頁不是持有的那個）。"""

    confirmed: bool = False
    unsaved: bool = False
    token: str | None = None



_Stamp = TypeVar("_Stamp")


def _nothing() -> None:
    return None


class Guard:
    """一把**先到先處理**的行程內鎖（F23）：寫入的「檢查＋寫入」、取得、續期、接續、釋放、逾時回收、
    閒置退出拿同一把，排隊、不交錯。同一個執行緒可以重入（寫入 → 回收 → 閒置退出是巢狀的）。

    `enter(stamp)` 拿號碼牌、在拿號碼牌的同一刻呼叫 `stamp()` 記下到達時間，輪到才回；執行的順序因此
    就是到達的順序。重入不排隊、回 None。`on_wait` 是「有請求開始排隊」的通知：要等之前呼叫一次，
    預設什麼都不做——它只觀察、不影響誰先誰後，只有規格會換掉它。

    規則：只在同步的端點裡拿（async 端點在事件迴圈上拿會卡住整個服務）；拿著時不等別的執行緒。
    **只保證一個行程內的互斥**：服務只能跑一個後端行程（多個 worker 或副本不受支援）。
    """

    def __init__(self) -> None:
        self._turn = threading.Condition()
        self._next = 0  # 下一張號碼牌
        self._serving = 0  # 輪到的號碼牌
        self._owner: int | None = None
        self._depth = 0
        self.on_wait: Callable[[], None] = _nothing

    def enter(self, stamp: Callable[[], _Stamp]) -> _Stamp | None:
        me = threading.get_ident()
        with self._turn:
            if self._owner == me:
                self._depth += 1
                return None
            ticket, self._next = self._next, self._next + 1
            arrived = stamp()
            if ticket != self._serving:
                self.on_wait()
                while ticket != self._serving:
                    self._turn.wait()
            self._owner, self._depth = me, 1
            return arrived

    def leave(self) -> None:
        with self._turn:
            self._depth -= 1
            if self._depth == 0:
                self._owner = None
                self._serving += 1
                self._turn.notify_all()


@dataclass
class LockBox:
    """階段鎖與「上次回收清掉幾份草稿」的容器；掛在 app 上（同 browsers／stage_box）。"""

    lock: SessionLock
    clock: Clock
    cleared_drafts: int = 0
    # 持有階段的是哪個瀏覽器（票，#288）。它閒置逾時退出時，階段跟著釋放（#48）；續期、接續、釋放與
    # 寫入都只對它有效（F23）。階段一沒了就清掉，和階段一起改，不會留著指向已經不持有的票。
    holder_ticket: str | None = None
    # 寫入、釋放、回收與閒置退出排隊用的那一把鎖（F23）。
    guard: Guard = field(default_factory=Guard)
    # 拿著鎖的那一段的時間（不在鎖裡是 None）。只在拿著鎖時讀寫。
    _moment: datetime | None = field(default=None, init=False, repr=False)

    @contextmanager
    def locked(self) -> Iterator[datetime]:
        """拿著鎖做一段事，交出這一段的時間：**拿號碼牌那一刻**讀的時鐘（請求到達的時間）。

        鎖先到先處理，所以執行順序就是到達順序、時間自然不倒退；持有者的續期排在自己的長寫入後面
        時，用的是它到達的時間，排隊的時間不算在它頭上。同一個執行緒重入時沿用外層的時間——一段裡
        只有一個「現在」。沒有跨請求共用的「最晚時間」：撥回時鐘就是撥回（F23 審查後）。
        """
        arrived = self.guard.enter(self.clock)
        outer = arrived is not None
        try:
            if outer:
                self._moment = arrived
            yield cast(datetime, self._moment)
        finally:
            if outer:
                self._moment = None
            self.guard.leave()

    def now(self) -> datetime:
        """現在：拿著鎖的那一段裡是那一段的時間，否則排隊拿一次號碼牌、讀那一刻的時鐘。"""
        with self.locked() as moment:
            return moment

    def holds(self, token: str, ticket: str | None) -> bool:
        """這個識別碼是目前階段的、而且送的就是取得它的那個瀏覽器（F23：識別碼綁瀏覽器）。"""
        return ticket is not None and self.holder_ticket == ticket and self.lock.holds(token)

    def take(self, holder: Identity, ticket: str | None, now: datetime) -> EditingSession:
        """以 `holder` 取得階段並記下是哪個瀏覽器取得的；已被占用丟 `SessionHeld`。"""
        session = self.lock.acquire(holder, now)
        self.holder_ticket = ticket
        return session

    def give_up(self, token: str, ticket: str | None) -> bool:
        """持有的那個瀏覽器釋放；識別碼不對、或送的是別的瀏覽器 → 什麼都不做、回 False。"""
        if not self.holds(token, ticket):
            return False
        self.lock.release(token)
        self.holder_ticket = None
        return True


def seat_browsers(
    stage_box: dict[str, Stage], box: LockBox, idle_timeout: timedelta | None
) -> Browsers:
    """建立各瀏覽器的身分表，並把它接上編輯階段：一個瀏覽器閒置逾時退出時，它持有的階段釋放、
    草稿清除並記下數量（#48）——與續期逾時同一條規則（`sweep`）：不清的話，下一個人會把上一個人
    的草稿以自己的名字進版。時鐘讀 `box.clock`（每次都讀，測試換掉它兩邊一起變）。"""

    def release_idle(ticket: str) -> None:
        current = box.lock.current
        if current is None or box.holder_ticket != ticket:
            return
        box.lock.release(current.token)
        box.holder_ticket = None
        box.cleared_drafts += len(stage_box["stage"].drafts)
        stage_box["stage"] = discard(stage_box["stage"])

    return Browsers(box.now, idle_timeout, release_idle, locked=box.locked)


def register_session(
    app: FastAPI, browsers: Browsers, stage_box: dict[str, Stage], box: LockBox
) -> None:
    """把身分（POST／GET／DELETE /api/session）與編輯階段的端點掛上 app。身分與階段是兩件事
    （T13），但設身分要看階段有沒有被別人持有，所以接線放在一起。抽出來的理由同
    `register_history`（C901）。

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
        # 「別人有沒有持有」和「記下身分」是一段：中間不讓別人插進來取得（F23）。
        with box.locked():
            identity = _checked_identity(browsers, box, stage_box, payload)
            browsers.declare(request, response, identity)
        return _as_session(identity)

    @app.get("/api/session")
    def get_session(
        identity: Identity | None = Depends(browsers.current),
    ) -> dict[str, str] | None:
        """這個瀏覽器目前的身分，尚未輸入（或已閒置逾時退出）則回 null。"""
        return _as_session(identity) if identity else None

    @app.get("/api/session/idle")
    def idle_status(request: Request) -> dict[str, object]:
        """這個瀏覽器的閒置狀態（#48）：還剩幾秒、何時開始提示、是不是剛因閒置被退出。開發模式
        不逾時，`timeout_seconds` 是 null。問這一支**不算操作**。"""
        return browsers.idle_view(request)

    @app.post("/api/session/activity")
    def note_activity(request: Request) -> dict[str, object]:
        """這個瀏覽器的人有在操作（頁面回報，或按了「繼續使用」）：重新起算閒置。已逾時就不動
        ——逾時是退出，要重新輸入身分。回更新後的閒置狀態。"""
        browsers.active(request)
        return browsers.idle_view(request)

    @app.delete("/api/session")
    def leave_session(payload: ExitInput, request: Request) -> dict[str, object]:
        """退出（F3，本 repo 對 §3.5.3 的追加）：清掉這個瀏覽器的身分，好換人、換角色。只影響送請求
        的瀏覽器——票留著，別的瀏覽器的身分、編輯權與草稿都不動；不必帶識別碼（同一個瀏覽器的唯讀
        分頁也能退出），沒有身分也照樣成功。

        這個瀏覽器持有編輯階段時當場釋放，並清掉**目前全部共用草稿**（暫行規則，F22 未解：可能包含
        接手來的草稿）；下一個取得的人不會被告知份數（那句話只用在逾時）。有東西會丟——持有時有
        草稿、區塊裡有沒存的改動、持有的是同一個瀏覽器的另一個分頁——而沒帶 `confirmed` → 409
        `confirm_required`，什麼都不動。這個瀏覽器其實已經閒置逾時 → 帶 `timed_out: true`。"""
        with box.locked():
            return _leave(browsers, stage_box, box, payload, request)

    register_lock(app, browsers, stage_box, box)


def register_lock(
    app: FastAPI, browsers: Browsers, stage_box: dict[str, Stage], box: LockBox
) -> None:
    """把編輯階段的四支端點掛上 app。"""

    @app.get("/api/session/lock")
    def lock_status() -> dict[str, object]:
        """誰在編輯（無人→`held: false`）。不回識別碼。"""
        with box.locked():
            sweep(browsers, stage_box, box)
            return _status(box)

    @app.post("/api/session/lock")
    def acquire_lock(request: Request) -> dict[str, object]:
        """以這個瀏覽器的身分取得編輯階段；已被占用→409（持有者姓名、email、開始時間）。回收、
        查身分、取得在同一段鎖裡（F23）。"""
        with box.locked() as now:
            sweep(browsers, stage_box, box)
            identity = browsers.current(request)
            if identity is None:
                raise HTTPException(
                    status_code=409,
                    detail="尚未設定身分，無法取得編輯階段。"
                    "下一步：先 POST /api/session 設定姓名與 email",
                )
            try:
                session = box.take(identity, ticket_of(request), now)
            except SessionHeld as error:
                raise HTTPException(status_code=409, detail=_held_detail(error)) from error
            cleared, box.cleared_drafts = box.cleared_drafts, 0
        return {**_session_view(session), "token": session.token, "cleared_drafts": cleared}

    @app.post("/api/session/lock/renew")
    def renew_lock(payload: TokenInput, request: Request) -> dict[str, object]:
        """續期；階段已失效→410（不替你重新取得，前端轉唯讀並說明）。回應帶這個分頁接下來要用
        的識別碼（`token`）：一般續期不變，`resume`（重新整理後接續）會換新的。只對取得這個階段的
        瀏覽器有效（F23）：別的瀏覽器拿著識別碼來續期、接續，和識別碼失效一樣是 410。"""
        with box.locked():
            return _renew(browsers, stage_box, box, payload, ticket_of(request))

    @app.post("/api/session/lock/release")
    def release_lock_beacon(
        request: Request, token: str = Depends(_beacon_token)
    ) -> dict[str, object]:
        """頁面關閉時以 `navigator.sendBeacon` 釋放。beacon 只能 POST，且為了不觸發跨來源預檢
        （關頁當下預檢多半來不及）以 text/plain 送——body 由 `_beacon_token` 自己解析。

        本體是同步的：拿鎖要在執行緒池裡拿，在事件迴圈上拿會卡住整個服務（F23）。讀 body 是 async，
        放在依賴裡、不拿鎖。"""
        with box.locked():
            return {"released": box.give_up(token, ticket_of(request))}

    @app.delete("/api/session/lock")
    def release_lock(payload: TokenInput, request: Request) -> dict[str, object]:
        """持有者主動釋放（正常關閉頁面）。身分留著：重新整理也會觸發釋放，同一個人回來不必再填。
        只對取得這個階段的瀏覽器有效（F23）。"""
        with box.locked():
            return {"released": box.give_up(payload.token, ticket_of(request))}


async def _beacon_token(request: Request) -> str:
    """beacon 的 body（text/plain 的 JSON）裡的識別碼；讀不出來 422。"""
    try:
        return str(json.loads(await request.body())["token"])
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(
            status_code=422,
            detail="釋放編輯階段的請求內容讀不出識別碼（需要 {\"token\": …}）。"
            "下一步：送出這個分頁取得階段時拿到的 token",
        ) from error


def _no_earlier_than_last_renewal(box: LockBox) -> datetime:
    """續期用的時間：這一段的時間，但不早於目前階段的最後續期時間——最後續期時間只增不減，系統
    時間被調回去也不會把它寫回過去（F23）。"""
    now = box.now()
    current = box.lock.current
    return current.renewed_at if current is not None and current.renewed_at > now else now


def _renew(
    browsers: Browsers,
    stage_box: dict[str, Stage],
    box: LockBox,
    payload: TokenInput,
    ticket: str | None,
) -> dict[str, object]:
    # 先在 API 層 sweep：逾時的階段要在這裡清草稿、清身分並記下數量——SessionLock 自己的
    # sweep 只會把階段丟掉，之後就沒人知道有東西該清。
    sweep(browsers, stage_box, box)
    try:
        if not box.holds(payload.token, ticket):
            raise session_gone(payload.token)
        keep = box.lock.resume if payload.resume else box.lock.renew
        session = keep(payload.token, _no_earlier_than_last_renewal(box))
    except SessionExpired as error:
        raise HTTPException(status_code=410, detail=str(error)) from error
    return {**_session_view(session), "token": session.token}


def _leave(
    browsers: Browsers,
    stage_box: dict[str, Stage],
    box: LockBox,
    payload: ExitInput,
    request: Request,
) -> dict[str, object]:
    sweep(browsers, stage_box, box)
    if browsers.timed_out(request):
        # 其實已經閒置逾時（頁面還沒問到）：逾時那一刻已照逾時的規則釋放、清草稿。照實說，不記成
        # 已退出（閒置狀態仍說逾時）；也不必確認——身分已經沒了，沒有東西留得住。
        return {"left": True, "released": False, "cleared_drafts": 0, "timed_out": True}
    ticket = ticket_of(request)
    current = box.lock.current
    held = current if ticket is not None and box.holder_ticket == ticket else None
    drafts = len(stage_box["stage"].drafts) if held is not None else 0
    other_tab = held is not None and payload.token != held.token
    if not payload.confirmed and (drafts or other_tab or payload.unsaved):
        raise HTTPException(
            status_code=409,
            detail={
                "kind": "confirm_required", "drafts": drafts, "other_tab": other_tab,
                "unsaved": payload.unsaved, "message": _CONFIRM_EXIT,
            },
        )
    if held is not None:
        box.give_up(held.token, ticket)
        stage_box["stage"] = discard(stage_box["stage"])
    browsers.leave(request)
    return {"left": True, "released": held is not None, "cleared_drafts": drafts}


_CONFIRM_EXIT = (
    "退出會丟掉東西（份數與種類見 drafts、unsaved、other_tab），這次沒有退出。"
    "下一步：確定要退出就帶 confirmed: true 再送一次 DELETE /api/session；不想丟就先進版或儲存"
)


def sweep(browsers: Browsers, stage_box: dict[str, Stage], box: LockBox) -> None:
    """回收逾時的階段：草稿一併清除並記下數量（下次取得時回報）。先讓閒置逾時的瀏覽器退出（#48，
    它持有的階段在那裡釋放），再回收續期逾時的。續期逾時不清身分（分頁斷了，人不一定走了）。"""
    with box.locked() as now:
        browsers.sweep()
        for _expired in box.lock.sweep(now):
            box.holder_ticket = None
            box.cleared_drafts += len(stage_box["stage"].drafts)
            stage_box["stage"] = discard(stage_box["stage"])


@dataclass
class Editing:
    """寫入的門檻（F23）：只接受持有編輯權的那個分頁。

    會寫入的端點都以 `with editing.write(request, "<動作>") as identity:` 包住「業務檢查＋寫入」。
    請求要在 `X-CM-Editing-Token` 帶目前編輯階段的識別碼，而且送請求的瀏覽器票就是取得它的那一張
    （另一個瀏覽器拿到識別碼也不行）。不寫入的端點（讀取、身分、編輯階段本身、偵測）不經過這裡。
    """

    browsers: Browsers
    stage_box: dict[str, Stage]
    box: LockBox

    @contextmanager
    def write(self, request: Request, action: str) -> Iterator[Identity]:
        """依序：回收逾時的 → 帶了識別碼但不對（已釋放、逾時被收回、被接手、別的瀏覽器）→ 410
        `editing_lost`；沒帶也沒身分 → 409 先設身分；有身分沒帶 → 410 `editing_missing`。之後才是
        角色（403）與各端點自己的檢查，都在 yield 之內。"""
        # 「檢查＋寫入」整段拿著鎖（F23 條件 2）：和釋放、回收、接手排隊，檢查通過之後不會有人在
        # 寫入之前把編輯權拿走。
        with self.box.locked():
            sweep(self.browsers, self.stage_box, self.box)
            identity = self.browsers.current(request)
            token = request.headers.get(EDITING_HEADER, "")
            if token and not self.box.holds(token, ticket_of(request)):
                raise _refused("editing_lost", _LOST.format(action=action))
            if not token and identity is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"尚未設定身分，無法{action}。"
                    "下一步：先 POST /api/session 設定姓名與 email",
                )
            if not token:
                raise _refused("editing_missing", _MISSING.format(action=action))
            if identity is None:
                # 同一段裡只有一個「現在」：持有者的票閒置退出時階段已一起釋放（release_idle），
                # 走不到這裡；萬一走到，照失效說。
                raise _refused("editing_lost", _LOST.format(action=action))
            yield identity


_LOST = (
    "這個分頁的編輯權已失效（已釋放或退出、逾時被收回，或已被別人接手），這次的{action}沒有寫進去。"
    "下一步：重新整理頁面以重新取得編輯權；有人正在編輯時會以唯讀進入"
)
_MISSING = (
    "這個分頁沒有編輯權，這次的{action}沒有寫進去——一次只有一個分頁能修改。"
    "下一步：重新整理頁面；有人正在編輯時，等對方離開或逾時釋放後再試"
)


def _refused(kind: str, message: str) -> HTTPException:
    return HTTPException(status_code=410, detail={"kind": kind, "message": message})


def _checked_identity(
    browsers: Browsers, lock_box: LockBox, stage_box: dict[str, Stage], payload: SessionInput
) -> Identity:
    """設定身分的檢查。422：值不合法（訊息已是可行動的樣子，原樣傳）；409：別人正持有編輯階段
    ——後來者此時進不了編輯，帶持有者資訊讓前端以唯讀進清單並說明是誰（#33、#288 的條件 7）；
    持有者本人再設一次不擋（他的另一個分頁、或重新選角色）。"""
    try:
        identity = author(payload.name, payload.email, payload.role)
    except InvalidAuthor as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    occupied = held_by_other(browsers, lock_box, identity, stage_box)
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
    browsers: Browsers, box: LockBox, identity: Identity, stage_box: dict[str, Stage]
) -> SessionHeld | None:
    """別人正持有編輯階段時，換身分會把持有者的變更紀錄掛到別人頭上——擋在設定身分這一步。
    同一個人（姓名＋email 相同）再設一次不擋（第二個分頁：之後取得階段會被 409、轉唯讀）。"""
    sweep(browsers, stage_box, box)
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
    "require_permission", "seat_browsers", "sweep", "utc_now",
]
