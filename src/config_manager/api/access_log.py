"""可選的 access log（F8 決定 C）：每個請求在 stderr 記一行、就六欄。

`access <UTC 時刻（毫秒）> <方法> <路徑> <狀態碼> <毫秒數>`。平常關著（`CM_ACCESS_LOG` 未設或 0），
要查「請求到底有沒有到後端」時打開。紀錄裡**不得有敏感值**：不記 query string（URL 參數可能夾帶
秘密）、cookie、`X-CM-Editing-Token`、任何 header、request／response body、client 位址。路徑裡的
控制字元與反斜線跳脫成 `\\xNN`，一個請求永遠只有一行——不能用路徑偽造第二行紀錄。耗時用單調
時鐘算，UTC 只作時間標記。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_ESCAPED = {chr(code) for code in range(0x20)} | {"\x7f", "\\"}


def escape_path(path: str) -> str:
    """控制字元（U+0000–U+001F、U+007F）與反斜線寫成 `\\xNN`，其餘原樣。"""
    return "".join(f"\\x{ord(char):02x}" if char in _ESCAPED else char for char in path)


def access_line(when: datetime, method: str, path: str, status: int, elapsed_ms: int) -> str:
    """一行六欄；`when` 要是 UTC。"""
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond // 1000:03d}Z"
    return f"access {stamp} {method} {escape_path(path)} {status} {elapsed_ms}ms"


def _to_stderr(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def access_logged(app: ASGIApp, sink: Callable[[str], None] = _to_stderr) -> ASGIApp:
    """把 `app` 包成會記 access log 的 ASGI app（給 `FastAPI.add_middleware`）。

    只讀 scope 的 method 與 path、回應的 status；不碰 query string、headers、body。回應開始前就
    炸掉（沒有 `http.response.start`）記 500——外層的 ServerErrorMiddleware 會回 500，這裡不重複記。
    `sink` 收每一行：預設寫 stderr；測試給一個 list 的 append，不必攔 fd（回應先到、紀錄後寫，
    攔 fd 會偶爾讀到空）。
    """

    async def logged(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        started = time.monotonic()
        when = datetime.now(UTC)
        status = 500

        async def send_and_note(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await app(scope, receive, send_and_note)
        finally:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            sink(access_line(when, str(scope["method"]), str(scope["path"]), status, elapsed_ms))

    return logged
