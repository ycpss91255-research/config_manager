"""api/schema — schema 骨架的端點（#38）。

`POST /api/configs/{uid}/schema`：替一份已納管的 config 產生 schema 骨架。是本 repo 對設計
§3.5.3 端點表的追加，走「狀態變更用 `POST /api/configs/{uid}/<動作>` 子資源」的既有慣例。

**僅開發者**（ADR-00000020）：骨架一存在，第 2 層驗證就依它硬擋（#39），等於替所有人開啟一道
把關；推斷只看得到當下的值、可能猜錯，所以由開發者看過再開，納管時不自動產生（#38 定案）。

獨立成一個模組的理由同 `api/history`：`routes.py` 已貼近單檔行數上限（C0302）。
"""

from __future__ import annotations

from subprocess import CalledProcessError

from fastapi import FastAPI, HTTPException

from config_manager.api.lock import require_developer
from config_manager.api.session import Identity
from config_manager.core.errors import SyntaxParse
from config_manager.io.errors import (
    ChangeError,
    SchemaExists,
    SchemaLeftBehind,
    SchemaNotFound,
    SchemaUnavailable,
    WriterError,
)
from config_manager.io.schema import draft_skeleton


def register_schema(app: FastAPI, repo: str, held: dict[str, Identity]) -> None:
    """把 schema 骨架的端點掛上 app。"""

    @app.post("/api/configs/{uid}/schema")
    def draft_config_schema(uid: str) -> dict[str, object]:
        """產生 schema 骨架：存進 `.schemas/`、條目記下路徑、記一筆 meta。回 uid 與 schema 路徑。"""
        return _draft(repo, held, uid)


def _draft(repo: str, held: dict[str, Identity], uid: str) -> dict[str, object]:
    """產生骨架的邏輯：開發者門檻（沒身分 409、角色不足 403）之後交給 `io/schema`。

    定位不到 404、已有 schema 409（不覆寫）、沒有結構可推導 422（raw／頂層不是物件）；來源
    複本讀不到或解析不了、寫入／commit／回滾失敗是伺服器側的錯，帶訊息的 500。
    """
    identity = require_developer(held, "產生 schema")
    try:
        entry = draft_skeleton(repo, uid, identity.git_author)
    except SchemaNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SchemaExists as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SchemaUnavailable as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (UnicodeDecodeError, SyntaxParse, RecursionError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"uid「{uid}」的來源複本讀不到或解析不了：{error}。"
            "下一步：檢查 config-repo 裡該來源複本的內容與編碼",
        ) from error
    except (SchemaLeftBehind, WriterError, ChangeError, CalledProcessError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"uid": entry.uid, "schema": entry.schema_path}
