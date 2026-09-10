"""io/browse — 檔案系統瀏覽，受白名單限制（設計 §3.5.3；供納管使用）。

外部互動層：這裡真的碰檔案系統。核心層不碰（ADR-00000011）。

**判定不在這裡重寫**（同 `io/source`）：「這條路徑落在白名單內嗎」由 `core/whitelist.decide`
回答——純字面比對，已在 #11 落地並驗過。這一層只做核心做不到的那半：`realpath` 解析（I/O）
與列目錄。少了這樣的分工就會有兩份白名單邏輯，而兩份邏輯遲早會給出兩個答案。

瀏覽是納管的前一步：使用者在白名單內的目錄樹裡挑一份要納管的檔案（§5.1）。列出來的是
名字與種類（目錄可再進去、檔案可挑）；進到子目錄或挑檔案時，各自的白名單檢查會再跑一次
（browse 進子目錄、`io/source.read_source` 挑檔案），所以列舉本身只給名字、不洩漏內容。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

from config_manager.core.whitelist import decide
from config_manager.io.errors import (
    BrowseNotADirectory,
    BrowseOutsideRoots,
    BrowseUnreadable,
)


@dataclass(frozen=True)
class Entry:
    """目錄裡的一項。`kind` 是 `dir`（可再進去）或 `file`（可挑來納管）。"""

    name: str
    kind: str
    path: str


@dataclass(frozen=True)
class Listing:
    """一次瀏覽的結果：解析後的目錄，與它底下的項目（依名字排序）。"""

    path: str
    entries: tuple[Entry, ...]


def browse(path: str, allowed_roots: Iterable[str]) -> Listing:
    """列出 `path` 這個目錄的內容，限制在白名單 `allowed_roots` 之內。

    失敗一律是具名例外：

    - `BrowseOutsideRoots` —— 解析後落在白名單之外，一項都不列
    - `BrowseNotADirectory` —— 路徑在白名單內、但不是目錄
    - `BrowseUnreadable` —— 是白名單內的目錄，但列不出來（權限）
    """
    roots = tuple(allowed_roots)
    resolved = os.path.realpath(path)

    # 白名單的根自己也要解析（同 io/source）：同一個問題的兩道檢查不該給出不同答案。
    if not decide(tuple(os.path.realpath(root) for root in roots), resolved).allowed:
        covered = "、".join(roots) if roots else "（目前是空的）"
        raise BrowseOutsideRoots(
            f"瀏覽路徑落在白名單之外：{path} → {resolved}；目前涵蓋：{covered}。"
            f"下一步：確認這條路徑或其中一段不是指向白名單外的符號連結，"
            f"或把該位置納入白名單"
        )

    if not os.path.isdir(resolved):
        raise BrowseNotADirectory(
            f"瀏覽的對象不是目錄：{path} → {resolved}。"
            f"下一步：browse 列的是目錄內容——指向一個目錄，或直接把這個檔案拿去納管"
        )

    try:
        with os.scandir(resolved) as scan:
            items = [(item.name, item.is_dir(follow_symlinks=True)) for item in scan]
    except OSError as error:
        raise BrowseUnreadable(
            f"目錄讀不出來：{resolved}（{error.strerror}）。"
            f"下一步：確認執行服務的身分對這個目錄有讀取與 traverse 權限"
        ) from error

    items.sort(key=lambda pair: pair[0])
    entries = tuple(
        Entry(name=name, kind="dir" if is_dir else "file", path=os.path.join(resolved, name))
        for name, is_dir in items
    )
    return Listing(path=resolved, entries=entries)
