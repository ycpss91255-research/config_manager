"""api/routes — HTTP 端點（設計文件 §3.5.3；測試介面 T9）。

端點集合由設計文件 §3.5.3 的表決定，不在這裡發明。目前只實作
`GET /api/configs` 的最小形式，其餘隨各自的 issue 逐條加入。

app 由 create_app(repo) 產生而非模組層的全域物件：config-repo 的位置是啟動
參數，做成全域會讓它變成 import 的副作用，測試也就沒辦法在同一個行程裡指向
不同的 repo（ADR-00000011 的同一個理由：輸入從參數進來）。
"""

from collections.abc import Iterable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config_manager.core.models import FileEntry
from config_manager.core.state import State
from config_manager.io.scan import scan

# 前端是另一個容器、另一個 port（設計文件 §3.1：瀏覽器分別連 frontend 與
# backend），所以頁面對 API 的請求是跨來源的。
#
# 預設值不是 "*"。這個服務改得動機器上的 config，允許任意來源等於讓任何一個
# 被瀏覽的網頁都能對它下指令。預設只放行 compose 起的那個前端；別的部署形態
# 自己用 CM_ALLOWED_ORIGINS 指定（不變式 4：預設值落向安全）。
_DEFAULT_ORIGINS = ("http://127.0.0.1:8081", "http://localhost:8081")


def create_app(repo: str, allowed_origins: Iterable[str] = _DEFAULT_ORIGINS) -> FastAPI:
    """建立服務於 repo 這份 config-repo 的 app。"""
    app = FastAPI(title="config_manager", docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["content-type"],
    )

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
