"""io/digest — 檔案內容雜湊（測試介面 T20；設計文件 §5.4）。

偏離偵測的另一半。core/state 的 decide 刻意不讀檔，雜湊由呼叫者算好傳入
（ADR-00000011），所以算雜湊的地方在 io 層。呼叫端把兩者接起來：

    target_hash = digest(target)
    decide(target_hash is not None, target_hash, digest(source))

雜湊的是原始位元組，不是解析後的資料。偵測的目標是「有人繞過介面直接修改了
目標」——比對解析後的資料會讓「語意相同、格式不同」看起來一致，而那正是有人
手改過的痕跡。所以連空白與換行的差異都算偏離。
"""

import hashlib
import os

from config_manager.io.errors import ContentUnreadable

# 一次讀進記憶體的分塊大小。config 檔不大，但沒有理由假設它一定不大。
_CHUNK = 65536


def digest(path: str) -> str | None:
    """回傳 path 的內容 sha256，檔案不存在則回 None。

    「不存在」回 None、「存在但讀不出來」丟例外——兩者不可混為一談：都回 None
    的話，一個權限壞掉的目標會被判成「未部署」，UI 於是提供一鍵寫出，而那個
    動作修不好真正的問題，操作者也不會知道為什麼（不變式 2）。
    """
    if not os.path.lexists(path):
        return None

    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(_CHUNK), b""):
                hasher.update(block)
    except OSError as error:
        raise ContentUnreadable(
            f"內容讀不出來：{path}（{error.strerror}）。"
            f"下一步：確認它是一般檔案、且執行身分有讀取權限"
        ) from error

    return hasher.hexdigest()
