"""io/parsers — 以 round-trip parser 把改動寫回 `files/`（T6 的 io 半邊，#17）。

core/parse 負責 parse → set_value → dump（純邏輯、不碰檔案）；這裡只做兩件 I/O：讀 repo 裡
來源複本的原文、把新文字**原子**寫回同一路徑（T8 的 `replace_atomically`）。未觸動的部分逐位元組
保留，由 core/parse 的 round-trip 保證。

**不碰目標位置**——寫出到目標是進版（#19）的事；草稿改的是 repo 裡的來源複本。也不 stage、
不 record：那些屬於整批進版的原子操作（ADR-00000006、ADR-00000022），不在單一檔案的層次做。
"""

from __future__ import annotations

import os

from config_manager.core.parse import dump, parse, set_value
from config_manager.io.atomic import replace_atomically


# raw 的內容不解析、整塊搬（設計 §3.4：例如二進位或無法解析的自訂格式），所以不能要求它是
# UTF-8。寫出這條路徑以文字（`str`）傳遞內容；讓任意位元組無損地穿過 `str` 的做法是
# surrogateescape：解不開的位元組變成孤立的代理字元，編回去時還原成原本那個位元組。
LOSSLESS = "surrogateescape"


def as_text(content: bytes, fmt: str) -> str:
    """把檔案內容變成文字。`raw` 無損（任何位元組都過得去、編回去一模一樣）；其餘格式要解析，
    必須是合法的 UTF-8——不是就丟 `UnicodeDecodeError`，由呼叫端說明。"""
    return content.decode("utf-8", errors=LOSSLESS if fmt == "raw" else "strict")


def as_bytes(text: str) -> bytes:
    """`as_text` 的反向：寫出前把文字變回位元組。對合法的文字與 `raw` 的無損文字都正確。"""
    return text.encode("utf-8", errors=LOSSLESS)


def read_source(repo: str, source: str, fmt: str = "") -> str:
    """讀 repo 內來源複本的原文。API 層存草稿前拿它當 set_value 的底——讀檔是 I/O，不該在
    routes 裡做（CLAUDE.md 分層）。`fmt` 是那份 config 的 format：給 `raw` 時無損讀取（內容可能
    不是文字）；不給或給其他格式時必須是 UTF-8。"""
    with open(os.path.join(repo, source), "rb") as handle:
        return as_text(handle.read(), fmt)


def edit_source(repo: str, source: str, fmt: str, edits: dict[str, object]) -> str:
    """把 `edits`（路徑 → 新值）套到 repo 內的來源複本 `source` 並原子寫回，回傳寫出的新文字。

    `fmt` 來自清單檔的 format 欄位（不由副檔名推斷，#8）。任一路徑找不到會在寫回**之前**丟
    `UnknownPath`，檔案一個位元組都不會動。
    """
    absolute = os.path.join(repo, source)
    with open(absolute, encoding="utf-8") as handle:
        parsed = parse(handle.read(), fmt)
    for path, value in edits.items():
        set_value(parsed, path, value)
    text = dump(parsed)
    replace_atomically(absolute, text.encode("utf-8"))
    return text
