"""core/whitelist — T4 白名單判定.

白名單管的是「哪些目錄可以納管」這個**政策**問題；逃逸防護是實作問題，兩者不混為一談
（#11）。`decide(rules, path)` 回答前者：這條路徑落在被允許的範圍內嗎。

**純字面比對，不碰檔案系統。** 規則由呼叫端傳進來，`..` 以 `posixpath.normpath` 在
字串上收斂——那是純字串運算，不查磁碟。`readlink`／`realpath` 是 I/O，**符號連結
逃逸不在這裡判定**：連結是否指向白名單外，要在寫出時點以 realpath 解析後才算數
（T8、#5）。寫出才是逃逸真正造成危害的時刻，而那時已經在 io 層。

**拒絕一定帶原因，而且原因要可行動**（#11）：說得出目前涵蓋什麼、以及下一步怎麼辦。
一句「不允許」讓使用者只能猜自己差在哪。
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    """判定結果。`reason` 只在拒絕時有值——允許不需要理由。"""

    allowed: bool
    reason: str | None = None


def decide(rules: Iterable[str], path: str) -> Decision:
    covered = tuple(rules)

    if not path.startswith("/"):
        return Decision(
            allowed=False,
            reason=(
                f"「{path}」不是絕對路徑，白名單無從比對。"
                "下一步：改用以 / 開頭的完整路徑"
            ),
        )

    normalized = posixpath.normpath(path)
    for rule in covered:
        if _within(normalized, posixpath.normpath(rule)):
            return Decision(allowed=True)

    return Decision(allowed=False, reason=_uncovered(path, normalized, covered))


def _within(normalized: str, rule: str) -> bool:
    """路徑是否落在規則底下。以路徑分界比對，不是字串開頭。

    少了分界那個 `/`，`/opt/robotics` 會被 `/opt/robot` 涵蓋——一條看起來像通過的
    比對，實際上放行了一個不在白名單裡的目錄。
    """
    if normalized == rule:
        return True
    return normalized.startswith(rule.rstrip("/") + "/")


def _uncovered(path: str, normalized: str, covered: tuple[str, ...]) -> str:
    # 正規化改變了路徑時要說出來：使用者寫的是 config/../../etc，被判定的是 /etc，
    # 只回報後者他會以為自己看錯，只回報前者他不知道為什麼被擋。
    resolved = "" if normalized == path else f"（正規化後是「{normalized}」）"
    scope = "、".join(covered) if covered else "（目前是空的）"
    return (
        f"「{path}」{resolved}不在白名單涵蓋的範圍內；目前涵蓋：{scope}。"
        "下一步：把它的上層目錄加入白名單，或改納管已涵蓋的路徑"
    )
