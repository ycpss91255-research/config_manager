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


class AllowedRoot(BaseModel):
    """白名單設定檔（§7.9）裡的一個根：一個路徑前綴，加上誰在何時加入的。

    `added_by`／`added_at` 選填：由介面新增與 entrypoint 種子時明寫，但手動最小化
    的種子檔可只列 `prefix`。`prefix` 的字面檢查（絕對、無 `..`）在 allowed_roots
    載入時先行（T23），realpath 正規化留給 I/O 層。
    """

    model_config = ConfigDict(extra="forbid")

    prefix: str
    added_by: str = ""
    added_at: str = ""


class AllowedRoots(BaseModel):
    """持久化、可從介面維護的白名單（§7.9，#202）。

    entrypoint 首次啟動從 `CM_ALLOWED_ROOTS` 種下，之後以檔為準、可增可減。
    約束（版本、prefix 唯一與字面合法）在 T23 載入時被驗證。
    """

    model_config = ConfigDict(extra="forbid")

    roots_version: int
    roots: list[AllowedRoot] = []
