"""core/config_list — 清單檔載入 / 完整性檢查 / 寫回（PDF §3.3, §4.3）。

純邏輯，不做 I/O（ADR-00000011）：load 收字串、不讀磁碟。
"""

from pathlib import PurePosixPath

import tomlkit

from core.errors import (
    DuplicateTarget,
    DuplicateUid,
    InvalidFormat,
    TargetEscape,
)
from core.models import ConfigList, FileEntry

# 被管理 config 的允許格式（T6 亦處理這些）。raw = 不解析、只做版控。
ALLOWED_FORMATS = ("yaml", "json", "toml", "ini", "raw")


def load(text: str) -> ConfigList:
    """把 config 清單檔的原始文字解析為已驗證的資料模型。"""
    data = tomlkit.parse(text).unwrap()
    config_list = ConfigList.model_validate(data)
    _check_integrity(config_list)
    return config_list


def _check_integrity(config_list: ConfigList) -> None:
    """跨條目的完整性檢查。硬錯誤丟具名例外。"""
    seen_uid: dict[str, FileEntry] = {}
    seen_target: dict[str, FileEntry] = {}
    for entry in config_list.files:
        if ".." in PurePosixPath(entry.target).parts:
            raise TargetEscape(
                f"目標路徑含 ..（逃逸風險）：{entry.ref} 的目標「{entry.target}」"
            )

        if entry.format not in ALLOWED_FORMATS:
            allowed = "／".join(ALLOWED_FORMATS)
            raise InvalidFormat(
                f"format 非允許值：{entry.ref} 的 format「{entry.format}」"
                f"，允許值為 {allowed}"
            )

        first_uid = seen_uid.get(entry.uid)
        if first_uid is not None:
            raise DuplicateUid(
                f"uid 重複：{first_uid.ref} 與 {entry.ref} 共用 uid「{entry.uid}」"
            )
        seen_uid[entry.uid] = entry

        first_target = seen_target.get(entry.target)
        if first_target is not None:
            raise DuplicateTarget(
                f"目標位置重複：{first_target.ref} 與 {entry.ref} "
                f"共用目標「{entry.target}」"
            )
        seen_target[entry.target] = entry
