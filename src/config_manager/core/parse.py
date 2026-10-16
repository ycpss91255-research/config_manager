"""core/parse — T6 格式解析與原樣寫回.

`parse(text, fmt)` 把一份被管理的 config 解析成 `Parsed`；`dump(parsed)` 把它寫回
文字。核心保證：**未改動任何值時，dump 的輸出與 parse 的輸入逐位元組相同**
（T6、ADR-00000029）。

解析器由 `format` 欄位決定，**不由副檔名推斷**——`.yml`、`.param` 等變體不可靠（#8）。
yaml→ruamel.yaml、toml→tomlkit 用成熟的 lossless round-trip 函式庫（ADR-00000029），
document 即其 round-trip 物件。**json 與 ini 沒有保位元組的編輯器**（json 標準庫正規化；
configobj 補尾換行、去引號、壓 CRLF、吃尾隨空白，#217），故兩者**存原文**達到逐位元組往返，
只拿標準庫／configobj 驗語法與取值（`values()`）。改動一個值走 `set_value`（#17）：yaml／toml
直接改 round-trip 物件；json 與 ini 都原地文字替換——只換被改的那個值的原文（#17 的 D2、F9）。
`raw` 不解析，原文通過。需要走訪值（型別推斷）時一律經 `values()`。

核心層不做 I/O：`text` 由呼叫端讀好傳進來（CLAUDE.md 分層）。
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from typing import Any, cast

import tomlkit
from configobj import ConfigObj, ConfigObjError
from tomlkit import TOMLDocument
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import Item
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import ScalarString
from ruamel.yaml.nodes import MappingNode, SequenceNode
from ruamel.yaml.representer import RoundTripRepresenter

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

    `yaml_text` 是 parse 時的 yaml 原文：ruamel 把 `null` 與空白都讀成 None、寫回一律空白，
    `notes: null` 會變成 `notes:`（F9）。dump 時拿原文對出哪些空值原本寫成 `null`，照原寫法寫回
    （見 `_yaml_nulls`）。非 yaml 為空字串。
    """

    fmt: str
    document: object
    yaml_indent: tuple[int, int] | None = None
    yaml_text: str = ""


