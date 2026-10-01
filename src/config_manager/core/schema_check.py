"""core/schema_check — T3 第 2 層：依 schema 檢查解析後的資料（#39）。硬擋。

JSON Schema 描述的是**解析後的資料結構**，所以同一份 schema 驗 yaml／toml／json／ini 的等價
內容，結果一致（設計 §3.4）。檢查交給 `jsonschema`（不重寫、也不測它的驗證邏輯，T3「不測」）；
這裡做的是把它的每個錯誤轉成 `Problem`：**欄位路徑**（介面據此標示那一列）、**行號**（由
`core/locate` 從原文找）、中文的原因與**修正建議**。

**整數比 JSON Schema 的預設嚴**：標準把 `3.0` 當整數，這裡不當。ROS 區分 `1` 與 `1.0`，整數
參數寫成浮點會讓 node 啟動失敗——那正是這一層要擋的型別錯誤（T12 的同一條理由）。反過來
`number` 仍收整數（標準語意）。

核心層不做 I/O：原文與 schema 都由呼叫端傳進來；schema 檔的讀取與合法性在 `io/schema`。
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import SchemaError, ValidationError

from config_manager.core.errors import SchemaInvalid
from config_manager.core.locate import Parts, locator
from config_manager.core.parse import parse, values
from config_manager.core.problem import Problem

# 一個錯誤轉出來的（欄位路徑, 原因, 建議）；一個 jsonschema 錯誤可能對應好幾個欄位。
_Found = tuple[Parts, str, str]


def _is_integer(_checker: object, value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


_Validator = validators.extend(  # type: ignore[no-untyped-call]  # stub 沒標這個函式
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("integer", _is_integer),
)


def ensure_valid_schema(schema: object) -> None:
    """`schema` 不是一份合法的 JSON Schema 就丟 `SchemaInvalid`。

    壞掉的 schema 不能拿來驗——驗出「沒問題」會是假的（不變式 4：無法判定時落向安全）。
    """
    try:
        _Validator.check_schema(schema)
    except SchemaError as error:
        where = "／".join(str(part) for part in error.absolute_path) or "頂層"
        reason = f"{where}：{error.message}"
        raise SchemaInvalid(
            f"schema 本身不合法（{reason}）。"
            "下一步：請開發者依訊息修正 schema 檔，修好之前這份 config 不能修改",
            reason,
        ) from error


def check_schema(text: str, fmt: str, schema: Mapping[str, object]) -> list[Problem]:
    """依 `schema` 檢查 `text` 解析後的資料，回問題清單。`text` 必須已經解析得過（第 1 層）。"""
    line_of = locator(text, fmt)
    problems: list[Problem] = []
    for error in _Validator(schema).iter_errors(values(parse(text, fmt))):
        broken = _Broken.of(error)
        for parts, message, suggestion in _HANDLERS.get(broken.keyword, _other)(broken):
            problems.append(Problem(line_of(parts), message, suggestion, path=path_text(parts)))
    # `required` 每缺一個欄位回報一次、每次都列得出全部缺的——去掉重複，順序不變。
    return list(dict.fromkeys(problems))


# 介面用得上的 schema 關鍵字：範圍、步進、列舉選項、說明（#40）。
_HINT_KEYS = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "enum",
    "description",
)


def field_hints(schema: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """schema 裡每個欄位給介面的提示（範圍、步進、列舉選項、說明），沒有提示的欄位不列。

    鍵是欄位路徑，與 `infer_types` 同一套文法（`.` 相接、`[]` 標清單元素、key 內的字面點跳脫成
    `\\.`），欄位表才對得到那一列。這只是**提示**：讓介面在輸入當下就說得出問題；保證在
    `check_schema`，介面不擋的後端照擋（設計原則 N-3）。只跟著 `properties` 與 `items` 往下走。
    """
    found: dict[str, dict[str, object]] = {}
    _collect_hints(schema, "", found)
    return found


def _collect_hints(node: object, path: str, found: dict[str, dict[str, object]]) -> None:
    if not isinstance(node, Mapping):
        return
    hints = {key: node[key] for key in _HINT_KEYS if key in node}
    if path and hints:
        found[path] = hints
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        for key, child in properties.items():
            escaped = str(key).replace(".", "\\.")
            _collect_hints(child, f"{path}.{escaped}" if path else escaped, found)
    if "items" in node:
        _collect_hints(node["items"], f"{path}[]", found)


@dataclass(frozen=True)
class _Broken:
    """一條沒過的規則，從 jsonschema 的錯誤取出這裡用得到的欄位。

    在邊界上轉一次：jsonschema 的錯誤物件欄位型別很寬（多數可能是「未設定」），轉成這個之後
    下面各個說法只面對確定存在的值。
    """

    keyword: str  # 沒過的是哪一種規則（type、required、minimum…）
    parts: Parts  # 出問題的值在資料裡的路徑
    rule: Any  # schema 裡那條規則的設定值（要求的型別、必填清單、界限…）
    value: Any  # 資料裡實際的值
    around: Mapping[str, Any]  # 那條規則所在的那段 schema
    said: str  # 驗證器自己的說法（原文）

    @classmethod
    def of(cls, error: ValidationError) -> _Broken:
        around = error.schema if isinstance(error.schema, Mapping) else {}
        return cls(
            keyword=str(error.validator),
            parts=tuple(error.absolute_path),
            rule=cast(Any, error.validator_value),
            value=cast(Any, error.instance),
            around=around,
            said=error.message,
        )


def path_text(parts: Sequence[str | int]) -> str:
    """欄位路徑的文字寫法，與欄位表的參數列、`set_value` 同一套文法。頂層是空字串。"""
    text = ""
    for part in parts:
        if isinstance(part, int):
            text += f"[{part}]"
        else:
            text += ("." if text else "") + str(part).replace(".", "\\.")
    return text


def _named(parts: Parts) -> str:
    return f"「{path_text(parts)}」" if parts else "頂層"


_TYPE_TEXT = {
    "integer": "整數",
    "number": "數字",
    "string": "字串",
    "boolean": "布林",
    "array": "清單",
    "object": "物件",
    "null": "空值",
}
_TYPE_HINT = {
    "integer": "改成整數，不帶小數點（例如 3）",
    "number": "改成數字（例如 0.5）",
    "string": "改成字串；純數字或 true／false 要加引號",
    "boolean": "改成 true 或 false",
}
# 比對順序有意義：bool 是 int 的子類別，要排在前面（同 core/inference）。
_ACTUAL: tuple[tuple[type, str], ...] = (
    (bool, "布林"),
    (int, "整數"),
    (float, "浮點數"),
    (str, "字串"),
    (Mapping, "物件"),
    (Sequence, "清單"),
)


def _actual(value: object) -> str:
    if value is None:
        return "空值"
    return next((name for kind, name in _ACTUAL if isinstance(value, kind)), "其他型別")


def _shown(value: object) -> str:
    """訊息裡引用的值：純量照寫，容器不展開（訊息會被它淹沒）。"""
    if isinstance(value, (Mapping, list, tuple)):
        return ""
    return f"（{value}）"


def _type(broken: _Broken) -> list[_Found]:
    kinds = [broken.rule] if isinstance(broken.rule, str) else list(broken.rule)
    names = "或".join(_TYPE_TEXT.get(kind, kind) for kind in kinds)
    hint = _TYPE_HINT.get(kinds[0], f"改成{names}") if len(kinds) == 1 else f"改成{names}"
    actual = f"{_actual(broken.value)}{_shown(broken.value)}"
    return [(broken.parts, f"{_named(broken.parts)}應為{names}，現在是{actual}", hint)]


def _required(broken: _Broken) -> list[_Found]:
    return [
        (
            (*broken.parts, key),
            f"缺少必填欄位{_named((*broken.parts, key))}",
            f"在{_named(broken.parts)}底下補上「{key}」",
        )
        for key in broken.rule
        if key not in broken.value
    ]


def _additional(broken: _Broken) -> list[_Found]:
    known = [str(name) for name in broken.around.get("properties", {})]
    patterns = list(broken.around.get("patternProperties", {}))
    found: list[_Found] = []
    for key in broken.value:
        if str(key) in known or any(re.search(pattern, str(key)) for pattern in patterns):
            continue
        close = difflib.get_close_matches(str(key), known, n=1)
        guess = f"是不是「{close[0]}」？否則" if close else ""
        found.append((
            (*broken.parts, key),
            f"{_named((*broken.parts, key))}不在 schema 允許的欄位裡",
            f"{guess}移除它，或請開發者把它加進 schema",
        ))
    return found


_LIMITS = {
    "minimum": ("小於下限", "不小於"),
    "maximum": ("大於上限", "不大於"),
    "exclusiveMinimum": ("沒有大於", "大於"),
    "exclusiveMaximum": ("沒有小於", "小於"),
}


def _limit(broken: _Broken) -> list[_Found]:
    wrong, right = _LIMITS[broken.keyword]
    return [(
        broken.parts,
        f"{_named(broken.parts)}的值 {broken.value} 超出範圍：{wrong} {broken.rule}",
        f"改成{right} {broken.rule} 的值",
    )]


def _enum(broken: _Broken) -> list[_Found]:
    allowed = "、".join(str(value) for value in broken.rule)
    return [(
        broken.parts,
        f"{_named(broken.parts)}的值 {broken.value} 不在允許的值裡",
        f"改成其中之一：{allowed}",
    )]


def _other(broken: _Broken) -> list[_Found]:
    """沒有專屬說法的規則：照樣回報、指名是哪一條規則，附上驗證器的原文。不靜默放行。"""
    return [(
        broken.parts,
        f"{_named(broken.parts)}不符合 schema 的 {broken.keyword} 規則（{broken.said}）",
        "依 schema 對這個欄位的規定修正；規定不合理就請開發者調整 schema",
    )]


_HANDLERS: dict[str, Callable[[_Broken], list[_Found]]] = {
    "type": _type,
    "required": _required,
    "additionalProperties": _additional,
    "enum": _enum,
    **{keyword: _limit for keyword in _LIMITS},
}
