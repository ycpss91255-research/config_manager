"""api/routes — HTTP 端點（設計文件 §3.5.3；測試介面 T9）。

端點集合由設計文件 §3.5.3 的表決定，不在這裡發明。目前只實作
`GET /api/configs` 的最小形式，其餘隨各自的 issue 逐條加入。

app 由 create_app(repo) 產生而非模組層的全域物件：config-repo 的位置是啟動
參數，做成全域會讓它變成 import 的副作用，測試也就沒辦法在同一個行程裡指向
不同的 repo（ADR-00000011 的同一個理由：輸入從參數進來）。
"""

from fastapi import FastAPI

from config_manager.core.models import FileEntry
from config_manager.core.state import State
from config_manager.io.scan import scan


def create_app(repo: str) -> FastAPI:
    """建立服務於 repo 這份 config-repo 的 app。"""
    app = FastAPI(title="config_manager", docs_url="/api/docs", openapi_url="/api/openapi.json")

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
