"""io/rules — 讀一份 config 的跨欄位規則，並備好跨 config 對照要用的值（#41）。

規則檔是 config-repo 裡的 `.rules/<uid>.toml`，由開發者編寫、隨 config 版控。檔名用 uid（名稱
可改、uid 不變），與 `.schemas/<uid>.json` 同一個慣例。沒有這個檔就是沒有規則。

規則的解讀在 `core/rules`（純邏輯）；這裡做 I/O 的那一半：讀檔，以及替「值必須出現在另一份
config 的某個欄位裡」的規則，去讀那一份 config 的來源複本、取出那個欄位的值。

**第 3 層不硬擋，所以這裡不因規則檔壞掉而丟例外**（那會讓存檔整個失敗，介面成了路障）；
但也不默默當成沒有規則——讀不出來的原因放進 `Rules.faults`，`check` 把它變成一個要填理由
才略得過的警告，留下紀錄（不變式 2）。這與 schema 不同：schema 是硬擋，壞了就大聲失敗。
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from config_manager.core.errors import RulesInvalid, SyntaxParse
from config_manager.core.parse import parse, values
from config_manager.core.rules import Rule, Rules, parse_rules, values_at
from config_manager.io.parsers import read_source
from config_manager.io.repo import load_list, read_or_none

RULES_DIR = ".rules"


def rules_relpath(uid: str) -> str:
    """`uid` 那份 config 的規則檔在 repo 內的相對路徑。"""
    return f"{RULES_DIR}/{uid}.toml"


def read_rules(repo: str, uid: str) -> Rules | None:
    """`uid` 那份 config 的規則（第 3 層驗證用）；沒有規則檔回 None。"""
    relative = rules_relpath(uid)
    raw = read_or_none(os.path.join(repo, relative))
    if raw is None:
        return None
    try:
        rules = parse_rules(raw.decode("utf-8"))
    except (UnicodeDecodeError, RulesInvalid) as error:
        return Rules((), {}, (f"{relative} 讀不出規則：{error}",))
    known: dict[tuple[str, str], Sequence[object]] = {}
    faults: list[str] = []
    for rule in rules:
        if rule.source is None or rule.source in known:
            continue
        found = _referenced(repo, rule)
        if isinstance(found, str):
            faults.append(found)
        else:
            known[rule.source] = found
    return Rules(rules, known, tuple(faults))


def _referenced(repo: str, rule: Rule) -> Sequence[object] | str:
    """規則對照的那個欄位裡的值；取不到時回說明（字串），由呼叫端放進 faults。"""
    uid, field = rule.source or ("", "")
    where = f"規則「{rule.id}」對照的 config「{uid}」"
    _, listing = load_list(repo)
    entry = next((item for item in listing.files if item.uid == uid), None)
    if entry is None:
        return f"{where}不在清單裡（寫的要是那一份的 uid）"
    if entry.format == "raw":
        return f"{where}的格式是 raw，沒有欄位可以對照"
    try:
        data = values(parse(read_source(repo, entry.source), entry.format))
    except (OSError, UnicodeDecodeError, SyntaxParse, RecursionError) as error:
        return f"{where}讀不到或解析不了：{error}"
    found: list[object] = []
    for value in values_at(data, field):
        # 指到一個清單時，對照的是它的元素。
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            found.extend(value)
        else:
            found.append(value)
    return found or f"{where}裡沒有「{field}」這個欄位，或它是空的"
