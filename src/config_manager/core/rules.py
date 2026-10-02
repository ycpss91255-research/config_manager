"""core/rules — T3 第 3 層：跨欄位規則（#41）。警告，可填理由略過（#42），不硬擋。

型別正確但數值不合理的 config 比型別錯誤的危險：型別錯誤讓 node 啟動失敗、立刻暴露；
`inflation_radius` 設太小要到撞車才發現（設計 §6.2）。這一層檢查欄位之間合不合理。它只
**警告**——現場異常時若因規則誤判改不了設定，介面就成了路障。

規則的寫法（#41 動工前定案——選簡單的、不容易寫錯的）：

    [[rules]]
    id = "min-below-max"          # 代號；略過時記進變更紀錄
    left = "min_vel"              # 欄位路徑
    op = "<"                      # 六種之一：< <= > >= == !=
    right = "max_vel"             # 另一個欄位……
    # value = 5                   # ……或一個固定值（二選一）
    message = "最小速度要小於最大速度"   # 選填：為什麼有這條規則

    [[rules]]
    id = "frame-is-defined"
    left = "robot_frame"
    in = { config = "<另一份的 uid>", field = "frames" }   # 值必須出現在那份 config 的那個欄位裡

不支援算式：組合越自由，寫錯的方式越多。**規則檔本身寫錯要被指出來**（`parse_rules` 丟
`RulesInvalid`，指名第幾條、哪裡錯），不默默當成沒有那條規則。規則用到的欄位不在內容裡、
兩個值比不了，也都回報成警告而不是當成通過（不變式 2）。

核心層不做 I/O：規則檔的文字、跨 config 對照要用的值（`Rules.known`）都由呼叫端傳進來。
"""

from __future__ import annotations

import operator
import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from config_manager.core.errors import RulesInvalid
from config_manager.core.locate import Parts, locator
from config_manager.core.parse import parse, values
from config_manager.core.problem import WARNING, Problem

# 規則檔自己有問題時，那個警告用的代號（略過它一樣要填理由、一樣留紀錄）。
FILE_RULE = "rules-file"

_OPS: dict[str, tuple[Callable[[Any, Any], Any], str]] = {
    "<": (operator.lt, "小於"),
    "<=": (operator.le, "小於或等於"),
    ">": (operator.gt, "大於"),
    ">=": (operator.ge, "大於或等於"),
    "==": (operator.eq, "等於"),
    "!=": (operator.ne, "不等於"),
}
_EQUALITY = frozenset({"==", "!="})
_KEYS = frozenset({"id", "left", "op", "right", "value", "in", "message"})
# 代號會寫進變更紀錄的 `override(<代號>)`，只收不會把那一行弄壞的字。
_ID = re.compile(r"[A-Za-z0-9._-]+")
_DOT = re.compile(r"(?<!\\)\.")
_INDEX = re.compile(r"\[(\d*)\]")
_FIX_FILE = "請開發者修正規則檔"


@dataclass(frozen=True)
class Rule:
    """一條規則。比較：`op` 與 `right`（另一個欄位）或 `value`（固定值）；對照：`source`
    是（另一份 config 的 uid, 那份的欄位），此時 `op` 是 None。"""

    id: str
    left: str
    op: str | None
    right: str | None
    value: object
    source: tuple[str, str] | None
    message: str


@dataclass(frozen=True)
class Rules:
    """`check` 第 3 層的輸入：規則、跨 config 對照要用的值、規則用不了的原因。

    `known` 是（config uid, 欄位）→ 那個欄位裡的值，由 I/O 層讀另一份 config 取得。`faults`
    是規則檔壞了、或對照的 config 讀不到時的說明——跨欄位檢查沒跑完這件事本身要讓人看到。
    """

    rules: tuple[Rule, ...]
    known: Mapping[tuple[str, str], Sequence[object]]
    faults: tuple[str, ...] = ()


