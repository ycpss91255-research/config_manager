"""io/repo — 寫進 config repo（#186）.

外部互動層：這裡真的碰檔案系統。核心層不碰（ADR-00000011）。

納管要把來源檔的**位元組**放進 config repo，並把更新後的清單檔寫回。`io/writer` 不
合身：它收 `str`、固定 utf-8、且為**部署目標**設計（白名單逃逸檢查、套用 owner／
group／mode）。寫進 repo 內部是另一件事——受信任的路徑、保留原始位元組、不套權限
（git 只追蹤 executable bit，原始權限記在清單檔）。

原子寫出不重寫：兩個函式都走 `io/atomic.replace_atomically`（ADR-00000006）。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

from pydantic import ValidationError
from tomlkit.exceptions import TOMLKitError

from config_manager.core.config_list import load
from config_manager.core.errors import ConfigListError
from config_manager.core.models import ConfigList
from config_manager.io.atomic import replace_atomically
from config_manager.io.errors import ConfigListUnparsable, WriterError
from config_manager.io.preflight import CONFIG_LIST_NAME

_SOURCES_DIR = "files"


def source_relpath(hostname: str, target: str) -> str:
    """`target` 的來源檔在 repo 內的相對路徑：`files/<hostname>/<目標路徑編碼>`。

    只算路徑、不碰檔案，於是呼叫端可以在**還沒寫任何位元組之前**就取得這個值——納管
    要先拿它組出條目、通過完整性檢查，確定不重複了才真的寫下去（#172）。

    目標路徑編成單一檔名（`_encode`，D3）：一個機器上不同目錄的同名檔（`/opt/a/conf`
    與 `/etc/a/conf`）於是在 `files/<hostname>/` 底下不會互相覆蓋，而編碼後的名字仍看得出
    它原本從哪來。編碼是單射的——不同目標一定編成不同檔名（#192）。
    """
    return os.path.join(_SOURCES_DIR, hostname, _encode(target))


def place_source(repo: str, hostname: str, target: str, content: bytes) -> str:
    """把 `content` 逐位元組放進 `files/<hostname>/<目標路徑編碼>`，回傳 repo 內的相對
    來源路徑（那正是要填進 `FileEntry.source` 的值，等同 `source_relpath`）。
    """
    relative = source_relpath(hostname, target)
    absolute = os.path.join(repo, relative)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    replace_atomically(absolute, content)
    return relative


def load_list(repo: str) -> tuple[str, ConfigList]:
    """讀＋載入既有清單，回（原文, 已驗證模型）。讀（非 UTF-8）與載入（壞 TOML／未知欄位／不符
    模型／既有就重複）的失敗都是伺服器端資料損壞，映成 ConfigListUnparsable（結構化 500，與
    scan／preflight 一致，#248/#257）。會改寫清單檔的編排（納管、解除納管、產生 schema）共用。

    讀檔也要包在這裡：非 UTF-8 的 UnicodeDecodeError 在 open().read() 就發生，先前若只包 load
    會逃逸成裸 500。tomlkit 對壞 TOML 丟 TOMLKitError（非 core.ParseError——接那個是死碼，#257）；
    load 的形狀／未知欄位／完整性丟 ConfigListError；model_validate 丟 ValidationError。
    """
    path = os.path.join(repo, CONFIG_LIST_NAME)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        return text, load(text)
    except (UnicodeDecodeError, TOMLKitError, ValidationError, ConfigListError) as error:
        raise ConfigListUnparsable(
            f"清單檔無法解析：{path}——{error}。"
            "下一步：依訊息指出的位置修正該檔，或以 UTF-8 重新存檔",
            file=path,
        ) from error


def write_config_list(repo: str, text: str) -> None:
    """把清單檔文字原子寫回 `repo/config-list.toml`。"""
    replace_atomically(os.path.join(repo, CONFIG_LIST_NAME), text.encode("utf-8"))


def _encode(target: str) -> str:
    """把絕對目標路徑編成單一、**互不碰撞**的檔名。

    去掉開頭的 `/`，先把路徑裡原有的 `_` 跳脫成 `_5f`（`5f` 是 `_` 的 ASCII 十六進位），
    再把分隔用的 `/` 編成 `__`。順序不可反：若先 `/`→`__` 再跳脫 `_`，`/` 產生的 `__`
    會被當成兩個 `_` 一起跳脫，反而與原有的 `__` 撞在一起。

    先跳脫 `_` 這一步是必要的（#192）：只做 `/`→`__` 時 `/etc/a/b` 與 `/etc/a__b` 都編成
    `etc__a__b`，第二次納管會靜默覆蓋第一次的複本。跳脫之後 `_` 由 `_5f` 表示、`/` 由 `__`
    表示，兩者在任何位置都分得開，編碼於是單射：不同的目標一定編成不同的檔名。

    無 `_` 的路徑不受影響：`/etc/docker/daemon.json` 仍編成 `etc__docker__daemon.json`
    （與設計 §4.3 的範例一致）。編碼只需唯一、不需可還原——目標原路徑另存在 `FileEntry.target`。
    目標一律是絕對路徑（白名單要求），所以開頭的 `/` 一定在。
    """
    return target.lstrip("/").replace("_", "_5f").replace("/", "__")


def read_or_none(path: str) -> bytes | None:
    """`path` 的位元組；檔案不存在回 None。進版與解除納管在動手前拍現場用（回滾據以還原）。"""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def undo(steps: list[tuple[str, Callable[[], None]]]) -> list[str]:
    """逐步執行回滾的還原步驟，回傳還原不了的那幾步（「標籤（原因）」）。

    一步失敗不中途停——能還原多少算多少，沒還原的由呼叫端指名、大聲失敗（`…LeftBehind`）。
    改寫 repo 的編排（解除納管、產生 schema）共用這一份，回滾的寬容度才不會各自漂移。
    """
    leftover = []
    for label, step in steps:
        try:
            step()
        except (OSError, WriterError, subprocess.CalledProcessError) as error:
            leftover.append(f"{label}（{error}）")
    return leftover
