"""core/identity — 名稱推導與 uid 產生（CONTEXT 身分欄位；PDF §4.3）。

純邏輯，不做 I/O（ADR-00000011）：derive_name 收路徑字串，new_uid 收注入的時鐘。
"""

import datetime
import string
from pathlib import PurePosixPath

_BASE36 = string.digits + string.ascii_lowercase


def derive_name(target_path: str) -> str:
    """由目標路徑推導人可讀名稱：取最後二層、去副檔名、底線轉連字號、去重疊層級。"""
    parts = target_path.split("/")
    parent = parts[-2].replace("_", "-")
    filename = PurePosixPath(parts[-1]).stem.replace("_", "-")
    if parent == filename:
        return filename
    return f"{parent}-{filename}"


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    out: list[str] = []
    while n:
        n, r = divmod(n, 36)
        out.append(_BASE36[r])
    return "".join(reversed(out))


def new_uid(now: datetime.datetime) -> str:
    """由匯入時刻的毫秒時間戳產生 8 碼 base36 uid。"""
    ms = int(now.timestamp() * 1000)
    return _to_base36(ms).rjust(8, "0")
