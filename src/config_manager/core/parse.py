"""core/parse — T6 格式解析與原樣寫回.

`parse(text, fmt)` 把一份被管理的 config 解析成 `Parsed`；`dump(parsed)` 把它寫回
文字。核心保證：**未改動任何值時，dump 的輸出與 parse 的輸入逐位元組相同**
（T6、ADR-00000029）。

解析器由 `format` 欄位決定，**不由副檔名推斷**——`.yml`、`.param` 等變體不可靠（#8）。
yaml→ruamel.yaml、toml→tomlkit 用成熟的 lossless round-trip 函式庫（ADR-00000029），
document 即其 round-trip 物件。**json 與 ini 沒有保位元組的編輯器**（json 標準庫正規化；
configobj 補尾換行、去引號、壓 CRLF、吃尾隨空白，#217），故兩者**存原文**達到逐位元組往返，
只拿標準庫／configobj 驗語法與取值（`values()`）；改動值後的往返留待 v0.3.0（#217）。
`raw` 不解析，原文通過。需要走訪值（型別推斷）時一律經 `values()`。

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
        case "json" | "ini":
            # json／ini 都存原文（configobj／json 不保位元組），dump 回原文即逐位元組相同。
            return _as_text(parsed.document)
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


def _unsupported(fmt: str) -> str:
    return (
        f"format「{fmt}」不是支援的格式；只接受 yaml／json／toml／ini／raw 五種。"
        "下一步：把清單檔該筆的 format 改成這五種之一"
    )


def _as_text(document: object) -> str:
    # raw／json 的 document 恆為原文字串（由 parse 建立）——cast 而非 isinstance 檢查，
    # 因為那個「不是字串」的情況只可能來自內部誤用，不是外部輸入（信任內部程式碼）。
    return cast(str, document)
