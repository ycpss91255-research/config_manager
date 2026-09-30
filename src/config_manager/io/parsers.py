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
