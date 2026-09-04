"""core/models — config 清單檔的 pydantic 資料模型（PDF §4.3）。

資料模型本身無行為（無獨立測試介面），其約束在 T1 載入時被驗證。
只宣告目前切片需要的欄位；schema / 權限覆蓋 / requires_privilege 等
選填欄位待後續切片加入。
"""

from pydantic import BaseModel


class Permissions(BaseModel):
    owner: str
    group: str
    mode: str  # 字串，避免 0644 被解析為整數


class Defaults(BaseModel):
    permissions: Permissions


class FileEntry(BaseModel):
    uid: str
    name: str
    hostname: str
    source: str
    target: str
    format: str
    groups: list[str] = []
    description: str | None = None


class ConfigList(BaseModel):
    list_version: int
    defaults: Defaults
    files: list[FileEntry] = []
