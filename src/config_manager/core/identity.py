"""core/identity — 名稱推導與 uid 產生（CONTEXT 身分欄位；PDF §4.3）。

純邏輯，不做 I/O（ADR-00000011）：derive_name 收路徑字串，new_uid 收注入的時鐘。
"""

import datetime
import string
from pathlib import PurePosixPath

from config_manager.core.errors import NameUnderivable, UidHorizonReached

_BASE36 = string.digits + string.ascii_lowercase

# uid 固定寬度是 ref 格式契約（ADR-00000012）。8 碼 base36 能表示 [0, 36^8) 的整數；
# 到達 36^8 就需要第 9 碼、破壞「固定寬度可排序」不變式（#231）。值必須 < 此上限。
_UID_WIDTH = 8
_UID_CEILING = 36**_UID_WIDTH

# 訊息裡給的那個合格例子。與 T5 第一列的推導範例同一條路徑，讀者對得起來。
_EXAMPLE_TARGET = "/opt/robot/navigation/params.yaml"


def derive_name(target_path: str) -> str:
    """由目標路徑推導人可讀名稱：取最後二層、去副檔名、底線轉連字號、去重疊層級。

    推不出名稱時丟 `NameUnderivable`，不回一個湊合的名字（不變式 2、T5）。
    """
    parts = target_path.split("/")
    if not target_path.startswith("/"):
        raise NameUnderivable(
            f"目標路徑不是絕對路徑：derive_name 收到「{target_path}」。"
            f"目標位置是 config 實際被程式讀取的路徑，相對路徑指到哪裡"
            f"取決於當下的工作目錄，推不出一個穩定的名稱。"
            f"下一步：改傳絕對路徑，例如 {_EXAMPLE_TARGET}"
        )
    if not parts[-1] or not parts[-2]:
        raise NameUnderivable(
            f"目標路徑推不出兩層：derive_name 收到「{target_path}」，"
            f"名稱取自最後二層（上層目錄與檔名），這裡有一層是空的。"
            f"下一步：改傳兩層都在的絕對路徑，例如 {_EXAMPLE_TARGET}"
        )
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


def new_uid(now: datetime.datetime, previous: str | None = None) -> str:
    """由匯入時刻的毫秒時間戳產生 8 碼 base36 uid。

    唯一性來自時間單調遞增（backend 是唯一寫入者，無競爭）。傳入前一個 uid 時，
    若時間值未超過它（例如同一毫秒內批次納管），則以前值加一，保證嚴格遞增。

    到達 8 碼寬度容納不下的時間 horizon（`36^8` ms，2059-05-25T17:38:27Z 起）時丟
    `UidHorizonReached`：`rjust` 只補不截，放行 9 碼會靜默破壞固定寬度可排序不變式
    （#231）。守衛加在 prev+1 補償**之後**的最終值上，兩條產生路徑都蓋到。
    """
    value = int(now.timestamp() * 1000)
    if previous is not None:
        prev_value = int(previous, 36)
        if value <= prev_value:
            value = prev_value + 1
    if value >= _UID_CEILING:
        raise UidHorizonReached(
            f"uid 由毫秒時間戳轉 base36、固定 {_UID_WIDTH} 碼；時間值 {value} 已達 "
            f"{_UID_WIDTH} 碼容納上限 {_UID_CEILING}（36^{_UID_WIDTH}），轉出來會是 9 碼、"
            f"破壞固定寬度可排序不變式。horizon 是 2059-05-25T17:38:27Z。"
            f"下一步：uid 寬度是參照格式契約（ADR-00000012，uid 永不變），加寬需先修契約——見 #231"
        )
    return _to_base36(value).rjust(_UID_WIDTH, "0")
