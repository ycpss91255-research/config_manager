"""core/identity — 名稱推導與 uid 產生（CONTEXT 身分欄位；PDF §4.3）。

純邏輯，不做 I/O（ADR-00000011）：derive_name 收路徑字串，new_uid 收注入的時鐘。
"""


def derive_name(target_path: str) -> str:
    """由目標路徑推導人可讀名稱。"""
    parts = target_path.split("/")
    filename = parts[-1].replace(".yaml", "")
    return f"{parts[-2]}-{filename}"
