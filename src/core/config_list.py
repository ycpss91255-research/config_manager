"""core/config_list — 清單檔載入 / 完整性檢查 / 寫回（PDF §3.3, §4.3）。

純邏輯，不做 I/O（ADR-00000011）：load 收字串、不讀磁碟。
"""

import tomlkit

from core.errors import DuplicateUid
from core.models import ConfigList, FileEntry


def load(text: str) -> ConfigList:
    """把 config 清單檔的原始文字解析為已驗證的資料模型。"""
    data = tomlkit.parse(text).unwrap()
    config_list = ConfigList.model_validate(data)
    _check_integrity(config_list)
    return config_list


def _check_integrity(config_list: ConfigList) -> None:
    """跨條目的完整性檢查。硬錯誤丟具名例外。"""
    seen_uid: dict[str, FileEntry] = {}
    for entry in config_list.files:
        first = seen_uid.get(entry.uid)
        if first is not None:
            raise DuplicateUid(
                f"uid 重複：{first.ref} 與 {entry.ref} 共用 uid「{entry.uid}」"
            )
        seen_uid[entry.uid] = entry
