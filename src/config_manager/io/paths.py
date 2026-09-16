"""io/paths — 路徑可達性的共用小工具。

`source`（匯入讀取）與 `digest`（偏離偵測）都要在 `open` 失敗（EACCES）時分辨「不存在」
與「上層目錄擋住去路」，兩者都靠同一組「逐層 stat 找出擋路的那一層祖先」。抽在這裡共用，
避免兩份實作日後分歧（也讓 pylint 的 R0801 不再抓到重複）。
"""

import os


def ancestors(path: str) -> list[str]:
    """path 從根到它自己的每一層，根在最前面。"""
    chain = []
    current = os.path.abspath(path)
    while True:
        chain.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return list(reversed(chain))


def blocking_parent(path: str) -> str | None:
    """去不到 `path` 時，是哪一層目錄擋住的；若目標本身可 `stat` 則回 None。

    從根往下逐層 `stat`：第一個 `stat` 不了的祖先，它的上一層就是缺 `+x` 的那個目錄。
    目標本身 `stat` 得到（EACCES 來自檔案自己的讀取權限、不是 traverse）時回 None，交給
    呼叫端判為「內容讀不出來」。
    """
    reachable = "/"
    for ancestor in ancestors(path)[1:]:
        try:
            os.stat(ancestor)
        except OSError:
            return reachable
        reachable = ancestor
    return None
