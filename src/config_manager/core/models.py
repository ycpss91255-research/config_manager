"""core/models — config 清單檔的 pydantic 資料模型（PDF §4.3）。

資料模型本身無行為（無獨立測試介面），其約束在 T1 載入時被驗證。
extra="forbid"：未知欄位大聲失敗（不變式 2）；行號友善的檢查在 config_list 先行。
"""

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class Permissions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: str
    group: str
    mode: str  # 字串，避免 0644 被解析為整數


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permissions: Permissions


class FileEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

    list_version: int
    defaults: Defaults
    files: list[FileEntry] = []
    # 載入時收集的警示。非清單檔資料、不可由檔案設定，故為私有屬性（防注入）。
    _warnings: list[str] = PrivateAttr(default_factory=list)

    @property
    def warnings(self) -> list[str]:
        """載入期收集的警示（警示與錯誤的分界見 CONTEXT）。不參與寫回。"""
        return self._warnings
