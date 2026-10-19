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

**指向檔案時（F1，人工驗收 U05）**：開目錄得到「不是目錄」，就改列它的**上層目錄**，並以
`selected` 標出那一個——和逐層點進去再點它一樣，不自動納管。上層目錄**重跑一次白名單判定**
（解析後的檔本身就是白名單的根時，上層不在白名單內，不列它）；同樣以 `O_NOFOLLOW | O_DIRECTORY`
開出 fd、在那個 fd 上 `fstatat(..., follow_symlinks=False)` 確認那一項是**一般檔案**才選
（連結、具名管道、裝置都不選——Linux 上以 `O_NOFOLLOW | O_DIRECTORY` 開連結得到的也是
`ENOTDIR`，不先確認就會誤選連結迴圈）。白名單外的路徑在判定那一步就被拒，走不到這裡，
所以不會因此透露白名單外的檔案在不在。路徑不存在（含中間一段是檔案）是 `BrowseNotFound`。

**realpath 要解析得出唯一的位置**（`io/paths.stable_realpath`）：Python 3.11 的 realpath 遇到連結
迴圈就不再解析後面那幾段，`loop/..` 能讓一條經過「指向白名單外的連結」的路徑看起來落在白名單內。
解析結果不是不動點就直接拒絕，不拿它去判定（F1 的審查；修之前連目錄瀏覽都能這樣列出白名單外）。

