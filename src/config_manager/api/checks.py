"""api/checks — 一份 config 此刻要過的檢查（schema 與規則），以及變更紀錄裡的略過標記。

儲存草稿、進版、偏離處置都要同一組檢查：第 2 層的 schema（#39）與第 3 層的規則（#41）。集中
在這裡讀，三個呼叫端就不會各讀各的、漏掉其中一層。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from config_manager.core.drafts import Checks
from config_manager.core.models import FileEntry
from config_manager.io.rules import read_rules
from config_manager.io.schema import read_schema

# 進版把略過的規則與理由寫進紀錄內文，一行一條（`core/drafts.promote`）。
_OVERRIDE = re.compile(r"^override\((?P<rule>[^)]+)\): (?P<reason>.*)$", re.MULTILINE)


def checks_for(
    repo: str, entry: FileEntry, reasons: Mapping[str, str] | None = None
) -> Checks:
    """`entry` 那份 config 此刻的檢查；`reasons` 是這次請求帶來的、略過規則的理由（#42）。

    schema 讀不出來會丟 `SchemaUnreadable`（硬擋的那一層壞了不能當成沒有，#39）；規則讀不出來
    不丟——變成一個要填理由才略得過的警告（第 3 層不硬擋，#41）。
    """
    return Checks(
        schema=read_schema(repo, entry),
        rules=read_rules(repo, entry.uid),
        reasons=reasons or {},
    )


def overrides_in(body: str) -> list[dict[str, str]]:
    """一筆變更紀錄的內文裡略過了哪些規則、理由是什麼（#42）。沒有就是空清單。

    略過的紀錄本身有價值：同一條規則被反覆略過，代表規則需要修正（設計 §6.2）。
    """
    return [match.groupdict() for match in _OVERRIDE.finditer(body)]
