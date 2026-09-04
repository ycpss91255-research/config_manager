"""io/git — 以 subprocess 包裝 git CLI（ADR-00000007、ADR-00000009）。

不使用 GitPython／pygit2：CLI 的行為與人工操作完全一致，除錯時可以直接把指令
複製出來重現；函式庫的抽象在 revert 與 log 過濾這些場景反而增加不確定性。

commit 訊息是 `<類型>(<uid>): <說明>`。scope 只放 uid——name 與 hostname 都可改，
寫進歷史會讓前後兩筆對不起來（ADR-00000007）。
"""

import re
import subprocess
from typing import NamedTuple

from config_manager.io.errors import UnknownKind

# 變更紀錄的類型（CONTEXT）。介面上顯示的是行為描述，這些代號只進 commit 訊息。
KINDS = ("import", "cfg", "revert", "adopt", "meta", "unmanage")

_SUBJECT = re.compile(r"^(?P<kind>[a-z]+)\((?P<uid>[^)]+)\): (?P<summary>.*)$")
_UNIT = "\x1f"


class Change(NamedTuple):
    """一筆變更紀錄，如 history 所見。"""

    sha: str
    kind: str
    uid: str
    summary: str
    author: str


def _git(repo: str, *args: str) -> str:
    """跑一次 git，回傳 stdout。失敗時 CalledProcessError 帶著 git 的原始輸出。"""
    completed = subprocess.run(
        ["git", "-C", repo, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _split_author(author: str) -> tuple[str, str]:
    """把 `姓名 <email>` 拆成兩半。"""
    name, _, email = author.partition("<")
    return name.strip(), email.rstrip(">").strip()


def record(repo: str, uid: str, kind: str, summary: str, author: str) -> None:
    """把工作區目前的狀態記成一筆變更。"""
    if kind not in KINDS:
        raise UnknownKind(
            f"不是允許的變更類型：{kind}。允許的是 {'／'.join(KINDS)}。"
            f"下一步：改用其中一個；介面上顯示的行為描述由上層對應，不進 commit 訊息。"
        )

    name, email = _split_author(author)
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "commit",
        "-q",
        "-m",
        f"{kind}({uid}): {summary}",
    )


def history(repo: str, uid: str, kind: str | None = None) -> list[Change]:
    """某個 uid 的變更紀錄，最新的在前。給了類型就只回那個類型。"""
    output = _git(repo, "log", f"--format=%H{_UNIT}%s{_UNIT}%an <%ae>")
    changes: list[Change] = []
    for line in output.splitlines():
        if not line:
            continue
        sha, subject, author = line.split(_UNIT)
        matched = _SUBJECT.match(subject)
        # 不符格式的（例如 repo 的初始 commit）不是變更紀錄，略過。
        if matched is None or matched["uid"] != uid:
            continue
        if kind is not None and matched["kind"] != kind:
            continue
        changes.append(
            Change(
                sha=sha,
                kind=matched["kind"],
                uid=matched["uid"],
                summary=matched["summary"],
                author=author,
            )
        )
    return changes


def revert(repo: str, uid: str, version: str, source: str, author: str) -> None:
    """把某個 uid 的來源內容退回指定版本，並記成一筆新的變更。

    以反向變更實作：先還原內容，再記一筆 revert 紀錄。不移動指標、不改寫歷史
    （ADR-00000005），所以退版本身也留在歷史裡、也可以再被退。

    source 由呼叫端給——uid 對應到哪個來源檔是清單檔的知識，io 層不該自己推斷。
    """
    _git(repo, "checkout", version, "--", source)
    record(repo, uid, "revert", f"退回 {version[:7]}", author)