def parse(text: str, fmt: str) -> Parsed:
    match fmt:
        case "raw":
            return Parsed(fmt="raw", document=text)
        case "yaml":
            return Parsed(
                fmt="yaml",
                document=_parse_yaml(text),
                yaml_indent=_yaml_indent_style(text),
                yaml_text=text,
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
            return _dump_yaml(parsed.document, parsed.yaml_indent, parsed.yaml_text)
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


def _dump_yaml(document: object, indent: tuple[int, int] | None = None, text: str = "") -> str:
    engine = _yaml(indent)
    # 只換這一個引擎的 Representer（core/locate 等處各自建的 YAML() 不受影響）。
    engine.Representer = _NullKeepingRepresenter
    engine.representer.nulls = _yaml_nulls(text, document)
    buffer = io.StringIO()
    engine.dump(document, buffer)
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


# ── yaml 空值的寫法（F9）：`null` 照寫 `null`、空白照寫空白 ──


def _yaml_nulls(text: str, document: object) -> frozenset[tuple[int, object]]:
    """原文寫成 `null`、現在仍是 None 的那幾項，回 `(id(容器), 鍵或索引)`。

    load 出來的值只剩 None，分不出原本寫 `null` 還是空白；組成（compose）階段的節點還留著原文，
    而 load 出來的容器記得每一項在原文的起始行列（ruamel 公開的 `lc`，set_value 改值不會動它）。
    兩邊用位置對起來就知道是哪幾個。被整個換掉的新容器沒有原文位置，照 ruamel 原樣寫。只在 dump
    時做：讀值的路徑（型別推斷、驗證）不必多付一次組成。只認裸字小寫 `null`：`~`、`Null` 等寫法
    不在 F9 範圍，照舊寫成空白。
    """
    if "null" not in text:  # 多數檔案沒有寫出來的 null：不必再組成一次
        return frozenset()
    written = _null_positions(_yaml().compose(text))
    nulls: set[tuple[int, object]] = set()
    stack: list[object] = [document]
    seen: set[int] = set()
    while stack:
        container = stack.pop()
        if not isinstance(container, (CommentedMap, CommentedSeq)) or id(container) in seen:
            continue
        seen.add(id(container))  # 錨點／別名會讓同一個容器出現不只一次
        keys = list(container) if isinstance(container, CommentedMap) else range(len(container))
        for key in keys:
            stack.append(container[key])
            if container[key] is None and _start(container, key) in written:
                nulls.add((id(container), key))
    return frozenset(nulls)


def _null_positions(root: Any) -> set[tuple[int, int]]:
    """compose 出的節點樹裡，原文寫成裸字 `null` 的**值**節點的起始行列。

    只收 mapping 的值與清單元素、不收鍵：寫成空白的值，`lc` 位置指向下一個記號（常是下一個鍵的
    開頭），鍵名剛好叫 `null` 時才不會被誤認成「這個空值寫了 null」。
    """
    positions: set[tuple[int, int]] = set()
    stack: list[Any] = [root]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:  # 空文件 compose 回 None
            continue
        seen.add(id(node))
        if isinstance(node, MappingNode):
            stack.extend(value for _key, value in node.value)
        elif isinstance(node, SequenceNode):
            stack.extend(node.value)
        elif node.value == "null" and node.style is None:
            positions.add((node.start_mark.line, node.start_mark.column))
    return positions


def _start(container: CommentedMap | CommentedSeq, key: Any) -> object:
    """這一項在原文的起始行列；merge（`<<`）併進來的鍵在這個容器沒有自己的位置，回 None。"""
    try:
        if isinstance(container, CommentedMap):
            return container.lc.value(key)
        return container.lc.item(key)
    except KeyError:
        return None


_MERGE_TAG = "tag:yaml.org,2002:merge"


class _NullKeepingRepresenter(RoundTripRepresenter):
    """把 `_yaml_nulls` 找出的空值寫成 `null`，其餘照 ruamel 原樣。

    ruamel 的 represent_none 只拿到 None、不知道自己是哪個容器的哪一項，只能一律寫空白；容器這一層
    才知道。所以讓 ruamel 照常建好節點，再把該寫 `null` 的那幾個值節點改掉——版面邏輯不重寫。
    """

    # (id(容器), 鍵或索引)；_dump_yaml 每次 dump 前設定，那時容器都在 document 裡活著，id 不會撞。
    nulls: frozenset[tuple[int, object]] = frozenset()

    def represent_mapping(self, tag: Any, mapping: Any, flow_style: Any = None) -> Any:
        node = super().represent_mapping(tag, mapping, flow_style)
        # 有 merge（`<<`）時 ruamel 只寫本地的鍵、另插一個 `<<`：去掉 `<<` 再與本地的鍵對齊。
        own = (
            [key for key, _ in mapping.non_merged_items()]
            if isinstance(mapping, CommentedMap)
            else list(mapping)
        )
        value_nodes = [value for key, value in node.value if key.tag != _MERGE_TAG]
        if len(value_nodes) == len(own):
            self._respell(mapping, own, value_nodes)
        return node

    def represent_sequence(self, tag: Any, sequence: Any, flow_style: Any = None) -> Any:
        node = super().represent_sequence(tag, sequence, flow_style)
        self._respell(sequence, range(len(sequence)), node.value)
        return node

    def _respell(self, container: Any, keys: Sequence[object], nodes: list[Any]) -> None:
        for key, value_node in zip(keys, nodes, strict=True):
            if (id(container), key) in self.nulls and container[key] is None:
                value_node.value = "null"


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


# ── json：沒有成熟的 lossless 編輯函式庫，存原文保逐位元組往返 ──
# 未改動時 dump 回原文即逐位元組相同；改值時只替換那個值的原文（`_json_with`，F9）。


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


# `;` 開頭的整行註解。INI 沒有單一標準，但這是最常見的註解寫法（Windows 的 .ini、多數工具的
# 預設）；configobj 預設只認 `#`，遇到 `;` 開頭的行會當成語法錯誤。
_INI_SEMICOLON_COMMENT = re.compile(r"^([ \t]*);", re.MULTILINE)


def _ini_document(text: str) -> object:
    """把 ini 原文解析成 configobj 的結構（供驗語法與取值，非往返）。

    交給 configobj 之前，把 `;` 開頭的整行註解換成它認得的 `#`。**只換給解析器看的那一份**：
    `document` 存的仍是原文（`_parse_ini`），`;` 不會被改寫；一行換一行，行號不變。寫在值後面的
    `;` 不動——那可能是值的一部分（例如以分號分隔的清單）。
    """
    readable = _INI_SEMICOLON_COMMENT.sub(r"\1#", text)
    try:
        return ConfigObj(io.BytesIO(readable.encode("utf-8")), encoding="utf-8")
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
    項目，另把舊項的 trivia 搬到新項）。json 原地替換那個值的原文，其餘逐位元組不動（F9）；
    ini 原地文字替換（configobj 寫回會毀格式，#217）——#17 的 D2。
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
            _assign(parsed.document, segments, value, path, _yaml_item)


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
    # 整個清單替換（#46）：原本是 double 清單的，新元素寫成整數樣子也要帶小數點——與單一欄位
    # 同一條規則，清單裡新增的那一項才不會變成 ROS 讀不了的 int。
    if (
        isinstance(value, list)
        and isinstance(old, Sequence)
        and not isinstance(old, (str, bytes))
        and any(isinstance(item, float) for item in old)
    ):
        return [
            float(item) if isinstance(item, int) and not isinstance(item, bool) else item
            for item in value
        ]
    return value


def _yaml_item(value: object, old: object) -> object:
    """yaml 的新值：沒改的部分沿用舊節點，逐行不動（#46、F9）。

    介面改清單裡任何一處，送來的都是最外層的**整個**清單，而且是純 dict／list；寫法（一行的
    `[a, b]`、註解、引號、`1.0`）全記在舊節點身上，直接賦值就全丟了（U36：沒改的
    `ports: [8301]` 被拆成分行）。所以逐層比：值沒變就放回舊節點本身；變了的清單／對照表建新的
    round-trip 物件、搬舊節點的屬性（一行或分行、註解），裡面再逐項往下比。內容與順序照新值。
    `_keep_double` 每一層都套，巢狀的 double 改成整數樣子也帶小數點。
    """
    if _yaml_same(value, old):
        return old
    value = _keep_double(value, old)
    # 清成空的不借寫法：空清單寫不成分行，帶著舊註解會寫出不合法的 yaml，照引擎預設寫 `[]`／`{}`。
    if value and isinstance(value, dict) and isinstance(old, CommentedMap):
        kept_map = CommentedMap(
            (key, _yaml_item(item, old[key]) if key in old else item) for key, item in value.items()
        )
        # 對照表的註解以鍵為索引，搬過去就各歸各位；刪掉的鍵留下的註解不會被寫出。
        old.copy_attributes(kept_map)
        return kept_map
    if value and isinstance(value, list) and isinstance(old, CommentedSeq):
        twins = _yaml_twins(value, old)
        kept_seq = CommentedSeq(
            item if twin is None else _yaml_item(item, old[twin])
            for item, twin in zip(value, twins, strict=True)
        )
        # 清單的註解與原文行列（lc）都以**位置**為索引，調序、增刪後要跟著元素走。memo 讓搬過來
        # 的是一份複本，重排的是複本，舊節點不動。
        old.copy_attributes(kept_seq, memo={})
        for table in (kept_seq.ca.items, kept_seq.lc.data or {}):
            moved = {index: table[twin] for index, twin in enumerate(twins) if twin in table}
            table.clear()
            table.update(moved)
        return kept_seq
    if isinstance(value, str) and isinstance(old, ScalarString):
        # 改過的字串沿用原本的引號（ruamel 對既有的鍵賦值也這麼做）：`'on'` 改成 `off` 若寫成
        # 裸字，用 YAML 1.1 讀的 ROS 會當成布林。
        return type(old)(value)
    return value


def _yaml_twins(value: list[object], old: CommentedSeq) -> list[int | None]:
    """新清單每一項向舊清單的哪一項借寫法（舊索引；None＝新項，照引擎預設寫）。

    配過的舊項不再配，依序：同一位置、值沒變的（最常見：改的是別處）；值沒變、換了位置的（調序、
    前面增刪過），它們照樣帶著自己的註解與寫法——按值分桶找、不逐對比，逐對是 n²，幾千項的數值
    陣列存一次就要好幾秒；最後才是改過的項。
    """
    twins: list[int | None] = [
        position if position < len(old) and _yaml_same(item, old[position]) else None
        for position, item in enumerate(value)
    ]
    free = set(range(len(old))).difference(twins)
    buckets: dict[object, list[int]] = {}
    for index in sorted(free):
        buckets.setdefault(_yaml_bucket(old[index]), []).append(index)
    for position, item in enumerate(value):
        bucket = buckets.get(_yaml_bucket(item), []) if twins[position] is None else []
        twin = next((index for index in bucket if _yaml_same(item, old[index])), None)
        if twin is not None:
            bucket.remove(twin)
            free.remove(twin)
            twins[position] = twin
    # 改過的項：全體一起排，沒改的欄位最多的先配、一樣多時同一位置優先——逐項依位置配，排在前面的
    # 新項（一個欄位都沒對上）會先搶走同一位置那個舊項，真正被改的那一項反而沒得借，寫成新項。
    claims = sorted(
        (
            (_yaml_alike(item, old[index]), index == position, index, position)
            for position, item in enumerate(value)
            if twins[position] is None
            # 對照表才比欄位，其他種類只能照位置配（也免得幾千項的數值陣列逐對比）
            for index in (free if isinstance(item, dict) else free & {position})
        ),
        reverse=True,
    )
    for alike, same_place, index, position in claims:
        if (alike or same_place) and twins[position] is None and index in free:
            free.remove(index)
            twins[position] = index
    return twins


def _yaml_alike(value: object, old: object) -> int:
    """兩個對照表有幾個欄位的值沒變；有一邊不是對照表就是 0。"""
    if not isinstance(value, dict) or not isinstance(old, dict):
        return 0
    return sum(key in old and _yaml_same(item, old[key]) for key, item in value.items())


def _yaml_bucket(value: object) -> object:
    """分桶的鍵：值相同的一定同桶（1、1.0、True 同桶無妨，桶內再由 `_yaml_same` 細比）。"""
    if isinstance(value, dict):
        return frozenset((key, _yaml_bucket(item)) for key, item in value.items())
    if isinstance(value, list):
        return tuple(_yaml_bucket(item) for item in value)
    return value if isinstance(value, Hashable) else repr(value)


def _yaml_same(value: object, old: object) -> bool:
    """值與種類都沒變。Python 裡 `True == 1`、`1 == 1.0`，寫回去卻是不同的字，種類也要相同；
    唯一的例外：舊值是 double、新值送來整數樣子，算同一個值（JSON 分不出 5 與 5.0，同
    `_keep_double`）。只逐項比、不套它整個清單的規則——沒改的 `[1, 0.5]` 不算改成 `[1.0, 0.5]`。"""
    if isinstance(value, dict) and isinstance(old, dict):
        return value.keys() == old.keys() and all(
            _yaml_same(item, old[key]) for key, item in value.items()
        )
    if isinstance(value, list) and isinstance(old, list):
        return len(value) == len(old) and all(map(_yaml_same, value, old))
    value = _keep_double(value, old)
    return _yaml_kind(value) is _yaml_kind(old) and bool(value == old)


def _yaml_kind(value: object) -> type:
    # bool 是 int 的子類別，要先比；ruamel 的 ScalarFloat、ScalarInt、引號字串各歸 float、int、str。
    return next((kind for kind in (bool, int, float, str) if isinstance(value, kind)), type(value))


def _toml_item(value: object, old: object) -> object:
    """tomlkit 賦值會重建項目、丟掉行內註解與縮排；把舊項的 trivia 搬到新項才保得住。"""
    item = tomlkit.item(value)
    if isinstance(old, Item):
        item.trivia.indent = old.trivia.indent
        item.trivia.comment_ws = old.trivia.comment_ws
        item.trivia.comment = old.trivia.comment
        item.trivia.trail = old.trivia.trail
    return item


# json 的原地替換（F9）：整份 json.dumps 重寫會把沒改的一行陣列拆成一項一行、把空白與跳脫
# 正規化——違反「未改動的行逐位元組不動」。所以沿路徑在原文裡找出那個值的起訖，只換那一段。
# 值在哪裡結束交給標準庫的 raw_decode（解完一個值就回報停在哪），字串跳脫、數字寫法不自己解。
_JSON = json.JSONDecoder()
_JSON_SEPARATED = 2  # 清單要有兩項，才量得出原本項與項之間的分隔（_json_resize 照它接新項）


def _json_with(text: str, segments: list[str | int], value: object, path: str) -> str:
    start, end = _json_skip(text, 0), len(text)
    for segment in segments:  # 至少一段（_segments 保證），起訖必被路徑指到的那個值覆寫
        start, end = _child(_json_members(text, start), segment, path)
    return text[:start] + _json_splice(text, start, end, value) + text[end:]


def _json_members(text: str, start: int) -> dict[str | int, tuple[int, int]]:
    """`text[start]` 起的那個值的直接成員：鍵（物件）或索引（陣列）→ 成員值原文的（起, 訖）。

    純量沒有成員、回空——路徑再往下就是找不到（`_child` 丟 UnknownPath，與 yaml／toml 同一句）。
    重複的鍵後者蓋前者：json.loads 也取後者，改到的才是 values() 看得到的那一個。原文已在 parse
    驗過語法，這裡不再檢查逗號、冒號。
    """
    members: dict[str | int, tuple[int, int]] = {}
    closing = {"{": "}", "[": "]"}.get(text[start])
    index = _json_skip(text, start + 1)
    while closing is not None and text[index] != closing:
        key: str | int = len(members)
        if closing == "}":
            key, index = _JSON.raw_decode(text, index)
            index = _json_skip(text, _json_skip(text, index) + 1)  # 跳過 `:`
        end = _JSON.raw_decode(text, index)[1]
        members[key] = (index, end)
        index = _json_skip(text, end)
        if text[index] == ",":
            index = _json_skip(text, index + 1)
    return members


def _json_splice(text: str, start: int, end: int, value: object) -> str:
    """`text[start:end]` 那個值換成 `value` 後的原文——沒變的部分逐字留著。

    介面改清單裡任何一處都送出最外層的整個清單（#46），所以不能「有送就重寫」：值沒變→原文
    照抄（`1.50`、`1e3`、unicode 跳脫這類寫法也不動）；清單長度相同、物件的鍵與順序相同→逐個
    成員往下比；清單增刪了元素→頭尾沒變的照原文、只重組中間（`_json_resize`）；其餘（換了鍵、
    換了型別）才把這一個值整個重寫。double 的規則每一層都套（`_keep_double`），清單裡沒改的
    double 不因介面把 1.0 送成 1 而降成 int。清單的那條（有一項是 double 就把整數全轉）只給沒有
    舊項可對的新元素：配對到的元素各自由舊值決定，混著整數的清單裡沒動的整數才不被寫成 `1.0`。
    """
    old = json.loads(text[start:end])
    if not isinstance(value, list):
        value = _keep_double(value, old)
    if json.dumps(value) == json.dumps(old):  # 型別也要相同：1、1.0、true 寫出來各不相同
        return text[start:end]
    members = _json_members(text, start)
    if isinstance(value, dict) and isinstance(old, dict) and list(value) == list(old):
        return _json_patch(text, start, end, [(members[key], new) for key, new in value.items()])
    if isinstance(value, list) and isinstance(old, list):
        if len(value) == len(old):
            return _json_patch(text, start, end, list(zip(members.values(), value, strict=True)))
        if len(old) >= _JSON_SEPARATED and value:  # 清空就照下面寫成 []
            return _json_resize(text, start, end, value)
    return _json_dumps(_keep_double(value, old), text, _json_margin(text, start, end))


def _json_patch(
    text: str, start: int, end: int, pairs: list[tuple[tuple[int, int], object]]
) -> str:
    """逐個成員往下換：成員之間的逗號、空白、換行都是原文。依原文位置拼回去——有重複鍵時，
    被改的「後者」可能排在別的鍵後面。"""
    pieces: list[str] = []
    cursor = start
    for (begin, finish), new in sorted(pairs, key=lambda pair: pair[0]):
        pieces += [text[cursor:begin], _json_splice(text, begin, finish, new)]
        cursor = finish
    return "".join(pieces) + text[cursor:end]


def _json_resize(text: str, start: int, end: int, value: list[Any]) -> str:
    """清單增刪了元素（#46）：頭尾沒變的那幾項連同其間的逗號、換行照原文，只重組中間那段。

    中間的新舊元素依位置配對、往下比——改了一項又新增一項時，改的那項也只換它改的地方。多出來
    的新元素照第一項的樣子寫（一行或分行）；接縫用原清單前兩項之間的那段分隔（逗號加空白或換行
    縮排），新增的項才與舊項排在同一欄。
    """
    spans = list(_json_members(text, start).values())
    head, tail = _json_unchanged_ends(text, spans, value)
    fresh = cast(list[Any], _keep_double(value, json.loads(text[start:end])))
    paired = spans[head : len(spans) - tail]
    middle = [
        _json_splice(text, *paired[index], value[head + index])
        if index < len(paired)
        else _json_dumps(fresh[head + index], text, _json_margin(text, *spans[0]))
        for index in range(len(value) - head - tail)
    ]
    kept_head = [text[spans[0][0] : spans[head - 1][1]]] if head else []
    kept_tail = [text[spans[-tail][0] : spans[-1][1]]] if tail else []
    separator = text[spans[0][1] : spans[1][0]]
    inner = separator.join(kept_head + middle + kept_tail)
    return text[start : spans[0][0]] + inner + text[spans[-1][1] : end]


def _json_unchanged_ends(
    text: str, spans: list[tuple[int, int]], value: list[Any]
) -> tuple[int, int]:
    """新清單的頭、尾各有幾項，換上去後與原清單頭、尾的原文一字不差（兩段不重疊）。"""

    def unchanged(span: tuple[int, int], new: object) -> bool:
        return _json_splice(text, *span, new) == text[span[0] : span[1]]

    shorter = min(len(spans), len(value))
    head = 0
    while head < shorter and unchanged(spans[head], value[head]):
        head += 1
    tail = 0
    while head + tail < shorter and unchanged(spans[-1 - tail], value[-1 - tail]):
        tail += 1
    return head, tail


def _json_margin(text: str, start: int, end: int) -> str | None:
    """`text[start:end]` 寫成一行回 None；分行的回它起頭那一行的縮排——新寫的值照這個樣子。"""
    if "\n" not in text[start:end]:
        return None
    lead = text[text.rfind("\n", 0, start) + 1 : start]
    return lead[: len(lead) - len(lead.lstrip(" \t"))]


def _json_dumps(value: object, text: str, margin: str | None) -> str:
    """寫出一個新值：margin 為 None 寫成一行；否則一項一行，縮排單位取原檔，接續行前綴 margin
    （收尾括號與起頭那一行同欄），換行照原檔（CRLF 的檔不混進 LF）。"""
    if margin is None:
        return json.dumps(value, ensure_ascii=False)
    newline = "\r\n" if "\r\n" in text else "\n"
    rendered = json.dumps(value, indent=_json_indent(text), ensure_ascii=False)
    return rendered.replace("\n", newline + margin)  # 字串裡的換行 dumps 已跳脫，只換到結構換行


def _json_skip(text: str, index: int) -> int:
    """跳過 json 的空白（RFC 8259 只認這四種），回傳下一個非空白字元的位置。"""
    while index < len(text) and text[index] in " \t\n\r":
        index += 1
    return index


def _json_indent(text: str) -> str | None:
    """原檔第一個縮排行的縮排（空白或 tab 照抄）；單行／緊湊寫法回 None（json.dumps 即輸出單行）。

    回字串而不是寬度：tab 縮排的檔，新寫的值也用 tab，不會變成空白或擠成一行。
    """
    for line in text.split("\n")[1:]:
        stripped = line.lstrip(" \t")
        if stripped and stripped != line:
            return line[: len(line) - len(stripped)]
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
