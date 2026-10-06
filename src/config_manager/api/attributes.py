"""api/attributes — 修改 config 屬性的端點（#286，設計 §7.4.4）。

`POST /api/configs/{uid}/attributes`，body `{name, hostname, groups, description}`（完整的四項）。
是本 repo 對設計 §3.5.3 端點表的追加，走「狀態變更用 `POST /api/configs/{uid}/<動作>` 子資源」的
既有慣例。

**僅開發者**（ADR-00000020）：群組決定整個面板的組織方式，一般使用者誤改會讓其他人下次開啟時
找不到熟悉的位置——不是資料損毀，但會造成混淆而且難以察覺（§7.4.4）。

獨立成一個模組的理由同 `api/history`：`routes.py` 已貼近單檔行數上限（C0302）。
"""

from __future__ import annotations

from subprocess import CalledProcessError

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config_manager.api.lock import require_permission
from config_manager.api.session import Identity
from config_manager.core.attributes import Attributes
from config_manager.core.errors import (
    AttributeInvalid,
    AttributesUnchanged,
    ConfigListError,
    EntryNotFound,
)
from config_manager.core.roles import EDIT_ATTRIBUTES
from config_manager.io.attributes import update
from config_manager.io.errors import AttributesLeftBehind, ChangeError, WriterError


# 回給介面的欄位：身分（uid、參照形式）加上改得了的四項。
_RETURNED = ("uid", "name", "hostname", "ref", "groups", "description")


class AttributesInput(BaseModel):
    """一份 config 的四項屬性。每次送完整的四項；`description` 空字串或 null＝沒有說明。"""

    name: str
    hostname: str
    groups: list[str] = []
    description: str | None = None


def register_attributes(app: FastAPI, repo: str, held: dict[str, Identity]) -> None:
    """把修改屬性的端點掛上 app。"""

    @app.post("/api/configs/{uid}/attributes")
    def update_config_attributes(uid: str, payload: AttributesInput) -> dict[str, object]:
        """修改名稱、群組、主機、說明：寫回清單檔、記一筆 meta。回更新後的那幾項。"""
        return _update(repo, held, uid, payload)


def _update(
    repo: str, held: dict[str, Identity], uid: str, payload: AttributesInput
) -> dict[str, object]:
    """修改屬性的邏輯：開發者門檻（沒身分 409、角色不足 403）之後交給 `io/attributes`。

    值不合法 422（detail 帶 `field`，介面據此標在那個輸入框）；什麼都沒改 409；定位不到 404；
    改完會讓清單檔不合規（與既有條目衝突）409；寫入／commit／回滾失敗是伺服器側的錯，500。
    """
    identity = require_permission(held, EDIT_ATTRIBUTES)
    wanted = Attributes(
        name=payload.name,
        hostname=payload.hostname,
        groups=tuple(payload.groups),
        description=payload.description,
    )
    try:
        entry = update(repo, uid, wanted, identity.git_author)
    except EntryNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except AttributeInvalid as error:
        raise HTTPException(
            status_code=422, detail={"message": str(error), "field": error.field}
        ) from error
    except (AttributesUnchanged, ConfigListError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (AttributesLeftBehind, WriterError, ChangeError, CalledProcessError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {key: getattr(entry, key) for key in _RETURNED}
