"""core/models — config 清單檔的 pydantic 資料模型（PDF §4.3）。

資料模型本身無行為（無獨立測試介面），其約束在 T1 載入時被驗證。
只宣告目前切片需要的欄位；schema / 權限覆蓋 / requires_privilege 等
選填欄位待後續切片加入。
"""

from pydantic import BaseModel, Field


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

    @property
    def ref(self) -> str:
        """完整參照形式 <name>@<hostname>-<uid>（CONTEXT）。用於指名條目。"""
        return f"{self.name}@{self.hostname}-{self.uid}"


class ConfigList(BaseModel):
    list_version: int
    defaults: Defaults
    files: list[FileEntry] = []
    # 載入時收集的警示（非清單檔資料，不參與寫回）。警示與錯誤的分界見 CONTEXT。
    warnings: list[str] = Field(default_factory=list, exclude=True)
