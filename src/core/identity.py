"""core/identity — 名稱推導與 uid 產生（CONTEXT 身分欄位；PDF §4.3）。

純邏輯，不做 I/O（ADR-00000011）：derive_name 收路徑字串，new_uid 收注入的時鐘。
"""

from pathlib import PurePosixPath


def derive_name(target_path: str) -> str:
    """由目標路徑推導人可讀名稱：取最後二層、去副檔名、底線轉連字號、去重疊層級。"""
    parts = target_path.split("/")
    parent = parts[-2].replace("_", "-")
    filename = PurePosixPath(parts[-1]).stem.replace("_", "-")
    if parent == filename:
        return filename
    return f"{parent}-{filename}"
