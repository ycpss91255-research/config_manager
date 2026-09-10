"""io/onboard — 把一份磁碟上的 config 納入管理（#12）.

外部互動層：這裡真的碰檔案系統與 git。核心層不碰（ADR-00000011）。

**編排。** 它自己不算出新東西，而是把既有的積木依正確順序串起來：

1. `read_source`（T22）——白名單判定 + realpath + 讀原始位元組與權限（一次讀完）
2. `local_hostname`（T22）——納管當下讀一次，寫進檔案後永不再讀（不變式 8）
3. `derive_name` / `new_uid`（T5）——name 取自目標路徑最後兩層，uid 取自匯入時刻
4. `place_source`（#186）——把位元組逐位元組放進 `files/<hostname>/<編碼>`
5. `config_list.dump`（T1）——把新條目寫進清單檔文字，寫出前先做完整性檢查（#181）
6. `record`（T7）——`import(<uid>): <name>@<hostname>`，歧義確認放內文（D6）

**過程不改變來源檔案內容**（#12 第一行）：寫進 repo 的是 `read_source` 讀到的那一份
位元組，來源檔本身只被讀、不被寫。

**中途失敗的回滾不在這裡**（#173）：place_source → write_config_list → record 三步，
`record` 的 `git add -A` 會把前兩步的檔案變更一起收進同一筆 commit，所以 git 層面是
一次成一次的；但三步之間若失敗，磁碟上會留下未提交的檔案。補上回滾是 #173 的範圍，
它以本模組存在為前提。

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
from config_manager.io.repo import place_source, write_config_list
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


def _append_to_list(repo: str, entry: FileEntry) -> None:
    """把新條目併進 repo 的清單檔。

    dump 以原檔文字為底，排版與註解原樣保留；寫出前先做完整性檢查（#181）——不合法的
    format、重複的 target／uid 會在這裡被擋下。此時檔案已放進 repo，交給 #173 的回滾處理。
    """
    list_path = os.path.join(repo, CONFIG_LIST_NAME)
    with open(list_path, encoding="utf-8") as handle:
        original = handle.read()
    current = load(original)
    updated = current.model_copy(update={"files": [*current.files, entry]})
    write_config_list(repo, dump(updated, original))


def onboard(repo: str, request: OnboardRequest, author: str) -> FileEntry:
    """把 `request.source_path` 指的檔案納入 `repo` 管理，回傳新建的條目。"""
    # 1. 讀來源：白名單判定、realpath、原始位元組與權限，一次讀完（T22）。
    #    白名單外或不是一般檔案時，這一步就丟例外，repo 一個位元組都還沒動。
    source = read_source(request.source_path, request.allowed_roots)
    target = source.resolved_path

    # 2-3. 身分：hostname 讀一次，name 取自目標路徑，uid 取自匯入時刻。
    hostname = local_hostname()
    uid = new_uid(datetime.datetime.now(datetime.timezone.utc))
    name = derive_name(target)

    # 4. 把位元組放進 repo，拿回它在 repo 內的相對路徑（那就是 source 欄位）。
    relative = place_source(repo, hostname, target, source.content)

    entry = FileEntry(
        uid=uid,
        name=name,
        hostname=hostname,
        source=relative,
        target=target,
        format=request.fmt,
        permissions=source.permissions,
    )

    # 5. 把新條目寫進清單檔（見 _append_to_list：dump 前先做完整性檢查）。
    _append_to_list(repo, entry)

    # 6. 一筆 import commit。主旨是 <name>@<hostname>（設計 §2.3），歧義確認放內文（D6）。
    #    record 的 git add -A 把 place_source 與清單檔的變更一起收進這筆 commit。
    message = f"{name}@{hostname}"
    if request.ambiguity_note:
        message += f"\n\n{request.ambiguity_note}"
    record(repo, uid, "import", message, author)

    return entry