def parse_rules(text: str) -> tuple[Rule, ...]:
    """把規則檔的文字讀成規則。寫錯的地方丟 `RulesInvalid`，指名第幾條、哪裡錯。"""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise RulesInvalid(
            f"規則檔不是合法的 TOML：{error}。下一步：依訊息指出的位置修正語法"
        ) from error
    stray = sorted(set(document) - {"rules"})
    items = document.get("rules", [])
    if stray or not isinstance(items, list):
        raise RulesInvalid(
            f"規則檔頂層只能有 [[rules]]，現在有：{'、'.join(stray) or 'rules 不是清單'}。"
            "下一步：每條規則都寫成一個 [[rules]] 區塊"
        )
    rules = tuple(_rule(number, item) for number, item in enumerate(items, start=1))
    seen = [rule.id for rule in rules]
    repeated = sorted({name for name in seen if seen.count(name) > 1})
    if repeated:
        raise RulesInvalid(
            f"規則的 id 重複了：{'、'.join(repeated)}。略過規則的紀錄以 id 指名是哪一條，"
            "重複就分不出來。下一步：給每條規則一個不同的 id"
        )
    return rules


def _rule(number: int, item: Mapping[str, object]) -> Rule:
    name = item.get("id")
    where = f"第 {number} 條" + (f"（{name}）" if isinstance(name, str) else "")
    unknown = sorted(set(item) - _KEYS)
    if unknown:
        raise RulesInvalid(
            f"{where}有不認得的鍵：{'、'.join(unknown)}。"
            f"下一步：只用 {'／'.join(sorted(_KEYS))}，檢查是不是打錯字"
        )
    if not isinstance(name, str) or not _ID.fullmatch(name):
        raise RulesInvalid(
            f"{where}的 id「{name}」不能用：必填，只收字母、數字與 . _ -。"
            "下一步：給這條規則一個像 min-below-max 的代號"
        )
    left = item.get("left")
    if not isinstance(left, str) or not left:
        raise RulesInvalid(f"{where}沒有 left（要檢查的欄位）。下一步：補上 left = \"欄位路徑\"")
    message = str(item.get("message", ""))
    if "in" in item:
        return Rule(name, left, None, None, None, _source(where, item), message)
    return _comparison(where, item, Rule(name, left, None, None, None, None, message))


def _source(where: str, item: Mapping[str, object]) -> tuple[str, str]:
    target = item["in"]
    extra = sorted(set(item) & {"op", "right", "value"})
    if (
        extra
        or not isinstance(target, Mapping)
        or not isinstance(target.get("config"), str)
        or not isinstance(target.get("field"), str)
        or set(target) - {"config", "field"}
    ):
        raise RulesInvalid(
            f"{where}的 in 寫法不對：要寫成 in = {{ config = \"另一份的 uid\", "
            "field = \"欄位\" }，而且不能同時寫 op／right／value。"
            "下一步：補上 config 與 field，拿掉多的鍵"
        )
    return str(target["config"]), str(target["field"])


def _comparison(where: str, item: Mapping[str, object], base: Rule) -> Rule:
    op = item.get("op")
    if not isinstance(op, str) or op not in _OPS:
        raise RulesInvalid(
            f"{where}的 op「{op}」不是允許的比較方式。下一步：改用 {' '.join(_OPS)} 其中之一"
        )
    if ("right" in item) == ("value" in item):
        raise RulesInvalid(
            f"{where}的比較對象要寫 right（另一個欄位）或 value（固定值），而且只能擇一。"
            "下一步：留下其中一個"
        )
    right = item.get("right")
    if "right" in item and (not isinstance(right, str) or not right):
        raise RulesInvalid(
            f"{where}的 right 要是欄位路徑（文字）。下一步：改成 right = \"欄位路徑\""
        )
    return Rule(base.id, base.left, op, right if isinstance(right, str) else None,
                item.get("value"), None, base.message)


def check_rules(text: str, fmt: str, rules: Rules) -> list[Problem]:
    """依 `rules` 檢查 `text` 解析後的資料，回警告清單。`text` 必須已經解析得過（第 1 層）。"""
    if fmt == "raw":
        return []
    data = values(parse(text, fmt))
    line_of = locator(text, fmt)
    problems = [
        Problem(
            None,
            f"規則檔有問題，跨欄位檢查不完整：{fault}",
            f"{_FIX_FILE}；要照樣儲存就填寫理由",
            WARNING,
            rule=FILE_RULE,
        )
        for fault in rules.faults
    ]
    for rule in rules.rules:
        found = _judge(rule, data, rules.known)
        if found is not None:
            parts = _parts(rule.left)
            problems.append(
                Problem(line_of(parts), found[0], found[1], WARNING, path=rule.left, rule=rule.id)
            )
    return problems


