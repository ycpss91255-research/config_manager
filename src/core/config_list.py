"""core/config_list — 清單檔載入 / 完整性檢查 / 寫回（PDF §3.3, §4.3）。

純邏輯，不做 I/O（ADR-00000011）：load 收字串、不讀磁碟。
"""

import tomlkit

from core.models import ConfigList


def load(text: str) -> ConfigList:
    """把 config 清單檔的原始文字解析為已驗證的資料模型。"""
    data = tomlkit.parse(text).unwrap()
    return ConfigList.model_validate(data)
