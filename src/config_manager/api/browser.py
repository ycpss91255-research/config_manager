"""api/browser — 身分屬於各自的瀏覽器（#288；設計文件 §7.8；ADR-00000020）。

身分的唯一目的是讓變更可追溯到人。身分只有一份、全域的話，第一個人輸入之後，別的瀏覽器、別台電腦
開網頁都直接變成他——之後的變更就記到錯的人名下，變更紀錄因此不可信。所以身分以**瀏覽器票**為鍵：
後端發一張隨機的票寫進該瀏覽器的 cookie（HttpOnly、SameSite=Lax、關閉瀏覽器即失效），每次請求自動
帶上；沒帶票或票上沒身分，就是「還沒說自己是誰」。

**這仍然不是認證**：票只是把「這個瀏覽器」與「它宣告的身分」綁在一起，不證明宣告是真的。

同一個瀏覽器的分頁共用同一張票（cookie 是瀏覽器層級的），所以第二個分頁不必再填身分——編輯階段的
鎖（`api/session.SessionLock`）另外保證一次只有一個人在編輯，後來的分頁唯讀。

「記住此裝置」不在這裡：那是前端把姓名與 Email 存在瀏覽器自己的儲存空間、下次預填（角色每次重選），
後端不存、重啟不受影響（#288 的定案 2）。

**閒置逾時（#48）也以票為單位**：部署模式下，一個瀏覽器的人太久沒操作就退出（身分清掉）。邏輯在
`api/session.IdleSeats`（純邏輯、收 `now`）；這裡是它與 HTTP 之間的那一層——從 cookie 取票、讀注入的
時鐘、把「這張票剛逾時」交給 `on_timeout`（接線的人據此釋放它持有的編輯階段）。

**退出（F3）也以票為單位**：人按「退出」，這個瀏覽器的身分清掉、記下「已退出」，直到它重新說自己是
誰。票留著——同一個瀏覽器的其他分頁靠它問得出「這個瀏覽器已退出」，而不是當成閒置逾時或服務重啟。
"""

import re
import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta

from fastapi import Request, Response

from config_manager.api.session import IdleSeats, Identity, idle_warning

COOKIE = "cm_browser"

# 瀏覽器送回來的票：只收這個形狀的（token_urlsafe 產生的字元、長度有上限）。不合形狀的當作沒帶——
# 票是鍵，不是秘密，所以收下一張沒見過但形狀合法的票（例如後端重啟前發的）沒有壞處；形狀不合的
# 丟掉是為了不讓 cookie 裡的任意內容變成字典的鍵。
_TICKET = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class Browsers:
    """每個瀏覽器各自的身分，以票為鍵。掛在 app 上（同 stage_box），同一行程起兩個 app 互不相見。

    `idle_timeout` 是閒置逾時（None＝不逾時，開發模式）；`clock` 是注入的時鐘（T13：讀系統時間的
    實作測不了逾時）。`on_timeout(票)` 在一張票剛因閒置退出時被呼叫一次。`locked` 給出和編輯階段
    共用的那一把鎖（F23）：身分表的讀寫和寫入、釋放、回收排在一起，也不會在走訪時被別的執行緒改掉；
    `clock` 在鎖裡讀到的是那一段的時間。每個操作只讀一次時鐘——同一個請求裡回收與查身分用同一個
    「現在」。
    """

    def __init__(
        self,
        clock: Callable[[], datetime],
        idle_timeout: timedelta | None = None,
        on_timeout: Callable[[str], None] = lambda _ticket: None,
        locked: Callable[[], AbstractContextManager[object]] = nullcontext,
    ) -> None:
        self._seats = IdleSeats(idle_timeout)
        self._clock = clock
        self._on_timeout = on_timeout
        self._locked = locked
        # 已退出、還沒重新說自己是誰的票（F3）。
        self._left: set[str] = set()

    def current(self, request: Request) -> Identity | None:
        """這個請求的瀏覽器宣告過的身分；沒票、票上沒身分、已閒置逾時或已退出回 None。給端點以
        `Depends` 取用。"""
        with self._locked():
            now = self._clock()
            self._expire(now)
            ticket = ticket_of(request)
            if not ticket or ticket in self._left:
                return None
            return self._seats.identity(ticket, now)

    def declare(self, request: Request, response: Response, identity: Identity) -> None:
        """記下這個瀏覽器的身分。沒票（或票的形狀不對）就發一張新的、寫進回應的 cookie。"""
        ticket = ticket_of(request) or secrets.token_urlsafe(32)
        with self._locked():
            self._seats.declare(ticket, identity, self._clock())
            self._left.discard(ticket)
        response.set_cookie(COOKIE, ticket, httponly=True, samesite="lax", path="/")

    def sweep(self) -> None:
        """讓閒置逾時的瀏覽器退出，並逐張交給 `on_timeout`。任何會看身分或編輯階段的請求都先掃
        一次，所以逾時的人在別人碰到他留下的東西之前就已經退出。"""
        with self._locked():
            self._expire(self._clock())

    def _expire(self, now: datetime) -> None:
        for ticket in self._seats.expire(now):
            self._on_timeout(ticket)

    def active(self, request: Request) -> None:
        """這個瀏覽器的人剛才有在操作：重新起算閒置。已逾時就不動（逾時是退出，不是暫停）。"""
        with self._locked():
            now = self._clock()
            self._expire(now)
            ticket = ticket_of(request)
            if ticket and ticket not in self._left:
                self._seats.touch(ticket, now)

    def timed_out(self, request: Request) -> bool:
        """這個瀏覽器是不是因閒置被退出、而且還沒重新說自己是誰（已退出的不算）。"""
        ticket = ticket_of(request)
        if not ticket:
            return False
        with self._locked():
            self._expire(self._clock())
            return ticket not in self._left and self._seats.timed_out(ticket)

    def leave(self, request: Request) -> None:
        """這個瀏覽器退出（F3）：身分清掉、記下「已退出」。票留著（見模組說明）；沒票什麼都不做。"""
        ticket = ticket_of(request)
        if ticket:
            with self._locked():
                self._left.add(ticket)

    def idle_view(self, request: Request) -> dict[str, object]:
        """這個瀏覽器的閒置狀態，給介面顯示提示用：逾時與提示的秒數（不逾時為 null）、還剩幾秒
        （沒身分為 null）、是不是剛因閒置被退出、是不是按了退出（F3；退出優先）。三者都不是而且
        沒身分＝後端沒有這張票的紀錄（例如服務重啟過）。判定在後端，介面只顯示。"""
        ticket = ticket_of(request) or ""
        timeout = self._seats.timeout
        with self._locked():
            now = self._clock()
            self._expire(now)
            left = ticket in self._left
            remaining = None if left else self._seats.remaining(ticket, now)
            timed_out = not left and self._seats.timed_out(ticket)
        return {
            "timeout_seconds": None if timeout is None else timeout.total_seconds(),
            "warn_seconds": None if timeout is None else idle_warning(timeout).total_seconds(),
            "remaining_seconds": None if remaining is None else remaining.total_seconds(),
            "timed_out": timed_out,
            "left": left,
        }


def ticket_of(request: Request) -> str | None:
    """請求帶的票；沒帶或形狀不合回 None。"""
    raw = request.cookies.get(COOKIE, "")
    return raw if _TICKET.match(raw) else None
