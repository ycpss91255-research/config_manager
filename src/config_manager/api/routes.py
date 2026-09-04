"""api/routes — HTTP 端點（設計文件 §3.5.3；測試介面 T9）。

端點集合由設計文件 §3.5.3 的表決定，不在這裡發明。目前只實作
`GET /api/configs` 的最小形式，其餘隨各自的 issue 逐條加入。

app 由 create_app(repo) 產生而非模組層的全域物件：config-repo 的位置是啟動
參數，做成全域會讓它變成 import 的副作用，測試也就沒辦法在同一個行程裡指向
不同的 repo（ADR-00000011 的同一個理由：輸入從參數進來）。
"""

from fastapi import FastAPI

from config_manager.core.models import FileEntry
from config_manager.io.preflight import read_config_list


def create_app(repo: str) -> FastAPI:
    """建立服務於 repo 這份 config-repo 的 app。"""
    app = FastAPI(title="config_manager", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/api/configs")
    def list_configs() -> list[dict[str, object]]:
        """列出所有納管項目（設計文件 §3.5.3）。

        清單檔每次請求重讀：它是唯一真實來源（ADR-00000002），而快取一份會讓
        「畫面上的清單」與「磁碟上的清單」有機會不一致——那正是這個系統存在的
        理由所要避免的東西。
        """
        return [_as_row(entry) for entry in read_config_list(repo).files]

    return app


def _as_row(entry: FileEntry) -> dict[str, object]:
    """條目在清單畫面上需要的欄位。狀態尚未接上，隨 #7 加入。"""
    return {
        "uid": entry.uid,
        "name": entry.name,
        "hostname": entry.hostname,
        "ref": entry.ref,
        "target": entry.target,
        "format": entry.format,
        "groups": entry.groups,
    }