列出來的項目以**項目自身的型別**分類（`is_dir(follow_symlinks=False)`），不跟隨符號連結：
一個指向白名單外目錄的連結會被歸為 `file`、而不是洩漏「它的目標是不是目錄」這一個位元。
要進到子目錄或挑檔案時，各自的白名單檢查會再跑一次（browse 進子目錄同樣 `O_NOFOLLOW`、
`io/source.read_source` 挑檔案），所以列舉本身只給名字與自身型別、不越過邊界。
"""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass

from config_manager.core.whitelist import decide
from config_manager.io.errors import (
    BrowseError,
    BrowseNotADirectory,
    BrowseNotFound,
    BrowseOutsideRoots,
    BrowseUnreadable,
)
from config_manager.io.paths import blocking_parent, describe_mode, stable_realpath


@dataclass(frozen=True)
class Entry:
    """目錄裡的一項。`kind` 是 `dir`（可再進去）或 `file`（可挑來納管）。符號連結以其
    自身型別分類，不跟隨——所以指向目錄的連結是 `file`，不越過白名單洩漏目標型別。"""

    name: str
    kind: str
    path: str


@dataclass(frozen=True)
class Listing:
    """一次瀏覽的結果：解析後的目錄，與它底下的項目（依名字排序）。指向檔案時，`path` 是它
    所在的目錄，`selected` 是那個檔（解析後的真正路徑）；瀏覽目錄時 `selected` 是 None。"""

    path: str
    entries: tuple[Entry, ...]
    selected: str | None = None


def browse(path: str, allowed_roots: Iterable[str]) -> Listing:
    """列出 `path` 這個目錄的內容，限制在白名單 `allowed_roots` 之內。指向白名單內的一般
    檔案時，列它所在的目錄並以 `selected` 標出它（F1）。

    失敗一律是具名例外：

    - `BrowseOutsideRoots` —— 解析後落在白名單之外，一項都不列
    - `BrowseNotFound` —— 白名單內，但不存在（含路徑中間一段是檔案）
    - `BrowseNotADirectory` —— 白名單內，但既不是可列的目錄、也不是可選的一般檔案（具名管道、
      裝置、符號連結、連結迴圈），或它本身就是白名單的根而不是目錄
    - `BrowseUnreadable` —— 是白名單內的目錄（或檔案所在的目錄），但列不出來、進不去（權限）
    """
    roots = tuple(allowed_roots)
    stable = stable_realpath(path)
    if stable is None:
        # 路上有沒解析完的連結（連結迴圈之後接 `..`）：白名單判定看的字串與核心實際會走到的位置
        # 可能不同——不判定、不開檔。不帶解析結果，也不分白名單內外，回應不透露路上有什麼。
        raise BrowseNotADirectory(
            f"這條路徑解析不出唯一的真正位置（路上有連結迴圈）：{path}。"
            f"下一步：改貼不經過連結的完整路徑"
        )
    resolved = stable
    # 白名單的根自己也要解析（同 io/source）：同一個問題的兩道檢查不該給出不同答案。
    resolved_roots = tuple(os.path.realpath(root) for root in roots)

    if not decide(resolved_roots, resolved).allowed:
        covered = "、".join(roots) if roots else "（目前是空的）"
        # 建議加進白名單的前綴：被拒目標的目錄 realpath；目標是檔案（或不存在）則取父目錄
        # ——白名單根必須是目錄（add_allowed_root 會擋非目錄），前端的「加入白名單」預填要的
        # 是這個解析後的目錄，不是使用者打的原字串（#13）。
        suggested = resolved if os.path.isdir(resolved) else os.path.dirname(resolved)
        raise BrowseOutsideRoots(
            f"瀏覽路徑落在白名單之外：{path} → {resolved}；目前涵蓋：{covered}。"
            f"下一步：確認這條路徑或其中一段不是指向白名單外的符號連結，"
            f"或把該位置納入白名單",
            resolved=resolved,
            suggested=suggested,
        )

    try:
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as error:
        if error.errno == errno.ENOTDIR:
            # 不是目錄：可能是檔案（去上層選它）、路徑中間一段是檔案，或（Linux）最後一段是
            # 符號連結——一律交給上層那一關分辨，不在這裡猜。
            return _select_in_parent(path, resolved, resolved_roots)
        raise _open_failure(path, resolved, error) from error
    try:
        entries = _entries(descriptor, resolved)
    finally:
        os.close(descriptor)
    return Listing(path=resolved, entries=entries)


def _open_failure(path: str, resolved: str, error: OSError) -> BrowseError:
    """開目錄失敗（不是 `ENOTDIR`）時的具名例外。

    `O_NOFOLLOW` 讓「解析後最後一段變成符號連結」的競速開檔失敗，而不是被騙去列它指向的
    地方；`O_DIRECTORY` 讓非目錄直接失敗（`ENOTDIR` 由呼叫端轉去上層選檔）。
    """
    if error.errno in (errno.EACCES, errno.EPERM):
        blocker = blocking_parent(resolved)
        if blocker is not None:
            # 擋住的是上層某一層（缺 traverse）：指名那一層，不把整條路徑說成「目錄讀不出來」。
            return BrowseUnreadable(
                f"去不到這條路徑：{path} → {resolved}；擋住的是 {blocker}（{error.strerror}）。"
                f"下一步：確認執行服務的身分對 {blocker} 有 traverse（x）權限",
                resolved=resolved,
            )
        return BrowseUnreadable(
            f"目錄讀不出來：{path} → {resolved}（{error.strerror}）。"
            f"下一步：確認執行服務的身分對這個目錄有讀取與 traverse 權限",
            resolved=resolved,
        )
    if error.errno == errno.ENOENT:
        return _not_found(path, resolved, error.strerror)
    # ELOOP（macOS：最後一段是符號連結，或解析後被抽換）與其他：不是能列、也不是能選的東西。
    return BrowseNotADirectory(
        f"瀏覽的對象不是可列的目錄：{path} → {resolved}（{error.strerror}）。"
        f"下一步：指向白名單內一個目錄，或白名單內的一般檔案",
        resolved=resolved,
    )


def _not_found(path: str, resolved: str, reason: str | None) -> BrowseNotFound:
    # 人工驗收 U10：不存在就說找不到、請檢查——不建議納管（那是做不到的下一步）。
    return BrowseNotFound(
        f"找不到這個路徑：{path} → {resolved}（{reason}）。"
        f"下一步：檢查路徑有沒有打錯，並確認那個檔案或目錄確實存在",
        resolved=resolved,
    )


def _select_in_parent(path: str, resolved: str, resolved_roots: tuple[str, ...]) -> Listing:
    """`resolved` 不是目錄：若它是白名單內的一般檔案，列它所在的目錄並選它（F1）。

    上層目錄重跑白名單判定；以 `O_NOFOLLOW | O_DIRECTORY` 開它、在那個 fd 上確認那一項的**自身**
    型別（不跟隨連結）是一般檔案才選。
    """
    parent, name = os.path.split(resolved)
    if not decide(resolved_roots, parent).allowed:
        # 解析後的檔本身就是白名單的根（API 加不進去，設定檔被手改才會這樣）：上層目錄不在
        # 白名單內，不列它。
        raise BrowseNotADirectory(
            f"白名單的根不是目錄：{path} → {resolved}。"
            f"下一步：請開發者在白名單維護把它換成它所在的目錄",
            resolved=resolved,
        )
    try:
        descriptor = os.open(parent, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM):
            # 上層目錄進得去、列不出來（只有 x）：不硬選，指名是哪個目錄。
            raise BrowseUnreadable(
                f"檔案所在的目錄讀不出來：{parent}（{error.strerror}）。"
                f"下一步：確認執行服務的身分對這個目錄有讀取權限",
                resolved=parent,
            ) from error
        # 上層也不是目錄或不在了：路徑中間一段是檔案（x.yaml/inner）——這條路徑不存在。
        raise _not_found(path, resolved, error.strerror) from error
    try:
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError as error:
            raise _not_found(path, resolved, error.strerror) from error
        except OSError as error:
            raise BrowseUnreadable(
                f"檔案所在的目錄讀不出來：{parent}（{error.strerror}）。"
                f"下一步：確認執行服務的身分對這個目錄有讀取與 traverse 權限",
                resolved=parent,
            ) from error
        if not stat.S_ISREG(info.st_mode):
            raise BrowseNotADirectory(
                f"不是可以選取的一般檔案（{describe_mode(info.st_mode)}）：{path} → {resolved}。"
                f"下一步：指向白名單內的一般檔案，或它所在的目錄",
                resolved=resolved,
            )
        entries = _entries(descriptor, parent)
    finally:
        os.close(descriptor)
    return Listing(path=parent, entries=entries, selected=resolved)


def _entries(descriptor: int, resolved: str) -> tuple[Entry, ...]:
    """在已開啟的 fd 上列目錄。型別以項目自身分類，不跟隨符號連結。"""
    try:
        with os.scandir(descriptor) as scan:
            items = [(item.name, item.is_dir(follow_symlinks=False)) for item in scan]
    except OSError as error:
        raise BrowseUnreadable(
            f"目錄讀不出來：{resolved}（{error.strerror}）。"
            f"下一步：確認執行服務的身分對這個目錄有讀取權限",
            resolved=resolved,
        ) from error

    items.sort(key=lambda pair: pair[0])
    return tuple(
        Entry(name=name, kind="dir" if is_dir else "file", path=os.path.join(resolved, name))
        for name, is_dir in items
    )
