"""core/parse — T6 格式解析與原樣寫回.

`parse(text, fmt)` 把一份被管理的 config 解析成 `Parsed`；`dump(parsed)` 把它寫回
文字。核心保證：**未改動任何值時，dump 的輸出與 parse 的輸入逐位元組相同**
（T6、ADR-00000029）。

解析器由 `format` 欄位決定，**不由副檔名推斷**——`.yml`、`.param` 等變體不可靠（#8）。
yaml→ruamel.yaml、toml→tomlkit 用成熟的 lossless round-trip 函式庫（ADR-00000029），
document 即其 round-trip 物件。**json 與 ini 沒有保位元組的編輯器**（json 標準庫正規化；
configobj 補尾換行、去引號、壓 CRLF、吃尾隨空白，#217），故兩者**存原文**達到逐位元組往返，
只拿標準庫／configobj 驗語法與取值（`values()`）。改動一個值走 `set_value`（#17）：yaml／toml
直接改 round-trip 物件；json 偵測縮排後重新序列化；ini 原地文字替換（#17 的 D2）。
`raw` 不解析，原文通過。需要走訪值（型別推斷）時一律經 `values()`。

核心層不做 I/O：`text` 由呼叫端讀好傳進來（CLAUDE.md 分層）。
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

import tomlkit
from configobj import ConfigObj, ConfigObjError
from tomlkit import TOMLDocument
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Item
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from config_manager.core.errors import SyntaxParse, UnknownPath, UnsupportedFormat


@dataclass
class Parsed:
    """T6 的（資料，原樣資訊）。

    `document` 是格式專屬的 round-trip 物件（或 `raw`／json／ini 的原文字串）：dump 靠它重建
    原文，而型別判定（#9）、歧義值偵測（#10）也在它上面讀值與行號。`fmt` 記住是哪
    一種格式，dump 才知道用哪一支序列化器。

    不設 frozen：`set_value` 就地編輯它——yaml／toml 改的是可變的 round-trip 物件，json／ini
    存的是原文字串、改值後要換成新文字（#17）。

    `yaml_indent` 是 parse 時從原文量出的清單縮排風格 `(sequence, offset)`：ruamel 不記住每個
    檔案的清單縮排、dump 一律用引擎設定，所以要量出來、dump 時套回，改值後巢狀清單的縮排才不會
    被重排（#17）。沒有清單或非 yaml 為 None（用引擎預設）。
    """

    fmt: str
    document: object
    yaml_indent: tuple[int, int] | None = None


def parse(text: str, fmt: str) -> Parsed:
    match fmt:
        case "raw":
            return Parsed(fmt="raw", document=text)
        case "yaml":
            return Parsed(
                fmt="yaml", document=_parse_yaml(text), yaml_indent=_yaml_indent_style(text)
            )
        case "toml":
            return Parsed(fmt="toml", document=_parse_toml(text))
        case "json":
            return Parsed(fmt="json", document=_parse_json(text))
        case "ini":
            return Parsed(fmt="ini", document=_parse_ini(text))
        case _:
            raise UnsupportedFormat(_unsupported(fmt))


def dump(parsed: Parsed) -> str:
    match parsed.fmt:
        case "raw":
            return _as_text(parsed.document)
        case "yaml":
            return _dump_yaml(parsed.document, parsed.yaml_indent)
        case "toml":
            return _dump_toml(parsed.document)
        case "json" | "ini":
            # json／ini 都存原文（configobj／json 不保位元組），dump 回原文即逐位元組相同。
            return _as_text(parsed.document)
        case _:
            raise UnsupportedFormat(_unsupported(parsed.fmt))


# ── yaml：ruamel.yaml 的 round-trip 模式，明確 1.2 以避開 1.1 的布林值陷阱 ──


def _yaml(indent: tuple[int, int] | None = None) -> YAML:
    # typ='rt' 就是「明確指定 YAML 1.2」的方式（#8）：它保留註解、引號樣式、鍵順序、
    # 結構，且把 `yes`／`no`／`on` 這些 1.1 會當成布林的裸字保持為字串——1.1 的布林陷阱
    # 因此不成立。preserve_quotes 保住引號樣式。不設 `version`：一設它就會在輸出頂端印出
    # `%YAML 1.2` 指令，破壞逐位元組往返，而 rt 本身已經給了 1.2 的取值語意。
    engine = YAML(typ="rt")
    engine.preserve_quotes = True
    if indent is not None:
        # dump 用：套回 parse 時從原文量出的清單縮排風格（見 _yaml_indent_style）。
        sequence, offset = indent
        engine.indent(mapping=2, sequence=sequence, offset=offset)
    return engine


def _parse_yaml(text: str) -> object:
    try:
        return _yaml().load(text)
    except YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        loc = f"（第 {line} 行）" if line is not None else ""
        raise SyntaxParse(
            f"yaml 語法錯誤，解析不下去{loc}。下一步：修正該行的語法後重試",
            line,
        ) from exc


def _dump_yaml(document: object, indent: tuple[int, int] | None = None) -> str:
    buffer = io.StringIO()
    _yaml(indent).dump(document, buffer)
    return buffer.getvalue()


# 清單縮排風格的偵測：找第一個緊接在「鍵:」之後的 `- ` 項目，量破折號相對該鍵的縮排。
_YAML_KEY_ONLY = re.compile(r"^(\s*)[^\s#-][^#]*:\s*(#.*)?$")
_YAML_DASH = re.compile(r"^(\s*)- ")


def _yaml_indent_style(text: str) -> tuple[int, int] | None:
    """從原文量出 ruamel 的 `(sequence, offset)`；沒有清單就回 None（引擎預設）。

    ruamel 不記住每個檔案的清單縮排，dump 一律用引擎設定——改值後巢狀清單會被重排成預設
    形狀（`    - 1` 變 `  - 1`）。先量再套，未改動的清單才逐位元組不動。只看第一組，一份
    config 通常只用一種風格。
    """
    key_indent: int | None = None
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        dash = _YAML_DASH.match(line)
        if dash is not None and key_indent is not None:
            offset = len(dash.group(1)) - key_indent
            return (offset + 2, offset) if offset >= 0 else None
        key = _YAML_KEY_ONLY.match(line)
        key_indent = len(key.group(1)) if key is not None else None
    return None


# ── toml：tomlkit 已在 T1 通過逐位元組往返（ADR-00000029）──


def _parse_toml(text: str) -> object:
    try:
        return tomlkit.parse(text)
    except TOMLKitError as exc:
        line = getattr(exc, "line", None)
        loc = f"（第 {line} 行）" if line is not None else ""
        raise SyntaxParse(
            f"toml 語法錯誤，解析不下去{loc}。下一步：修正該行的語法後重試", line
        ) from exc


def _dump_toml(document: object) -> str:
    return tomlkit.dumps(cast(TOMLDocument, document))


# ── json：沒有成熟的 lossless 編輯函式庫，先存原文保逐位元組往返 ──
# 未改動時 dump 回原文即逐位元組相同；編輯後的往返（保留縮排與鍵順序）留待 v0.3.0
# 參數編輯落地時處理——那時才會有人去動 json 的值。


def _parse_json(text: str) -> str:
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise SyntaxParse(
            f"json 語法錯誤，解析不下去（第 {exc.lineno} 行）。下一步：修正該行的語法後重試",
            exc.lineno,
        ) from exc
    return text


# ── ini：configobj，ADR-00000029 指名的 INI lossless 選項 ──
# 讀寫都走記憶體內的 bytes 緩衝，不碰檔案系統（核心層不做 I/O）。


def _parse_ini(text: str) -> str:
    """驗語法後**存原文**（比照 json）。

    configobj 是設定讀寫器、不是保位元組的編輯器：它 dump 時會補尾換行、去引號、把 CRLF
    壓成 LF、吃掉尾隨空白（#217）——值語意有保、位元組不同，違反「未改動時逐位元組相同」。
    所以只拿它當語法檢查器，document 存原文；未改動時 dump 回原文即逐位元組相同。改動一個值
    後的往返（configobj 序列化）留待 v0.3.0 參數編輯落地——那時才會有人去動 ini 的值（同 json）。
    """
    _ini_document(text)  # 只為驗語法；失敗丟具名 SyntaxParse
    return text


def _ini_document(text: str) -> object:
    """把 ini 原文解析成 configobj 的結構（供驗語法與取值，非往返）。"""
    try:
        return ConfigObj(io.BytesIO(text.encode("utf-8")), encoding="utf-8")
    except ConfigObjError as exc:
        line = getattr(exc, "line_number", None)
        loc = f"（第 {line} 行）" if line else ""
        raise SyntaxParse(
            f"ini 語法錯誤，解析不下去{loc}。下一步：修正該行的語法後重試", line
        ) from exc


def values(parsed: Parsed) -> object:
    """回傳可供型別推斷／走訪的值結構（dict／list／純量）。

    yaml／toml 的 `document` 本身就是可走訪的 round-trip 結構；json／ini 的 `document` 是
    **原文字串**（為逐位元組往返而存，#217／ADR-00000029），需要值時在這裡再 parse 一次。
    raw 不解析，`document` 是原文字串、沒有結構化值（呼叫端不對 raw 做型別推斷）。
    """
    match parsed.fmt:
        case "json":
            return json.loads(cast(str, parsed.document))
        case "ini":
            return _ini_document(cast(str, parsed.document))
        case _:
            return parsed.document


# ── 改動一個值後原樣寫回（T6 第二列，#17）──────────────────────────────────
# 路徑文法與 core/inference 的欄位路徑一致：`.` 相接、鍵內字面的點寫成 `\.`；清單元素以
# `[索引]` 指定（inference 用無索引的 `[]` 表「元素型別」，編輯要指到哪一個）。

_SEGMENT = re.compile(r"^([^\[]*)((?:\[\d+\])*)$")
_INDEX = re.compile(r"\[(\d+)\]")
_INI_HEADER = re.compile(r"^\s*\[([^\]]+)\]")
_INI_MAX_DEPTH = 2  # ini 的路徑最多「區段.鍵」兩段


def set_value(parsed: Parsed, path: str, value: object) -> None:
    """把 `path` 指到的值改成 `value`，其餘原封不動；路徑找不到丟 `UnknownPath`。

    yaml／toml 直接改 round-trip 物件（ruamel／tomlkit 保註解、引號、順序；toml 賦值會重建
    項目，另把舊項的 trivia 搬到新項）。json 偵測原檔縮排後重新序列化（無註解可失、保 key
    順序；空白與跳脫可能正規化）；ini 原地文字替換（configobj 寫回會毀格式，#217）——#17 的 D2。
    改 double 一定寫出小數點：Python float 序列化本就帶 `.0`（ADR-00000013）。
    """
    if parsed.fmt == "raw":
        raise UnsupportedFormat(
            f"raw 格式不解析、沒有結構，改不了路徑「{path}」指的值。"
            "下一步：以 yaml／json／toml／ini 之一納管，或整檔替換"
        )
    segments = _segments(path)
    match parsed.fmt:
        case "json":
            parsed.document = _json_with(cast(str, parsed.document), segments, value, path)
        case "ini":
            parsed.document = _ini_with(cast(str, parsed.document), segments, value, path)
        case "toml":
            _assign(parsed.document, segments, value, path, _toml_item)
        case _:
            _assign(parsed.document, segments, value, path, lambda new, _old: new)


def _segments(path: str) -> list[str | int]:
    r"""`a.b\.c[2].d` → ["a", "b.c", 2, "d"]。"""
    segments: list[str | int] = []
    for piece in re.split(r"(?<!\\)\.", path):
        match = _SEGMENT.match(piece)
        if match is None or (not match.group(1) and not match.group(2)):
            raise UnknownPath(
                f"路徑「{path}」的「{piece}」這一段寫法不合（鍵名後只能接 [索引]）。"
                "下一步：依「鍵.子鍵[索引]」的形式改寫，鍵內字面的點寫成 \\."
            )
        if match.group(1):
            segments.append(match.group(1).replace("\\.", "."))
        segments.extend(int(index) for index in _INDEX.findall(match.group(2)))
    return segments


def _child(node: Any, segment: str | int, path: str) -> Any:
    try:
        return node[segment]
    except (KeyError, IndexError, TypeError) as error:
        raise UnknownPath(
            f"路徑「{path}」在「{segment}」這一段找不到對應的值。"
            "下一步：對照這份 config 的實際結構修正路徑，或先確認該鍵存在"
        ) from error


def _assign(
    root: object,
    segments: list[str | int],
    value: object,
    path: str,
    make: Callable[[object, object], object],
) -> None:
    node: Any = root
    for segment in segments[:-1]:
        node = _child(node, segment, path)
    last = segments[-1]
    old = _child(node, last, path)
    node[last] = make(_keep_double(value, old), old)


def _keep_double(value: object, old: object) -> object:
    """舊值是 double、新值是整數 → 轉成 float，寫出才帶小數點（ADR-00000013）。

    JSON 分不出 `5` 與 `5.0`（介面送來的 5.0 到這裡已是 int）；型別由原值決定、不由新值的
    寫法決定，不然改一次 double 就把它降成 int——ROS 2 最常見的型別錯誤正是這樣造出來的。
    bool 是 int 的子類別，明確排除。
    """
    if isinstance(old, float) and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def _toml_item(value: object, old: object) -> object:
    """tomlkit 賦值會重建項目、丟掉行內註解與縮排；把舊項的 trivia 搬到新項才保得住。"""
    item = tomlkit.item(value)
    if isinstance(old, Item):
        item.trivia.indent = old.trivia.indent
        item.trivia.comment_ws = old.trivia.comment_ws
        item.trivia.comment = old.trivia.comment
        item.trivia.trail = old.trivia.trail
    return item


def _json_with(text: str, segments: list[str | int], value: object, path: str) -> str:
    data = json.loads(text)
    _assign(data, segments, value, path, lambda new, _old: new)
    rendered = json.dumps(data, indent=_json_indent(text), ensure_ascii=False)
    return rendered + ("\n" if text.endswith("\n") else "")


def _json_indent(text: str) -> int | None:
    """原檔第一個縮排行的縮排寬度；單行／緊湊寫法回 None（json.dumps 即輸出單行）。"""
    for line in text.split("\n")[1:]:
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return None


def _ini_with(text: str, segments: list[str | int], value: object, path: str) -> str:
    """原地替換：只改那一行 `鍵 = 值` 的值文字，區段／註解／其餘行逐位元組不動。"""
    names = [segment for segment in segments if isinstance(segment, str)]
    if len(names) != len(segments) or not 0 < len(names) <= _INI_MAX_DEPTH:
        raise UnknownPath(
            f"ini 的路徑只能是「區段.鍵」或「鍵」，「{path}」不合。下一步：改成那兩種形式之一"
        )
    section = names[0] if len(names) == _INI_MAX_DEPTH else None
    key = names[-1]
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*[=:]\s*)(.*?)(\s*[;#].*)?$")
    lines = text.split("\n")
    current: str | None = None
    for index, line in enumerate(lines):
        header = _INI_HEADER.match(line)
        if header:
            current = header.group(1).strip()
            continue
        if current != section:
            continue
        match = pattern.match(line.rstrip("\r"))
        if match:
            tail = "\r" if line.endswith("\r") else ""
            lines[index] = f"{match.group(1)}{value}{match.group(3) or ''}{tail}"
            return "\n".join(lines)
    raise UnknownPath(
        f"路徑「{path}」在這份 ini 裡找不到對應的鍵。"
        "下一步：確認區段名與鍵名，或先在該區段加上這個鍵"
    )


def _unsupported(fmt: str) -> str:
    return (
        f"format「{fmt}」不是支援的格式；只接受 yaml／json／toml／ini／raw 五種。"
        "下一步：把清單檔該筆的 format 改成這五種之一"
    )


def _as_text(document: object) -> str:
    # raw／json 的 document 恆為原文字串（由 parse 建立）——cast 而非 isinstance 檢查，
    # 因為那個「不是字串」的情況只可能來自內部誤用，不是外部輸入（信任內部程式碼）。
    return cast(str, document)