def _judge(
    rule: Rule, data: object, known: Mapping[tuple[str, str], Sequence[object]]
) -> tuple[str, str] | None:
    """一條規則對這份資料的結論：（原因, 建議）；沒有問題回 None。"""
    left = values_at(data, rule.left)
    if len(left) != 1:
        return _absent(rule, rule.left)
    if rule.source is not None:
        return _outside(rule, left[0], known)
    if rule.right is None:
        target, shown = rule.value, f" {rule.value}"
    else:
        right = values_at(data, rule.right)
        if len(right) != 1:
            return _absent(rule, rule.right)
        target, shown = right[0], f"「{rule.right}」（{right[0]}）"
    compare, wording = _OPS[str(rule.op)]
    because = f"：{rule.message}" if rule.message else ""
    if rule.op not in _EQUALITY and not _orderable(left[0], target):
        return (
            f"規則「{rule.id}」：「{rule.left}」（{left[0]}）與{shown.strip()}比不了（型別不同）",
            f"{_FIX_FILE}，或把值改成同一種型別；要照樣儲存就填寫理由",
        )
    if compare(left[0], target):
        return None
    return (
        f"「{rule.left}」（{left[0]}）應{wording}{shown}，現在不成立{because}",
        f"調整其中一個值；確定要這樣設，就填寫理由後儲存（規則 {rule.id}）",
    )


def _absent(rule: Rule, path: str) -> tuple[str, str]:
    return (
        f"規則「{rule.id}」用到的欄位「{path}」不在這份 config 裡（或不是單一個值），沒辦法檢查",
        f"{_FIX_FILE}，或補上這個欄位；要照樣儲存就填寫理由",
    )


def _outside(
    rule: Rule, value: object, known: Mapping[tuple[str, str], Sequence[object]]
) -> tuple[str, str] | None:
    """跨 config 對照：`value` 要出現在另一份 config 的那個欄位裡。那個欄位取不到值時這裡
    不判（I/O 層已把原因放進 `Rules.faults`）。"""
    if rule.source is None or rule.source not in known or value in known[rule.source]:
        return None
    uid, field = rule.source
    because = f"：{rule.message}" if rule.message else ""
    allowed = "、".join(str(item) for item in list(known[rule.source])[:8]) or "（那個欄位是空的）"
    return (
        f"「{rule.left}」的值 {value} 不在 config {uid} 的「{field}」裡{because}",
        f"改成其中之一：{allowed}；或先修正被對照的那份；確定要這樣設，就填寫理由後儲存"
        f"（規則 {rule.id}）",
    )


def _orderable(left: object, right: object) -> bool:
    """兩個值能不能比大小：都是數字（布林不算），或都是文字。"""
    numbers = (int, float)
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    both_numbers = isinstance(left, numbers) and isinstance(right, numbers)
    return both_numbers or (isinstance(left, str) and isinstance(right, str))


def values_at(data: object, path: str) -> list[object]:
    """`path` 在 `data` 裡指到的值。`a.b`、`a[0]` 指到一個；`a[]` 展開清單的每個元素
    （`links[].name` 取每個元素的 name）。找不到回空清單。"""
    nodes: list[object] = [data]
    for piece in _DOT.split(path):
        key = _INDEX.sub("", piece).replace("\\.", ".")
        if key:
            nodes = [node[key] for node in nodes if isinstance(node, Mapping) and key in node]
        for index in _INDEX.findall(piece):
            nodes = _indexed(nodes, index)
    return nodes


def _indexed(nodes: list[object], index: str) -> list[object]:
    """`[n]` 取每個清單的第 n 個；`[]` 展開每個清單的全部元素。"""
    found: list[object] = []
    for node in nodes:
        if not isinstance(node, Sequence) or isinstance(node, (str, bytes)):
            continue
        if index == "":
            found.extend(node)
        elif int(index) < len(node):
            found.append(node[int(index)])
    return found


def _parts(path: str) -> Parts:
    """欄位路徑拆成 `core/locate` 用的片段（鍵與清單索引），給行號定位。"""
    parts: list[str | int] = []
    for piece in _DOT.split(path):
        key = _INDEX.sub("", piece).replace("\\.", ".")
        if key:
            parts.append(key)
        parts.extend(int(index) for index in _INDEX.findall(piece) if index)
    return tuple(parts)
