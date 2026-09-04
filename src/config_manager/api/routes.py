"""api/routes — HTTP 端點（設計文件 §3.5.3；測試介面 T9）。

端點集合由設計文件 §3.5.3 的表決定，不在這裡發明。目前只實作
`GET /api/configs` 的最小形式，其餘隨各自的 issue 逐條加入。

app 由 create_app(repo) 產生而非模組層的全域物件：config-repo 的位置是啟動
參數，做成全域會讓它變成 import 的副作用，測試也就沒辦法在同一個行程裡指向
不同的 repo（ADR-00000011 的同一個理由：輸入從參數進來）。
"""

from collections.abc import Iterable

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config_manager.api.errors import InvalidAuthor
from config_manager.api.session import USER, Identity, author
from config_manager.core.models import FileEntry
from config_manager.core.state import State
from config_manager.io.scan import scan


class SessionInput(BaseModel):
    """身分輸入的請求主體。角色預設為一般使用者（預設值落向安全，不變式 4）。"""

    name: str
    email: str
    role: str = USER

# 前端是另一個容器、另一個 port（設計文件 §3.1：瀏覽器分別連 frontend 與
# backend），所以頁面對 API 的請求是跨來源的。
#
# 預設值不是 "*"。這個服務改得動機器上的 config，允許任意來源等於讓任何一個
# 被瀏覽的網頁都能對它下指令。預設只放行 compose 起的那個前端；別的部署形態
# 自己用 CM_ALLOWED_ORIGINS 指定（不變式 4：預設值落向安全）。
# api/cli 的 serve_plan 也要它：「沒指定就用預設」這個決定要在計畫上看得見，
# 不能藏在一個只有 create_app 讀得到的私有預設參數裡（#97）。
DEFAULT_ORIGINS = ("http://127.0.0.1:8081", "http://localhost:8081")


def create_app(repo: str, allowed_origins: Iterable[str] = DEFAULT_ORIGINS) -> FastAPI:
    """建立服務於 repo 這份 config-repo 的 app。"""
    app = FastAPI(title="config_manager", docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["content-type"],
    )

    # 目前的身分。一次只有一個編輯階段（ADR-00000014），所以放在 app 上而不是
    # 一個模組層的全域——後者會讓同一個行程裡起兩個 app 互相看見對方的身分。
    held: dict[str, Identity] = {}

    @app.post("/api/session")
    def set_session(payload: SessionInput) -> dict[str, str]:
        """設定使用者身分（設計文件 §3.5.3）。

        **這不是登入。** 沒有密碼、不驗證、角色是自我宣告（ADR-00000020）。
        """
        try:
            identity = author(payload.name, payload.email, payload.role)
        except InvalidAuthor as error:
            # 422 而非 400：輸入的形狀對，值不合法。訊息原樣傳給使用者，因為它
            # 已經寫成可行動的樣子（欄位＋原因＋下一步）。
            raise HTTPException(status_code=422, detail=str(error)) from error

        held["identity"] = identity
        return _as_session(identity)

    @app.get("/api/session")
    def get_session() -> dict[str, str] | None:
        """目前的身分，尚未輸入則回 null。"""
        identity = held.get("identity")
        return _as_session(identity) if identity else None

    @app.get("/api/configs")
    def list_configs() -> list[dict[str, object]]:
        """列出所有納管項目，含狀態（設計文件 §3.5.3）。

        每次請求重新掃描：清單檔與目標檔案是唯一真實來源（ADR-00000002），
        快取一份會讓「畫面上的狀態」與「磁碟上的狀態」有機會不一致——而這個
        系統存在的理由就是要讓那兩者對得上。設計文件 §5.4 也是這樣定的：
        開啟主畫面時掃一次，按下「檢查差異」時再掃一次。
        """
        return [_as_row(entry, state) for entry, state in scan(repo)]

    return app


def _as_session(identity: Identity) -> dict[str, str]:
    """身分在畫面上需要的欄位。git_author 一併回傳，讓「紀錄上會是誰」看得見。"""
    return {
        "name": identity.name,
        "email": identity.email,
        "role": identity.role,
        "git_author": identity.git_author,
    }


def _as_row(entry: FileEntry, state: State) -> dict[str, object]:
    """條目在清單畫面上需要的欄位。"""
    return {
        "uid": entry.uid,
        "name": entry.name,
        "hostname": entry.hostname,
        "ref": entry.ref,
        "target": entry.target,
        "format": entry.format,
        "groups": entry.groups,
        "state": state.value,
    }
