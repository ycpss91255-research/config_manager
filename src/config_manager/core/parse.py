"""core/parse — T6 格式解析與原樣寫回.

`parse(text, fmt)` 把一份被管理的 config 解析成 `Parsed`；`dump(parsed)` 把它寫回
文字。核心保證：**未改動任何值時，dump 的輸出與 parse 的輸入逐位元組相同**
（T6、ADR-00000029）。

解析器由 `format` 欄位決定，**不由副檔名推斷**——`.yml`、`.param` 等變體不可靠（#8）。
每一種格式用它在 Python 生態裡最成熟的 lossless round-trip 函式庫（ADR-00000029）：
yaml→ruamel.yaml、toml→tomlkit、ini→configobj；`raw` 不解析，原文通過。

核心層不做 I/O：`text` 由呼叫端讀好傳進來（CLAUDE.md 分層）。
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import cast

import tomlkit
from configobj import ConfigObj, ConfigObjError
from tomlkit import TOMLDocument
from tomlkit.exceptions import TOMLKitError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from config_manager.core.errors import SyntaxParse, UnsupportedFormat


@dataclass(frozen=True)
class Parsed:
    """T6 的（資料，原樣資訊）。

    `document` 是格式專屬的 round-trip 物件（或 `raw` 的原文字串）：dump 靠它重建
    原文，而型別判定（#9）、歧義值偵測（#10）也在它上面讀值與行號。`fmt` 記住是哪
    一種格式，dump 才知道用哪一支序列化器。
    """

    fmt: str
    document: object


def parse(text: str, fmt: str) -> Parsed:
    match fmt:
        case "raw":
            return Parsed(fmt="raw", document=text)
        case "yaml":
            return Parsed(fmt="yaml", document=_parse_yaml(text))
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
            return _dump_yaml(parsed.document)
        case "toml":
            return _dump_toml(parsed.document)
        case "json":
            return _as_text(parsed.document)
        case "ini":
            return _dump_ini(parsed.document)
        case _:
            raise UnsupportedFormat(_unsupported(parsed.fmt))


# ── yaml：ruamel.yaml 的 round-trip 模式，明確 1.2 以避開 1.1 的布林值陷阱 ──


def _yaml() -> YAML:
    # typ='rt' 就是「明確指定 YAML 1.2」的方式（#8）：它保留註解、引號樣式、鍵順序、
    # 結構，且把 `yes`／`no`／`on` 這些 1.1 會當成布林的裸字保持為字串——1.1 的布林陷阱
    # 因此不成立。preserve_quotes 保住引號樣式。不設 `version`：一設它就會在輸出頂端印出
    # `%YAML 1.2` 指令，破壞逐位元組往返，而 rt 本身已經給了 1.2 的取值語意。
    engine = YAML(typ="rt")
    engine.preserve_quotes = True
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


def _dump_yaml(document: object) -> str:
    buffer = io.StringIO()
    _yaml().dump(document, buffer)
    return buffer.getvalue()


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


def _parse_ini(text: str) -> object:
    try:
        return ConfigObj(io.BytesIO(text.encode("utf-8")), encoding="utf-8")
    except ConfigObjError as exc:
        line = getattr(exc, "line_number", None)
        loc = f"（第 {line} 行）" if line else ""
        raise SyntaxParse(
            f"ini 語法錯誤，解析不下去{loc}。下一步：修正該行的語法後重試", line
        ) from exc


def _dump_ini(document: object) -> str:
    lines = cast(ConfigObj, document).write()
    return b"\n".join(lines).decode("utf-8") + "\n"


def _unsupported(fmt: str) -> str:
    return (
        f"format「{fmt}」不是支援的格式；只接受 yaml／json／toml／ini／raw 五種。"
        "下一步：把清單檔該筆的 format 改成這五種之一"
    )


def _as_text(document: object) -> str:
    # raw／json 的 document 恆為原文字串（由 parse 建立）——cast 而非 isinstance 檢查，
    # 因為那個「不是字串」的情況只可能來自內部誤用，不是外部輸入（信任內部程式碼）。
    return cast(str, document)
