"""api/session — 身分與編輯階段（設計文件 §7.8；測試介面 T13）。

**這不是認證機制。** 沒有密碼、沒有驗證、角色是自我宣告（ADR-00000020）。唯一的
用途是成為變更紀錄的作者，使變更可追溯到人。介面文案用「身分輸入」而非「登入」
——說「登入」會讓使用者以為系統有存取控制（CONTEXT.md「避免使用的說法」）。

身分（誰）與階段（誰正在編輯）是兩件事：使用者可以只是看清單，那不需要取得階段。
階段（`SessionLock`，#33、§7.2.2、ADR-00000014）：一次只有一個編輯階段；已被占用就拒絕並說出
持有者是誰、從何時開始；不提供強制接管——持有者逾時後自然釋放，其他人再取得。

純邏輯，不做 I/O，**也不讀時鐘**：階段的每個操作都收 `now`，逾時才測得出來（T13）。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import NamedTuple

from config_manager.api.errors import InvalidAuthor
# 角色的值定義在核心層的權限對照表（`core/roles`，#47）：這裡只驗「輸入是不是其中之一」，
# 「哪個角色能做什麼」由那張表回答，不在這裡另寫一份。
from config_manager.core.roles import DEVELOPER, ROLES, USER

# git 的作者字串是 `姓名 <email>`。這三個字元會拆散它：角括號讓作者變成另一個人，
# `<`／`>` 破壞 git 的作者字串；換行讓後面的內容變成另一行。`\x1f`／`\x1e` 是 io/git.history()
# 切變更紀錄欄位／分筆用的分隔符——混進作者會讓整庫帳本讀不出來（#212），在輸入這關就擋下。
# NUL 放行的話會被存進身分，納管時帶進 git commit → subprocess 以 ValueError(embedded null byte)
# 失敗（非 OSError，端點 except 接不到）而成裸 500（#257）；在輸入這關具名拒絕，比照 `<`。
_FORBIDDEN = ("<", ">", "\n", "\r", "\x1f", "\x1e", "\x00")


class Identity(NamedTuple):
    """一個人的身分。角色是他自己宣告的，不是系統判定的。"""

    name: str
    email: str
    role: str

    @property
    def git_author(self) -> str:
        """io/git.record 收的作者格式。"""
        return f"{self.name} <{self.email}>"


def author(name: str, email: str, role: str) -> Identity:
    """由輸入的姓名、email 與角色建立身分。不合法則丟具名例外。"""
    clean_name = _checked("姓名", name)
    clean_email = _checked("email", email)

    if role not in ROLES:
        raise InvalidAuthor(
            f"角色「{role}」不在允許的值裡：{USER}、{DEVELOPER}。"
            f"下一步：在身分輸入頁選擇其中一個"
        )

    return Identity(clean_name, clean_email, role)


def _checked(field: str, value: str) -> str:
    """修剪前後空白並拒絕會破壞 git 作者字串的字元。

    修剪是安全的：貼上來的字串常帶空白，去掉它不改變身分。清洗掉 `<` 就不同了
    ——那會改變身分，而變更紀錄上的名字若與使用者輸入的不同，紀錄與事實就不符。
    紀錄的用途正是追溯到人（ADR-00000020），所以這裡拒絕，不清洗（不變式 2）。
    """
    trimmed = (value or "").strip()
    if not trimmed:
        raise InvalidAuthor(f"{field}是必填的。下一步：在身分輸入頁填寫它")

    for character in _FORBIDDEN:
        if character in trimmed:
            shown = character.encode("unicode_escape").decode("ascii")
            raise InvalidAuthor(
                f"{field}不能含「{shown}」——那會破壞變更紀錄的作者欄位，"
                f"使紀錄上的人與實際輸入的人不同。下一步：移除該字元"
            )
    return trimmed


# ── 編輯階段（#33）────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EditingSession:
    """一個編輯階段：識別碼（只有持有的分頁知道）、持有者、開始時間、最近一次續期。"""

    token: str
    holder: Identity
    started_at: datetime
    renewed_at: datetime


class SessionHeld(Exception):
    """已有活躍階段：回覆含持有者姓名、email、開始時間（§7.2.2）。不提供強制接管。"""

    def __init__(self, holder: Identity, started_at: datetime) -> None:
        super().__init__(
            f"目前由 {holder.name}（{holder.email}）自 {started_at.isoformat()} 起編輯中，"
            "介面為唯讀。下一步：等對方離開或逾時釋放後再取得；不提供強制接管"
        )
        self.holder = holder
        self.started_at = started_at


class SessionExpired(Exception):
    """續期的階段已失效（逾時被回收、已釋放、或識別碼不對）：明確失敗，不靜默重新取得。"""


# 續期逾時的預設值：頁面每 30 秒續期一次，瀏覽器會把背景分頁的計時器節流到約每分鐘一次，
# 所以留兩次節流後的心跳再多一點——短到異常中斷後幾分鐘內就釋放，長到背景分頁不被誤收。
DEFAULT_RENEW_TIMEOUT = timedelta(seconds=150)


class SessionLock:
    """單一編輯階段（ADR-00000014）。

    `timeout` 是**續期逾時**：持有者超過這麼久沒續期（分頁異常中斷、沒走到釋放）就回收——
    開發與部署模式**都有**，否則一次異常中斷就讓之後所有人永遠唯讀（§7.2.2）。頁面開著時
    會一直續期，所以「開發模式不因閒置回收」仍成立；部署模式的閒置逾時（使用者沒操作）是
    另一件事，由 #48 在這之上加。

    所有操作先 `sweep(now)`：逾時的階段在任何人碰它之前就被回收，被回收的那份回給呼叫端
    ——API 層據此清草稿並回報「有 N 份草稿被清除」而非靜默丟棄（T13）。
    """

    def __init__(
        self,
        timeout: timedelta = DEFAULT_RENEW_TIMEOUT,
        tokens: Callable[[], str] = secrets.token_urlsafe,
    ) -> None:
        self.timeout = timeout
        self._tokens = tokens
        self.current: EditingSession | None = None

    def acquire(self, holder: Identity, now: datetime) -> EditingSession:
        """無人持有時取得；已被占用丟 `SessionHeld`（含持有者資訊）。"""
        self.sweep(now)
        if self.current is not None:
            raise SessionHeld(self.current.holder, self.current.started_at)
        self.current = EditingSession(self._tokens(), holder, now, now)
        return self.current

    def renew(self, token: str, now: datetime) -> EditingSession:
        """續期。階段已失效（回收／釋放／識別碼不對）→ `SessionExpired`，不會替你重新取得。"""
        self.sweep(now)
        if self.current is None or self.current.token != token:
            raise SessionExpired(
                f"識別碼 {token[:6]}… 的編輯階段已失效（逾時被回收或已釋放），這個分頁現在是唯讀。"
                "下一步：重新整理頁面以重新取得編輯階段"
            )
        self.current = replace(self.current, renewed_at=now)
        return self.current

    def resume(self, token: str, now: datetime) -> EditingSession:
        """重新整理後接續：續期並**換一個新的識別碼**。階段已失效→`SessionExpired`。

        頁面重新整理時，舊頁面卸載會送出「釋放」、新頁面拿存著的識別碼回來接續，兩個請求誰先到
        不一定。接續時把識別碼換掉，晚到的那個釋放帶的是舊識別碼，就什麼都動不了；先到的話
        階段已釋放，這裡丟 `SessionExpired`、由呼叫端重新取得。
        """
        current = self.renew(token, now)
        self.current = replace(current, token=self._tokens())
        return self.current

    def release(self, token: str) -> bool:
        """持有者主動釋放（正常關閉頁面）。識別碼不對就什麼都不做、回 False。"""
        if self.current is None or self.current.token != token:
            return False
        self.current = None
        return True

    def sweep(self, now: datetime) -> list[EditingSession]:
        """回收續期逾時的階段。回被回收的那幾份（最多一份）。"""
        if self.current is None:
            return []
        if now - self.current.renewed_at <= self.timeout:
            return []
        expired = self.current
        self.current = None
        return [expired]

    def holds(self, token: str) -> bool:
        return self.current is not None and self.current.token == token
