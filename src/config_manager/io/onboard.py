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
7. `record`（T7）——`import(<uid>): <name>@<hostname>`，歧義確認放內文（D6）

**過程不改變來源檔案內容**（#12 第一行）：寫進 repo 的是 `read_source` 讀到的那一份
位元組，來源檔本身只被讀、不被寫。

**重複的 target／uid 攔在寫入之前**（#172）：步驟 5 的完整性檢查失敗時，步驟 6、7
都還沒跑，清單檔與 repo 完全不被改動。這正是先前沒做到的——舊順序先 `place_source`
再驗證，重複時已經留下一個殘留檔。

**寫入步驟本身失敗的回滾不在這裡**（#173）：步驟 6、7 之間若失敗（磁碟滿、權限等），
磁碟上仍會留下未提交的檔案，被下一次 `git add -A` 默默收走。那一類失敗的回滾是 #173
的範圍，它以本模組存在為前提。驗證失敗（#172）與寫入失敗（#173）是兩件事：前者在動手
之前擋下，後者是動手到一半才出事。

**parse／型別推斷／歧義偵測不在這裡。** 那些在確認畫面（#14）就做完了：使用者看過
偵測到的格式與歧義清單、確認後才呼叫 onboard，把確認過的 `fmt` 與 `ambiguity_note`
帶進來（#12 的 D4、D1）。onboard 記下它們，不重做。
"""

from __future__ import annotations

import datetime
import os
from dataclasses import dataclass

from config_manager.core.config_list import dump, load
from config_manager.core.identity import derive_name, new_uid
from config_manager.core.models import FileEntry
from config_manager.io.git import record
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.repo import place_source, source_relpath, write_config_list
from config_manager.io.source import local_hostname, read_source


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


def _list_text_with(repo: str, entry: FileEntry) -> str:
    """算出「把 entry 併進去之後」的清單檔文字，但**不寫回**。

    dump 以原檔文字為底，排版與註解原樣保留，而它在產生任何輸出**之前**先做完整性檢查
    （#181）——format 非法、target 或 uid 與既有條目重複，都在這裡丟具名例外。把這一步
    與實際寫入分開，onboard 才能先驗證、確定不重複了，再開始動 repo（#172）。
    """
    list_path = os.path.join(repo, CONFIG_LIST_NAME)
    with open(list_path, encoding="utf-8") as handle:
        original = handle.read()
    current = load(original)
    updated = current.model_copy(update={"files": [*current.files, entry]})
    return dump(updated, original)


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
    )

    # 4. 先驗證再寫。_list_text_with 在產生任何輸出之前做完整性檢查——target 或 uid 與
    #    既有條目重複就在這裡丟例外，而此刻 repo 一個位元組都還沒動（#172 的 AC2）。
    new_list_text = _list_text_with(repo, entry)

    # 5. 驗證過了才真的動 repo：先放來源位元組，再寫回清單檔。順序讓 record 的
    #    git add -A 把兩者一起收進同一筆 commit。
    place_source(repo, hostname, target, source.content)
    write_config_list(repo, new_list_text)

    # 6. 一筆 import commit。主旨是 <name>@<hostname>（設計 §2.3），歧義確認放內文（D6）。
    message = f"{name}@{hostname}"
    if request.ambiguity_note:
        message += f"\n\n{request.ambiguity_note}"
    record(repo, uid, "import", message, author)

    return entry
