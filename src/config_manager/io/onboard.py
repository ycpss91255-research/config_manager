"""io/onboard — 把一份磁碟上的 config 納入管理（#12）.

外部互動層：這裡真的碰檔案系統與 git。核心層不碰（ADR-00000011）。

**編排。** 它自己不算出新東西，而是把既有的積木依正確順序串起來——**先全部驗證，
確定不重複了，才開始寫**（#172）：

1. `read_source`（T22）——白名單判定 + realpath + 讀原始位元組與權限（一次讀完）
2. `local_hostname`（T22）——納管當下讀一次，寫進檔案後永不再讀（不變式 8）
3. `derive_name` / `new_uid`（T5）——name 取自目標路徑最後兩層，uid 取自匯入時刻
4. `source_relpath`（#186）——只算出來源檔在 repo 內的相對路徑，還不寫任何位元組
5. `config_list.dump`（T1）——算出併入後的清單檔文字；它在產生輸出前先做完整性檢查
   （#181），target 或 uid 與既有條目重複就在這裡丟例外，此刻 repo 一個位元組都還沒動
6. `place_source`（#186）＋ `write_config_list`——驗證過了才真的動 repo：放來源位元組、
   寫回清單檔
7. `stage` ＋ `record`（T7）——只 stage 這次動到的兩個路徑（**不用 `git add -A`**，否則
   會掃進不相干的未追蹤殘留，#173），再記一筆 `import(<uid>): <name>@<hostname>`，
   歧義確認放內文（D6）

**過程不改變來源檔案內容**（#12 第一行）：寫進 repo 的是 `read_source` 讀到的那一份
位元組，來源檔本身只被讀、不被寫。

**原始 owner／group／mode 一律記進 `permissions`**（#175 的 AC3）：即使剛好等於
`defaults.permissions` 也寫——每筆是原始權限的忠實快照，不因日後有人改 `defaults` 而
靜默改變已納管檔案的套用權限。**`requires_privilege` 在這裡判定**（#175 的 AC2）：納管
是系統唯一一次讀到原始 owner／group 的時刻（不變式 8），`_requires_privilege` 據此決定
——原始 owner／group 不是服務執行身分（`io/source.service_identity`）設得上的就標記為真。
真正的 sudoers 提權**寫出**是 v0.10.0（apply 端點尚未接線），這裡只決定並記下。

**重複的 target／uid 攔在寫入之前**（#172）：步驟 5 的完整性檢查失敗時，步驟 6、7
都還沒跑，清單檔與 repo 完全不被改動。這正是先前沒做到的——舊順序先 `place_source`
再驗證，重複時已經留下一個殘留檔。

**寫入步驟失敗即整批回滾**（#173）：步驟 6、7 是一個整體。中途任一步失敗（磁碟滿、
權限、commit 被 hook 擋等），onboard 把已做的還原（unstage、清單檔還原成原本的位元組、
放進去的來源檔清掉），repo 回到納管前：無殘留檔、清單檔未改、無 commit。回滾本身也失敗
時**大聲失敗**（`OnboardLeftBehind`）並指名殘留了什麼，不靜默——留在 repo 裡的孤兒來源檔
會被下一次不相干的 commit 收走，那是不變式 2 禁止的形狀。

失敗模式（甲）「清單檔寫成功、來源檔沒複製」在這個順序下不可能發生：步驟 6 一律先放來源
位元組、再寫清單檔，清單檔永遠不會領先於來源檔。會發生的都是（乙）形狀——來源檔放好了，
之後的清單檔或 commit 才失敗，那正是回滾要收拾的。驗證失敗（#172）與寫入失敗（#173）
是兩件事：前者在動手之前擋下，後者是動手到一半才出事、要靠回滾。

**parse／型別推斷／歧義偵測不在這裡。** 那些在確認畫面（#14）就做完了：使用者看過
偵測到的格式與歧義清單、確認後才呼叫 onboard，把確認過的 `fmt` 與 `ambiguity_note`
帶進來（#12 的 D4、D1）。onboard 記下它們，不重做。
"""

from __future__ import annotations

import datetime
import os
import subprocess
from dataclasses import dataclass

from config_manager.core.config_list import dump, load
from config_manager.core.identity import derive_name, new_uid
from config_manager.core.models import FileEntry, Permissions
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import OnboardLeftBehind
from config_manager.io.git import record, stage, unstage
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.repo import place_source, source_relpath, write_config_list
from config_manager.io.source import local_hostname, read_source, service_identity


@dataclass(frozen=True)
class OnboardRequest:
    """納管一份 config 需要的、由確認畫面決定的一切。

    合成一個物件而不是攤成參數，因為它們是同一次確認的產物，也讓 `onboard` 的簽名
    維持在 max-args 之內。
    """

    source_path: str
    fmt: str
    allowed_roots: tuple[str, ...]
    ambiguity_note: str = ""


@dataclass(frozen=True)
class _WritePreState:
    """納管動 repo 之前的現場，回滾時據以還原。"""

    list_text: str
    source_existed: bool
    source_bytes: bytes | None
    dir_existed: bool

    @classmethod
    def capture(cls, repo: str, relative: str, list_text: str) -> _WritePreState:
        """在寫入之前拍下現場：清單檔原文、來源檔在不在（在的話連內容一起）。"""
        absolute = os.path.join(repo, relative)
        existed = os.path.isfile(absolute)
        return cls(
            list_text=list_text,
            source_existed=existed,
            source_bytes=_read_bytes(absolute) if existed else None,
            dir_existed=os.path.isdir(os.path.dirname(absolute)),
        )


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _read_list(repo: str) -> str:
    """讀 repo 的清單檔原文。"""
    with open(os.path.join(repo, CONFIG_LIST_NAME), encoding="utf-8") as handle:
        return handle.read()


