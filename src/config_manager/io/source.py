"""io/source — T22 匯入時刻對外界的讀取.

外部互動層：這裡真的碰檔案系統。核心層不碰（ADR-00000011）。

`read_source` 與 `local_hostname` 放在同一個模組，因為它們共用同一條性質：**系統
唯一一次向外界取值的時刻**，取到之後就凍結進紀錄、永不再讀（不變式 8、
ADR-00000012、T22）。

**判定不在這裡重寫。** 「這條路徑落在白名單內嗎」由 `core/whitelist.decide` 回答
——那是純字面比對，已經在 #11 落地並驗過。這一層只做核心做不到的那半：`realpath`
解析（I/O）。少了這樣的分工就會有兩份白名單邏輯，而兩份邏輯遲早會給出兩個答案。

**順序是這支的關鍵：解析 → 判定 → 型別檢查 → 才讀。** 白名單外的來源**一個位元組
都不讀**——內容一旦被複製進 config repo 就永久留在版控裡，那時再發現已經來不及
（#174）。
"""

from __future__ import annotations

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
    SourceNotRegularFile,
    SourceOutsideRoots,
)


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
    # 先固定下來：allowed_roots 可能是一次性的可迭代物，而判定與訊息都要用到它。
    roots = tuple(allowed_roots)
    resolved = os.path.realpath(path)

    # 判定由 core 做（純字面比對，#11 已驗過）；這一層只提供它做不到的 realpath。
    if not decide(roots, resolved).allowed:
        covered = "、".join(roots) if roots else "（目前是空的）"
        raise SourceOutsideRoots(
            f"來源解析後落在白名單之外：{path} → {resolved}；目前涵蓋：{covered}。"
            f"下一步：確認這條路徑或其中一段不是指向白名單外的符號連結，"
            f"或把該位置納入白名單"
        )

    kind = _not_a_regular_file(resolved)
    if kind is not None:
        raise SourceNotRegularFile(
            f"來源不是一般檔案（{kind}）：{path} → {resolved}。"
            f"下一步：納管的對象是一份 config 檔案本身，改指向那個檔案"
        )

    # 只讀一次。解析要文字、複製要位元組、算雜湊也要位元組——對同一個活著的檔案
    # 讀三次，三次之間內容可以變，那樣「兩邊 sha256 相同」證明的就不是同一份東西
    # （T22）。這裡讀進來的這一份就是後續全部步驟共用的那一份。
    try:
        with open(resolved, "rb") as handle:
            content = handle.read()
    except OSError as error:
        raise ContentUnreadable(
            f"內容讀不出來：{path} → {resolved}（{error.strerror}）。"
            f"下一步：確認執行身分對它有讀取權限"
        ) from error

    return Source(
        content=content,
        permissions=_permissions_of(resolved),
        resolved_path=resolved,
    )


def local_hostname() -> str:
    """本機 hostname。

    T22 只保證「讀到的是本機 hostname」。它在不同部署形態下穩不穩定是部署決策
    ——容器在 bridge 網路下讀到的是每次重建都變的容器 ID（#178）。
    """
    return socket.gethostname()


# 型別名稱以中文回報，因為它會直接出現在使用者看得到的訊息裡。判斷順序無關緊要：
# 一個 inode 只會是其中一種。資料，不是邏輯：多認一種就加一列。
_KINDS: tuple[tuple[str, str], ...] = (
    ("S_ISDIR", "目錄"),
    ("S_ISFIFO", "具名管道"),
    ("S_ISSOCK", "socket"),
    ("S_ISBLK", "區塊裝置"),
    ("S_ISCHR", "字元裝置"),
)


def _not_a_regular_file(resolved: str) -> str | None:
    """不是一般檔案時回傳它是什麼；是一般檔案則回 None。"""
    try:
        mode = os.stat(resolved).st_mode
    except OSError:
        # 斷掉的符號連結、或路徑根本不存在——兩者都不是可納管的一般檔案。
        # 「不存在」與「讀不到」的細分是 #175 的範圍。
        return "不存在或無法取得狀態"

    for predicate, name in _KINDS:
        if getattr(stat, predicate)(mode):
            return name
    if not stat.S_ISREG(mode):
        return "非一般檔案"
    return None


def _permissions_of(resolved: str) -> Permissions:
    info = os.stat(resolved)
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
