"""io/source — T22 匯入時刻對外界的讀取.

外部互動層：這裡真的碰檔案系統。核心層不碰（ADR-00000011）。

`read_source` 與 `local_hostname` 放在同一個模組，因為它們共用同一條性質：**系統
唯一一次向外界取值的時刻**，取到之後就凍結進紀錄、永不再讀（不變式 8、
ADR-00000012、T22）。

**判定不在這裡重寫。** 「這條路徑落在白名單內嗎」由 `core/whitelist.decide` 回答
——那是純字面比對，已經在 #11 落地並驗過。這一層只做核心做不到的那半：`realpath`
解析（I/O）。少了這樣的分工就會有兩份白名單邏輯，而兩份邏輯遲早會給出兩個答案。

**名字只解析一次，之後全部在同一個 fd 上做。** 這是這支的核心設計，理由是實測出來的：
先前的版本按路徑做了三趟（`os.stat` 判型別、`open` 讀內容、再一次 `os.stat` 取權限），
而在白名單目錄裡持續翻動「普通檔案 ↔ 指向外面的符號連結」時，**56586 次呼叫命中
481 次**，回傳了白名單外檔案的完整內容，`resolved_path` 卻仍報白名單內的路徑——出處
紀錄會說謊。改成 `os.open(O_NOFOLLOW)` 一次拿到 fd，型別、權限、內容全部來自 `fstat`
與同一個 fd，那三趟之間的窗口就不存在了，content 與 permissions 也保證來自同一個 inode。

**殘餘的窗口仍然存在**：`O_NOFOLLOW` 只保護**最後一段**，路徑中間的目錄仍可能在解析
與開檔之間被換掉。要完全關掉需要 `openat2` 的 `RESOLVE_NO_SYMLINKS`，那不在本介面的
承諾範圍內（T22 已寫下這一條）。失敗方向是**拒絕**：寧可這次匯入不成立，也不讀一份
來歷不明的內容。
"""

from __future__ import annotations

import errno
import grp
import os
import pwd
import socket
import stat
from collections.abc import Iterable
from dataclasses import dataclass

from config_manager.core.models import Permissions
from config_manager.core.whitelist import decide
from config_manager.io.errors import (
    ContentUnreadable,
    SourceAbsent,
    SourceNotRegularFile,
    SourceOutsideRoots,
    SourcePathUnstable,
    SourceUnreachable,
)

_CHUNK = 65536


@dataclass(frozen=True)
class Source:
    """匯入時刻從外界讀到的一切。

    `content` 是**位元組**而不是 `str`：納管要保證來源檔與原始檔案逐位元組相同，
    而任何一次解碼再編碼都可能在邊角上改寫位元組。`resolved_path` 是實際被讀的那
    一個路徑——呼叫端請求的可能是一條符號連結。
    """

    content: bytes
    permissions: Permissions
    resolved_path: str


def read_source(path: str, allowed_roots: Iterable[str]) -> Source:
    """讀一份要被納管的來源檔。

    失敗一律是具名例外，呼叫端據以分辨：

    - `SourceOutsideRoots` —— 解析後落在白名單之外，**一個位元組都沒讀**
    - `SourceNotRegularFile` —— 目錄、裝置、socket、斷掉的連結
    - `SourcePathUnstable` —— 解析或開檔期間路徑被改動（競速），這次匯入不成立
    - `ContentUnreadable` —— 是一般檔案但讀不出來（權限）

    「不存在／讀不到／上層目錄無 traverse 權限」的進一步細分屬 #175。
    """
    # 先固定下來：allowed_roots 可能是一次性的可迭代物，而判定與訊息都要用到它。
    roots = tuple(allowed_roots)
    resolved = _resolve(path)

    # 白名單的「根」自己也要解析。`io/writer._within_roots` 對它的 roots 就是這樣做的
    # ——同一個問題的兩道檢查不該給出不同答案。core 不能做 I/O，所以解析在這一層，
    # 判定仍然由 core（純字面比對，#11 已驗過）。
    if not decide(tuple(_resolve(root) for root in roots), resolved).allowed:
        covered = "、".join(roots) if roots else "（目前是空的）"
        raise SourceOutsideRoots(
            f"來源解析後落在白名單之外：{path} → {resolved}；目前涵蓋：{covered}。"
            f"下一步：確認這條路徑或其中一段不是指向白名單外的符號連結，"
            f"或把該位置納入白名單"
        )

    descriptor = _open_source(path, resolved)
    try:
        info = os.fstat(descriptor)
        kind = _not_a_regular_file(info.st_mode)
        if kind is not None:
            raise SourceNotRegularFile(
                f"來源不是一般檔案（{kind}）：{path} → {resolved}。"
                f"下一步：納管的對象是一份 config 檔案本身，改指向那個檔案"
            )
        content = _read_all(descriptor, path, resolved)
    finally:
        os.close(descriptor)

    return Source(
        content=content,
        permissions=_permissions_of(info),
        resolved_path=resolved,
    )


def local_hostname() -> str:
    """本機 hostname。

    T22 只保證「讀到的是本機 hostname」。它在不同部署形態下穩不穩定是部署決策
    ——容器在 bridge 網路下讀到的是每次重建都變的容器 ID（#178）。
    """
    return socket.gethostname()


