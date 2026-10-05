"""api/browser — 身分屬於各自的瀏覽器（#288；設計文件 §7.8；ADR-00000020）。

身分的唯一目的是讓變更可追溯到人。身分只有一份、全域的話，第一個人輸入之後，別的瀏覽器、別台電腦
開網頁都直接變成他——之後的變更就記到錯的人名下，變更紀錄因此不可信。所以身分以**瀏覽器票**為鍵：
後端發一張隨機的票寫進該瀏覽器的 cookie（HttpOnly、SameSite=Lax、關閉瀏覽器即失效），每次請求自動
帶上；沒帶票或票上沒身分，就是「還沒說自己是誰」。

**這仍然不是認證**：票只是把「這個瀏覽器」與「它宣告的身分」綁在一起，不證明宣告是真的。

同一個瀏覽器的分頭共用同一張票（cookie 是瀏覽器層級的），所以第二個分頭不必再填身分——編輯階段的
鎖（`api/session.SessionLock`）另外保證一次只有一個人在編輯，後來的分頭唯讀。

「記住此裝置」不在這裡：那是前端把姓名與 Email 存在瀏覽器自己的儲存空間、下次預填（角色每次重選），
後端不存、重啟不受影響（#288 的定案 2）。
"""

import re
import secrets

from fastapi import Request, Response

from config_manager.api.session import Identity

COOKIE = "cm_browser"

# 瀏覽器送回來的票：只收這個形狀的（token_urlsafe 產生的字元、長度有上限）。不合形狀的當作沒帶——
# 票是鍵，不是秘密，所以收下一張沒見過但形狀合法的票（例如後端重啟前發的）沒有壞處；形狀不合的
# 丟掉是為了不讓 cookie 裡的任意內容變成字典的鍵。
_TICKET = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class Browsers:
    """每個瀏覽器各自的身分，以票為鍵。掛在 app 上（同 stage_box），同一行程起兩個 app 互不相見。

    只會長不會縮：每個第一次說自己是誰的瀏覽器各佔一格（幾十個位元組）。閒置回收是 #48 的事。
    """

    def __init__(self) -> None:
        self._identities: dict[str, Identity] = {}

    def current(self, request: Request) -> Identity | None:
        """這個請求的瀏覽器宣告過的身分；沒票或票上沒身分回 None。給端點以 `Depends` 取用。"""
        ticket = ticket_of(request)
        return self._identities.get(ticket) if ticket else None

    def declare(self, request: Request, response: Response, identity: Identity) -> None:
        """記下這個瀏覽器的身分。沒票（或票的形狀不對）就發一張新的、寫進回應的 cookie。"""
        ticket = ticket_of(request) or secrets.token_urlsafe(32)
        self._identities[ticket] = identity
        response.set_cookie(COOKIE, ticket, httponly=True, samesite="lax", path="/")


def ticket_of(request: Request) -> str | None:
    """請求帶的票；沒帶或形狀不合回 None。"""
    raw = request.cookies.get(COOKIE, "")
    return raw if _TICKET.match(raw) else None
