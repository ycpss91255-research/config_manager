"""io/browse — 檔案系統瀏覽，受白名單限制（設計 §3.5.3；供納管使用）。

外部互動層：這裡真的碰檔案系統。核心層不碰（ADR-00000011）。

**判定不在這裡重寫**（同 `io/source`）：「這條路徑落在白名單內嗎」由 `core/whitelist.decide`
回答——純字面比對，已在 #11 落地並驗過。這一層只做核心做不到的那半：`realpath` 解析（I/O）
與列目錄。少了這樣的分工就會有兩份白名單邏輯，而兩份邏輯遲早會給出兩個答案。

**判定與列舉之間不留 TOCTOU 窗口，比照 `io/source` 的做法（#174、#185 的資安審查）：**
先 `realpath` 解析、對解析結果做白名單判定，接著以 `O_NOFOLLOW | O_DIRECTORY` 開出一個 fd
並在**那個 fd 上** `scandir`。這樣即使解析後有人把最後一段換成指向白名單外的符號連結，
`O_NOFOLLOW` 會讓開檔失敗（不會被騙去列 `/etc`），而 fd 一旦開成就釘住了那個 inode，之後
的路徑改寫也影響不到已在列的目標。**殘餘窗口**與 `io/source` 相同：`O_NOFOLLOW` 只保護最後
一段，路徑中間的目錄仍可能在解析與開檔之間被抽換——那一段是 realpath 的既知限制（T22）。

列出來的項目以**項目自身的型別**分類（`is_dir(follow_symlinks=False)`），不跟隨符號連結：
一個指向白名單外目錄的連結會被歸為 `file`、而不是洩漏「它的目標是不是目錄」這一個位元。
要進到子目錄或挑檔案時，各自的白名單檢查會再跑一次（browse 進子目錄同樣 `O_NOFOLLOW`、
`io/source.read_source` 挑檔案），所以列舉本身只給名字與自身型別、不越過邊界。
"""

from __future__ import annotations

import errno
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
    """目錄裡的一項。`kind` 是 `dir`（可再進去）或 `file`（可挑來納管）。符號連結以其
    自身型別分類，不跟隨——所以指向目錄的連結是 `file`，不越過白名單洩漏目標型別。"""

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
    - `BrowseNotADirectory` —— 白名單內，但不是可列的目錄（是檔案、符號連結、不存在，
      或解析後最後一段被換成符號連結）
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

    descriptor = _open_directory(path, resolved)
    try:
        entries = _entries(descriptor, resolved)
    finally:
        os.close(descriptor)
    return Listing(path=resolved, entries=entries)


def _open_directory(path: str, resolved: str) -> int:
    """以 `O_NOFOLLOW | O_DIRECTORY` 開出目錄的 fd；失敗時分類成具名例外。

    `O_NOFOLLOW` 讓「解析後最後一段變成符號連結」的競速開檔失敗，而不是被騙去列它指向的
    地方；`O_DIRECTORY` 讓非目錄直接以 `ENOTDIR` 失敗。
    """
    try:
        return os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM):
            raise BrowseUnreadable(
                f"目錄讀不出來：{path} → {resolved}（{error.strerror}）。"
                f"下一步：確認執行服務的身分對這個目錄有讀取與 traverse 權限"
            ) from error
        # ENOTDIR（不是目錄）、ELOOP（最後一段是符號連結，或解析後被抽換）、ENOENT
        # （不存在）都歸這裡：browse 列的是白名單內一個實實在在的目錄。
        raise BrowseNotADirectory(
            f"瀏覽的對象不是可列的目錄：{path} → {resolved}（{error.strerror}）。"
            f"下一步：指向白名單內一個真正的目錄，或直接把這個檔案拿去納管"
        ) from error


def _entries(descriptor: int, resolved: str) -> tuple[Entry, ...]:
    """在已開啟的 fd 上列目錄。型別以項目自身分類，不跟隨符號連結。"""
    try:
        with os.scandir(descriptor) as scan:
            items = [(item.name, item.is_dir(follow_symlinks=False)) for item in scan]
    except OSError as error:
        raise BrowseUnreadable(
            f"目錄讀不出來：{resolved}（{error.strerror}）。"
            f"下一步：確認執行服務的身分對這個目錄有讀取權限"
        ) from error

    items.sort(key=lambda pair: pair[0])
    return tuple(
        Entry(name=name, kind="dir" if is_dir else "file", path=os.path.join(resolved, name))
        for name, is_dir in items
    )