def _resolve(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError as error:
        raise SourcePathUnstable(
            f"解析路徑時它正在被改動：{path}（{error.strerror}）。"
            f"下一步：確認沒有其他程序正在改寫這條路徑上的符號連結，然後重試"
        ) from error


def _open_source(path: str, resolved: str) -> int:
    """以 `O_NOFOLLOW` 開檔；失敗時把原因分類成具名例外。

    `O_NONBLOCK` 讓具名管道不會把整個匯入卡住——開得成之後 `fstat` 會認出它不是
    一般檔案並拒絕。
    """
    try:
        return os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise _classify_open_failure(path, resolved, error) from error


def _classify_open_failure(path: str, resolved: str, error: OSError) -> Exception:
    """把 `os.open` 的失敗分成四種——它們的成因與處置各不相同（#182）。

    把「不存在」「讀不到」「上層目錄擋住去路」混成同一種，等於把訊號消掉：使用者
    收到「不是一般檔案」時會去改錯的東西（不變式 2，`io/digest` 的 docstring 寫過
    同一件事）。
    """
    if error.errno == errno.ELOOP:
        # 解析之後最後一段又變成了符號連結——那就是競速本身。
        return SourcePathUnstable(
            f"開檔時來源變成了符號連結：{path} → {resolved}。"
            f"下一步：確認沒有其他程序正在改寫這條路徑，然後重試"
        )

    if error.errno == errno.ENOENT:
        if os.path.islink(path):
            # realpath 把連結收斂成了不存在的目標——那是一條斷掉的符號連結。
            return SourceNotRegularFile(
                f"來源是一條斷掉的符號連結（指向不存在的目標）：{path} → {resolved}。"
                f"下一步：納管的對象是一份 config 檔案本身，改指向那個檔案"
            )
        return SourceAbsent(
            f"來源不存在：{path} → {resolved}。"
            f"下一步：確認路徑拼對了，且那份檔案真的在那個位置"
        )

    if error.errno in (errno.EACCES, errno.EPERM):
        blocker = _blocking_parent(resolved)
        if blocker is not None:
            return SourceUnreachable(
                f"上層目錄擋住去路（「{blocker}」沒有 traverse 權限）：{path}。"
                f"下一步：給那個目錄加上執行（+x）權限，或改由讀得到它的身分執行"
            )
        return ContentUnreadable(
            f"內容讀不出來：{path} → {resolved}（{error.strerror}）。"
            f"下一步：確認執行身分對它有讀取權限"
        )

    return SourceNotRegularFile(
        f"來源開不起來（{error.strerror}）：{path} → {resolved}。"
        f"下一步：確認它是一份一般檔案（不是裝置或 socket）"
    )


def _blocking_parent(resolved: str) -> str | None:
    """去不到 `resolved` 時，是哪一層目錄擋住的；若目標本身可 `stat` 則回 None。

    從根往下逐層 `stat`：第一個 `stat` 不了的祖先，它的上一層就是缺 `+x` 的那個
    目錄。目標本身 `stat` 得到（EACCES 來自檔案自己的讀取權限、不是 traverse）時
    回 None，交給呼叫端判為 `ContentUnreadable`。
    """
    reachable = "/"
    for ancestor in _ancestors(resolved)[1:]:
        try:
            os.stat(ancestor)
        except OSError:
            return reachable
        reachable = ancestor
    return None


def _ancestors(path: str) -> list[str]:
    """path 從根到它自己的每一層，根在最前面。"""
    chain = []
    current = path
    while True:
        chain.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return list(reversed(chain))


def _read_all(descriptor: int, path: str, resolved: str) -> bytes:
    """把整個 fd 讀完。

    只讀這一次：解析要文字、複製要位元組、算雜湊也要位元組，對同一個活著的檔案
    分三趟讀，三趟之間內容可以變，那樣「兩邊 sha256 相同」證明的就不是同一份東西
    （T22）。這裡讀進來的這一份就是後續全部步驟共用的那一份。
    """
    blocks: list[bytes] = []
    try:
        while True:
            block = os.read(descriptor, _CHUNK)
            if not block:
                return b"".join(blocks)
            blocks.append(block)
    except OSError as error:
        raise ContentUnreadable(
            f"內容讀不出來：{path} → {resolved}（{error.strerror}）。"
            f"下一步：確認執行身分對它有讀取權限"
        ) from error


# 型別名稱以中文回報，因為它會直接出現在使用者看得到的訊息裡。一個 inode 只會是其中
# 一種，所以順序無關緊要。資料，不是邏輯：多認一種就加一列。
_KINDS: tuple[tuple[str, str], ...] = (
    ("S_ISDIR", "目錄"),
    ("S_ISFIFO", "具名管道"),
    ("S_ISSOCK", "socket"),
    ("S_ISBLK", "區塊裝置"),
    ("S_ISCHR", "字元裝置"),
)


def _not_a_regular_file(mode: int) -> str | None:
    """不是一般檔案時回傳它是什麼；是一般檔案則回 None。

    吃的是 `fstat` 的 mode 而不是路徑——型別必須與內容來自同一個 inode，否則判定的
    與讀到的可以是兩個不同的東西。
    """
    for predicate, name in _KINDS:
        if getattr(stat, predicate)(mode):
            return name
    if not stat.S_ISREG(mode):
        return "非一般檔案"
    return None


def _permissions_of(info: os.stat_result) -> Permissions:
    """權限來自已開啟的那個 fd 的 `fstat`，不是對路徑的第二次查詢。

    按路徑再查一次可能查到另一個 inode——那樣回報的權限描述的就不是被讀進來的
    那一份內容。
    """
    return Permissions(
        owner=_owner_name(info.st_uid),
        group=_group_name(info.st_gid),
        mode=f"{stat.S_IMODE(info.st_mode):04o}",
    )


def _owner_name(uid: int) -> str:
    # 容器裡的使用者常常沒有 passwd 項目（`io/writer` 記過實測 uid 501 查不到名字）。
    # 查不到就回數字形式——那不是取巧，是部署環境的現實，而且它仍然是正確的擁有者。
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def _group_name(gid: int) -> str:
    try:
        return grp.getgrgid(gid).gr_name
    except KeyError:
        return str(gid)
