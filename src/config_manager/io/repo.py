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

from config_manager.io.atomic import replace_atomically
from config_manager.io.preflight import CONFIG_LIST_NAME

_SOURCES_DIR = "files"


def place_source(repo: str, hostname: str, target: str, content: bytes) -> str:
    """把 `content` 逐位元組放進 `files/<hostname>/<目標路徑編碼>`，回傳 repo 內的相對
    來源路徑（那正是要填進 `FileEntry.source` 的值）。

    目標路徑的 `/` 編碼成 `__`（D3）：一個機器上不同目錄的同名檔（`/opt/a/conf` 與
    `/etc/a/conf`）於是在 `files/<hostname>/` 底下不會互相覆蓋，而編碼後的名字仍看得出
    它原本從哪來。
    """
    relative = os.path.join(_SOURCES_DIR, hostname, _encode(target))
    absolute = os.path.join(repo, relative)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    replace_atomically(absolute, content)
    return relative


def write_config_list(repo: str, text: str) -> None:
    """把清單檔文字原子寫回 `repo/config-list.toml`。"""
    replace_atomically(os.path.join(repo, CONFIG_LIST_NAME), text.encode("utf-8"))


def _encode(target: str) -> str:
    """把絕對目標路徑編成單一檔名：去掉開頭的 `/`，其餘 `/` → `__`。

    去開頭的 `/` 讓 `/etc/docker/daemon.json` 編成 `etc__docker__daemon.json`（與設計
    §4.3 的範例一致），而不是多一個開頭的 `__`。目標一律是絕對路徑（白名單要求），
    所以開頭的 `/` 一定在。
    """
    return target.lstrip("/").replace("/", "__")
