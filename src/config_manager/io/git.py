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
_RECORD = "\x1e"


class Change(NamedTuple):
    """一筆變更紀錄，如 history 所見。"""

    sha: str
    kind: str
    uid: str
    summary: str
    author: str
    body: str = ""


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


def stage(repo: str, *paths: str) -> None:
    """把指定的路徑加入索引（`git add -- <paths>`）。

    只 stage 明確點名的路徑，不用 `git add -A`：後者會把工作區裡**任何**未追蹤檔案一起
    收進來，包括與這次變更不相干的殘留——例如某次失敗的納管留下的孤兒來源檔，會被下一筆
    不相干的 commit 默默收編（#173）。一筆 commit 該只含這次真的動到的東西。
    """
    _git(repo, "add", "--", *paths)


def record(repo: str, uid: str, kind: str, message: str, author: str) -> None:
    """把**已 staged** 的變更記成一筆 commit。

    staging 是呼叫端的事（見 `stage`）：record 不再自己 `git add -A`，於是 commit 只含
    呼叫端明確 stage 的路徑，不會掃進不相干的殘留（#173）。沒有任何 staged 變更時 git
    的 commit 會失敗——那是對的，一筆什麼都沒動的變更紀錄不該存在。

    `message` 是完整的 commit 訊息：第一段（第一個空行之前）是主旨，之後是內文。主旨
    進 `<kind>(<uid>): <說明>`，內文原樣進 commit body、透過 `history()` 的 `Change.body`
    取回。納管把歧義值的確認放內文——主旨已被 `<name>@<hostname>` 占滿（設計 §2.3），
    而確認清單可能有好幾行（#12 的 D6）。沒有內文時（大多數變更）就只有主旨。
    """
    if kind not in KINDS:
        raise UnknownKind(
            f"不是允許的變更類型：{kind}。允許的是 {'／'.join(KINDS)}。"
            f"下一步：改用其中一個；介面上顯示的行為描述由上層對應，不進 commit 訊息。"
        )

    subject, _, body = message.partition("\n\n")
    name, email = _split_author(author)
    # 第二個 -m 就是 commit 內文；git 以一個空行把它與主旨隔開。沒有內文就不加。
    parts = ["-m", f"{kind}({uid}): {subject}"]
    if body:
        parts += ["-m", body]
    _git(
        repo,
        "-c",
        f"user.name={name}",
        "-c",
        f"user.email={email}",
        "commit",
        "-q",
        *parts,
    )


def history(repo: str, uid: str, kind: str | None = None) -> list[Change]:
    """某個 uid 的變更紀錄，最新的在前。給了類型就只回那個類型。"""
    # 以記錄分隔符 %x1e 切 commit，而不是逐行切：commit 內文（%b）有換行，逐行解析
    # 會把一筆拆成好幾筆。%b 放在最後，它裡面的換行於是不影響前面的欄位。
    fmt = f"%H{_UNIT}%s{_UNIT}%an <%ae>{_UNIT}%b{_RECORD}"
    output = _git(repo, "log", f"--format={fmt}")
    changes: list[Change] = []
    for raw in output.split(_RECORD):
        # git 在每筆之間補一個換行；剝掉它才不會把換行算進內文的頭尾。
        entry = raw.strip("\n")
        if not entry:
            continue
        sha, subject, author, body = entry.split(_UNIT)
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
                body=body,
            )
        )
    return changes


def revert(repo: str, uid: str, version: str, source: str, author: str) -> None:
    """把某個 uid 的來源內容退回指定版本，並記成一筆新的變更。

    以反向變更實作：先還原內容，再記一筆 revert 紀錄。不移動指標、不改寫歷史
    （ADR-00000005），所以退版本身也留在歷史裡、也可以再被退。

    source 由呼叫端給——uid 對應到哪個來源檔是清單檔的知識，io 層不該自己推斷。
    """
    # checkout <version> -- <source> 會同時更新工作區與索引，也就是說 source 已被 stage；
    # record 只提交已 staged 的內容，所以這裡不需要再 stage 一次。
    _git(repo, "checkout", version, "--", source)
    record(repo, uid, "revert", f"退回 {version[:7]}", author)
