"""core/models — config 清單檔的 pydantic 資料模型（PDF §4.3）。

資料模型本身無行為（無獨立測試介面），其約束在 T1 載入時被驗證。
"""

from pydantic import BaseModel, ConfigDict, Field


class Permissions(BaseModel):
    owner: str
    group: str
    mode: str  # 字串，避免 0644 被解析為整數


class Defaults(BaseModel):
    permissions: Permissions


class FileEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    uid: str
    name: str
    hostname: str
    source: str
    target: str
    format: str
    groups: list[str] = []
    description: str | None = None
    # schema 是清單檔的鍵名；以別名避免與 pydantic 的保留名衝突。
    schema_path: str | None = Field(default=None, alias="schema")
    requires_privilege: bool = False
    permissions: Permissions | None = None  # 未指定時套用 defaults.permissions

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
