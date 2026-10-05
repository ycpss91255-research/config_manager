"""io/lintrc — 讀 config-repo 的 `.lintrc.toml`（第 1 層規則的設定檔，#43）。

沒有這個檔就是預設值（§6.3 的全套）——既有的 repo 一個行為都不變。有檔但讀不出來（不是
UTF-8、不是合法 TOML、設定不合法）→ `LintrcUnparsable`：它管的是硬擋的第 1 層，壞了不能當成
預設值放行（不變式 4）。它是 `PreflightError` 的一種：啟動時 preflight 先攔、服務起來後 API 回
結構化的 500，與清單檔、白名單設定檔同一套處置。
"""

from __future__ import annotations

import os

from config_manager.core.errors import LintrcInvalid
from config_manager.core.lintrc import DEFAULT, Lintrc, parse_lintrc
from config_manager.io.errors import LintrcUnparsable

LINTRC_NAME = ".lintrc.toml"


def read_lintrc(repo: str) -> Lintrc:
    """repo 的第 1 層規則設定；沒有 `.lintrc.toml` 回預設值。"""
    path = os.path.join(repo, LINTRC_NAME)
    # 不經 io/repo 讀（preflight 會匯入這裡，io/repo 又匯入 preflight，繞回來就成環）。
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except FileNotFoundError:
        return DEFAULT
    try:
        return parse_lintrc(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise LintrcUnparsable(
            f"規則設定檔不是 UTF-8：{path}（{error}）。下一步：以 UTF-8 重新存檔", file=path
        ) from error
    except LintrcInvalid as error:
        raise LintrcUnparsable(
            f"規則設定檔無法使用：{path}——{error}。修好之前第 1 層驗證無法進行。"
            "下一步：依上面指出的位置修正該檔，或先把它移走以回到預設值",
            file=path,
        ) from error
