"""api/schema — schema 骨架的端點（#38）。

`POST /api/configs/{uid}/schema`：替一份已納管的 config 產生 schema 骨架。
`POST /api/configs/{uid}/types`：人工指定（或清除）一個參數的型別（#285、ADR-00000021）。
兩者都是本 repo 對設計 §3.5.3 端點表的追加，走「狀態變更用 `POST /api/configs/{uid}/<動作>`
子資源」的既有慣例。

**僅開發者**（ADR-00000020）：骨架一存在，第 2 層驗證就依它硬擋（#39），等於替所有人開啟一道
把關；推斷只看得到當下的值、可能猜錯，所以由開發者看過再開，納管時不自動產生（#38 定案）。

獨立成一個模組的理由同 `api/history`：`routes.py` 已貼近單檔行數上限（C0302）。
"""

from __future__ import annotations

from collections.abc import Mapping
from subprocess import CalledProcessError

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config_manager.api.lock import require_permission
from config_manager.api.session import Identity
from config_manager.core.errors import ManualTypeError, SyntaxParse, TypeNotSpecified
from config_manager.core.manual_types import manual_types
from config_manager.core.models import FileEntry
from config_manager.core.roles import SPECIFY_TYPES
from config_manager.core.schema_check import field_hints
from config_manager.io.errors import (
    ChangeError,
    SchemaExists,
    SchemaLeftBehind,
    SchemaNotFound,
    SchemaUnavailable,
    SchemaUnreadable,
    WriterError,
)
from config_manager.io.manual_type import specify_type
from config_manager.io.schema import draft_skeleton, read_schema


class TypeInput(BaseModel):
    """指定一個參數的型別。`type` 為 null＝清除指定（回到指定之前）。"""

    path: str
    type: str | None = None


def register_schema(app: FastAPI, repo: str, held: dict[str, Identity]) -> None:
    """把 schema 骨架的端點掛上 app。"""

    @app.post("/api/configs/{uid}/schema")
    def draft_config_schema(uid: str) -> dict[str, object]:
        """產生 schema 骨架：存進 `.schemas/`、條目記下路徑、記一筆 meta。回 uid 與 schema 路徑。"""
        return _draft(repo, held, uid)

    @app.post("/api/configs/{uid}/types")
    def specify_parameter_type(uid: str, payload: TypeInput) -> dict[str, object]:
        """人工指定一個參數的型別（#285）；`type` 為 null 是清除那個指定。寫進 schema、
        記一筆 meta。"""
        return _specify(repo, held, uid, payload)


def _draft(repo: str, held: dict[str, Identity], uid: str) -> dict[str, object]:
    """產生骨架的邏輯：開發者門檻（沒身分 409、角色不足 403）之後交給 `io/schema`。

    定位不到 404、已有 schema 409（不覆寫）、沒有結構可推導 422（raw／頂層不是物件）；來源
    複本讀不到或解析不了、寫入／commit／回滾失敗是伺服器側的錯，帶訊息的 500。
    """
    identity = require_permission(held, SPECIFY_TYPES)
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


def schema_view(repo: str, entry: FileEntry) -> dict[str, object]:
    """單筆內容裡與 schema 有關的三個欄位：`constraints`、`manual_types`、`schema_error`。

    `constraints` 是欄位路徑 → 範圍／步進／列舉選項／說明，欄位表據此設輸入框（#40）；
    `manual_types` 是欄位路徑 → 人工指定的型別，欄位表據此標示「已指定」、單筆內容據此把推斷的
    型別蓋成指定的（#285）。schema 讀不出來時**不讓整份內容跟著讀不到**——值還是要看得到（才能
    退版、解除納管），所以兩者回空、錯誤以文字帶回，介面明說這份在 schema 修好前存不了（存的
    時候後端照樣擋，#39）。
    """
    try:
        schema = read_schema(repo, entry)
    except SchemaUnreadable as error:
        return {"constraints": {}, "manual_types": {}, "schema_error": str(error)}
    return {
        "constraints": field_hints(schema) if schema else {},
        "manual_types": manual_types(schema),
        "schema_error": None,
    }


def as_specified(
    schema: Mapping[str, object] | None, edits: Mapping[str, object]
) -> dict[str, object]:
    """把介面送來的改動依人工指定的型別整理：指定成 double 的欄位，整數值轉成浮點。

    JSON 分不出 `6` 與 `6.0`。來源值本來就是浮點時 `core/parse` 會保住小數點；但推斷成整數、
    人工指定成 double 的欄位（`timeout: 5`），來源值是整數，不轉的話寫出去還是 `6`——那正是
    指定 double 要避免的事（ADR-00000021）。bool 是 int 的子類別，明確排除。
    """
    doubles = {path for path, name in manual_types(schema).items() if name == "float"}
    return {
        path: float(value)
        if path in doubles and isinstance(value, int) and not isinstance(value, bool)
        else value
        for path, value in edits.items()
    }


def _specify(
    repo: str, held: dict[str, Identity], uid: str, payload: TypeInput
) -> dict[str, object]:
    """指定／清除型別的邏輯：開發者門檻之後交給 `io/manual_type`。

    送錯的請求 422（不認得的型別、不能指定的路徑、與現值不相容、raw）；要清除的欄位沒有指定
    409；定位不到 404；寫入／commit／回滾失敗是伺服器側的錯，帶訊息的 500。
    """
    identity = require_permission(held, SPECIFY_TYPES)
    try:
        entry = specify_type(repo, uid, payload.path, payload.type, identity.git_author)
    except SchemaNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except TypeNotSpecified as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (ManualTypeError, SchemaUnavailable) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (UnicodeDecodeError, SyntaxParse, RecursionError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"uid「{uid}」的來源複本讀不到或解析不了：{error}。"
            "下一步：檢查 config-repo 裡該來源複本的內容與編碼",
        ) from error
    except (SchemaLeftBehind, WriterError, ChangeError, CalledProcessError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"uid": uid, "path": payload.path, "type": payload.type, "schema": entry.schema_path}