def _list_text_with(original: str, entry: FileEntry) -> str:
    """算出「把 entry 併進 `original` 之後」的清單檔文字，但**不寫回**。

    dump 以原檔文字為底，排版與註解原樣保留，而它在產生任何輸出**之前**先做完整性檢查
    （#181）——format 非法、target 或 uid 與既有條目重複，都在這裡丟具名例外。純函式：
    收原文、回新文，不碰檔案，onboard 才能先驗證、確定不重複了，再開始動 repo（#172）。
    """
    current = load(original)
    updated = current.model_copy(update={"files": [*current.files, entry]})
    return dump(updated, original)


def _requires_privilege(
    permissions: Permissions, identity: tuple[str, frozenset[str], bool]
) -> bool:
    """服務要把目標寫成這份 owner／group，需不需要提權（#175）。

    納管是系統唯一一次讀到原始 owner／group 的時刻（不變式 8），所以在這裡判定並記下，
    v0.10.0 的 sudoers 提權寫出才有依據、不必日後重讀。root 什麼都設得了 → 不需要；非
    root 只有在「owner 就是服務自己、group 是服務所屬之一」時才設得上，其餘一律標記需
    提權——保守地標，讓 apply 走提權路徑，而不是在寫出時才以 OwnershipRefused 失敗。
    """
    user, groups, is_root = identity
    if is_root:
        return False
    return not (permissions.owner == user and permissions.group in groups)


def _import_message(name: str, hostname: str, ambiguity_note: str) -> str:
    """import commit 的訊息：主旨 `<name>@<hostname>`，有歧義確認就接在內文。"""
    message = f"{name}@{hostname}"
    if ambiguity_note:
        message += f"\n\n{ambiguity_note}"
    return message


def _rollback(repo: str, relative: str, before: _WritePreState, failure: BaseException) -> None:
    """把 repo 還原成 `before` 的現場。任一步還原不了就記下，最後大聲失敗（#173）。

    不蓋掉 `failure`（`__cause__` 指向它），但也不讓孤兒殘留悄悄留著。索引先退回 HEAD，
    工作區的還原才不被殘留的暫存狀態干擾。空目錄不特別清：git 本來就忽略空目錄，`無殘留
    檔案` 這條驗收條件講的是檔案。
    """
    absolute = os.path.join(repo, relative)
    leftover: list[str] = []

    try:
        unstage(repo, relative, CONFIG_LIST_NAME)
    except (OSError, subprocess.CalledProcessError) as error:
        leftover.append(f"索引（{error}）")

    try:
        write_config_list(repo, before.list_text)
    except OSError as error:
        leftover.append(f"{CONFIG_LIST_NAME}（{error}）")

    try:
        if before.source_existed:
            replace_atomically(absolute, before.source_bytes or b"")
        elif os.path.lexists(absolute):
            os.remove(absolute)
    except OSError as error:
        leftover.append(f"{relative}（{error}）")

    if leftover:
        raise OnboardLeftBehind(
            f"納管中途失敗後回滾未竟，殘留：{'；'.join(leftover)}。"
            f"原本的失敗：{failure}。"
            f"下一步：先照原本的失敗處理，再手動清掉上列殘留——"
            f"留著的話，孤兒來源檔會被下一次不相干的 commit 收走。"
        ) from failure


def onboard(repo: str, request: OnboardRequest, author: str) -> FileEntry:
    """把 `request.source_path` 指的檔案納入 `repo` 管理，回傳新建的條目。"""
    # 1. 讀來源：白名單判定、realpath、原始位元組與權限，一次讀完（T22）。
    #    白名單外或不是一般檔案時，這一步就丟例外，repo 一個位元組都還沒動。
    source = read_source(request.source_path, request.allowed_roots)
    target = source.resolved_path

    # 2-3. 身分：hostname 讀一次，name 取自目標路徑，uid 取自匯入時刻。source 欄位是
    #    來源檔在 repo 內的相對路徑，這裡只用 source_relpath 算出來、還不寫任何位元組。
    hostname = local_hostname()
    uid = new_uid(datetime.datetime.now(datetime.timezone.utc))
    name = derive_name(target)
    relative = source_relpath(hostname, target)

    entry = FileEntry(
        uid=uid,
        name=name,
        hostname=hostname,
        source=relative,
        target=target,
        format=request.fmt,
        permissions=source.permissions,
        requires_privilege=_requires_privilege(source.permissions, service_identity()),
    )

    # 4. 先驗證再寫。_list_text_with 在產生任何輸出之前做完整性檢查——target 或 uid 與
    #    既有條目重複就在這裡丟例外，而此刻 repo 一個位元組都還沒動（#172 的 AC2）。
    original_list = _read_list(repo)
    new_list_text = _list_text_with(original_list, entry)

    # 5. 寫入是一個整批：先拍下現場，再放來源位元組、寫回清單檔、stage、記一筆 import
    #    commit。中途任一步失敗就回滾到納管前的狀態（#173）。回滾也失敗時大聲失敗。
    before = _WritePreState.capture(repo, relative, original_list)
    try:
        place_source(repo, hostname, target, source.content)
        write_config_list(repo, new_list_text)
        stage(repo, relative, CONFIG_LIST_NAME)
        record(repo, uid, "import", _import_message(name, hostname, request.ambiguity_note), author)
    except BaseException as failure:
        _rollback(repo, relative, before, failure)
        raise

    return entry
