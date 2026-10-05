"""core/lintrc — 第 1 層規則的設定檔 `.lintrc.toml`（#43，設計 §6.4）。

§6.3 的正規形式白名單是目前版本的規則，不是永久定義：不同專案、不同格式對嚴謹度的需求不同。
規則抽到 config-repo 的 `.lintrc.toml`、隨 config 版控。**沒有這個檔就是預設值**（＝§6.3 的
全套，也就是這個檔出現之前的行為），所以既有的 repo 一個行為都不變。

    [yaml]
    yaml_version = "1.2"                      # 只支援 1.2
    boolean_literals = ["true", "false"]      # 白名單；"any" ＝ 不檢查布林的寫法
    allow_octal_prefix = false                # 前導零／0o 開頭的整數
    allow_scientific_notation = false         # 1e3
    duplicate_key = "error"                   # error | warn（沒有 ignore）
    trailing_whitespace = "error"             # error | warn | ignore
    indent = { style = "space", width = 2 }   # 只支援空白；Tab 一律擋

    [yaml.overrides]                          # 個別檔案的豁免，鍵是來源複本在 repo 內的路徑
    "files/legacy/old.yaml" = { boolean_literals = "any", reason = "第三方產生，暫不修改" }

    [toml]                                    # toml／json／ini 只有這兩個鍵：其餘對它們沒有意義
    duplicate_key = "error"
    trailing_whitespace = "error"

`duplicate_key` **不提供 `ignore`**：重複 key 的靜默失效是本系統要解決的核心問題之一，允許關掉會
使那個保證失效。豁免要填 `reason`，豁免本身才成為可稽核的紀錄。

核心層不做 I/O：檔案的文字由呼叫端傳進來。
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from config_manager.core.errors import LintrcInvalid

FORMATS = ("yaml", "toml", "json", "ini")
_SEVERITIES = ("error", "warn")
_TRAILING = ("error", "warn", "ignore")
_YAML_KEYS = frozenset({
    "yaml_version", "boolean_literals", "allow_octal_prefix", "allow_scientific_notation",
    "duplicate_key", "trailing_whitespace", "indent",
})
_SHARED_KEYS = frozenset({"duplicate_key", "trailing_whitespace"})


@dataclass(frozen=True)
class FormatRules:
    """一種格式（或一份檔案）套的第 1 層規則。預設值＝§6.3 的全套。"""

    boolean_literals: frozenset[str] | None = frozenset({"true", "false"})  # None＝不檢查
    allow_octal_prefix: bool = False
    allow_scientific_notation: bool = False
    duplicate_key: str = "error"
    trailing_whitespace: str = "error"
    indent_width: int = 2


@dataclass(frozen=True)
class Override:
    """個別檔案的豁免：寫了哪幾個設定（只蓋寫了的，其餘沿用那個格式的規則）、為什麼。"""

    fmt: str
    settings: Mapping[str, object]
    reason: str


@dataclass(frozen=True)
class Lintrc:
    formats: Mapping[str, FormatRules] = field(
        default_factory=lambda: MappingProxyType({fmt: FormatRules() for fmt in FORMATS})
    )
    overrides: Mapping[str, Override] = field(default_factory=lambda: MappingProxyType({}))


DEFAULT = Lintrc()


def default_text() -> str:
    """預設值寫成一份 `.lintrc.toml`（文件與種子用）；解讀回來要等於 `DEFAULT`。"""
    return (
        "# 第 1 層規則（設計 §6.3／§6.4）。沒有這個檔就是這些預設值；改了就隨 config 一起版控。\n"
        "[yaml]\n"
        'yaml_version = "1.2"\n'
        'boolean_literals = ["true", "false"]\n'
        "allow_octal_prefix = false\n"
        "allow_scientific_notation = false\n"
        'duplicate_key = "error"\n'
        'trailing_whitespace = "error"\n'
        'indent = { style = "space", width = 2 }\n'
        "\n"
        "[toml]\n"
        'duplicate_key = "error"\n'
        'trailing_whitespace = "error"\n'
        "\n"
        "[json]\n"
        'duplicate_key = "error"\n'
        'trailing_whitespace = "error"\n'
        "\n"
        "[ini]\n"
        'duplicate_key = "error"\n'
        'trailing_whitespace = "error"\n'
    )


def rules_for(lintrc: Lintrc, fmt: str, source: str) -> FormatRules:
    """`fmt` 格式、來源複本在 `source` 的那份 config 要套的規則：格式的規則，再套上它的豁免。"""
    rules = lintrc.formats.get(fmt, FormatRules())
    override = lintrc.overrides.get(source)
    if override is None or override.fmt != fmt:
        return rules
    return _apply(rules, override.settings, fmt, f"[{fmt}.overrides] 的「{source}」")


def parse_lintrc(text: str) -> Lintrc:
    """把 `.lintrc.toml` 的文字讀成規則。寫錯的地方丟 `LintrcInvalid`，指名哪裡錯。"""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise LintrcInvalid(
            f".lintrc.toml 不是合法的 TOML：{error}。下一步：依訊息指出的位置修正語法"
        ) from error
    stray = sorted(set(document) - set(FORMATS))
    if stray:
        raise LintrcInvalid(
            f".lintrc.toml 有不認得的分節：{'、'.join(stray)}。"
            f"下一步：分節只能是 {'／'.join(FORMATS)}"
        )
    formats = {fmt: FormatRules() for fmt in FORMATS}
    overrides: dict[str, Override] = {}
    for fmt in FORMATS:
        section = document.get(fmt, {})
        if not isinstance(section, Mapping):
            raise LintrcInvalid(f"[{fmt}] 不是一個區段。下一步：寫成 [{fmt}] 底下一行一個設定")
        own = {key: value for key, value in section.items() if key != "overrides"}
        formats[fmt] = _apply(FormatRules(), own, fmt, f"[{fmt}]")
        for source, given in _overrides_of(fmt, section.get("overrides", {})).items():
            overrides[source] = given
    return Lintrc(MappingProxyType(formats), MappingProxyType(overrides))


def _overrides_of(fmt: str, table: object) -> dict[str, Override]:
    if not isinstance(table, Mapping):
        raise LintrcInvalid(
            f"[{fmt}.overrides] 不是一個區段。"
            f"下一步：一行一份檔案，寫成 \"files/….{fmt}\" = {{ …, reason = \"…\" }}"
        )
    found: dict[str, Override] = {}
    for source, given in table.items():
        where = f"[{fmt}.overrides] 的「{source}」"
        if not isinstance(given, Mapping):
            raise LintrcInvalid(
                f"{where}要是一個 {{ … }}。下一步：寫成 {{ 設定 = 值, reason = \"理由\" }}"
            )
        reason = given.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise LintrcInvalid(
                f"{where}沒有填 reason（豁免的理由）。豁免要能稽核，少了理由就不知道為什麼放寬。"
                "下一步：補上 reason"
            )
        settings = {key: value for key, value in given.items() if key != "reason"}
        _apply(FormatRules(), settings, fmt, where)  # 只為驗：鍵認不認得、值合不合法
        found[str(source)] = Override(fmt, MappingProxyType(dict(settings)), reason.strip())
    return found


def _apply(base: FormatRules, given: Mapping[str, object], fmt: str, where: str) -> FormatRules:
    """把一段設定套在 `base` 上。每個鍵都驗：認不認得、值在不在允許的選項裡。"""
    allowed = _YAML_KEYS if fmt == "yaml" else _SHARED_KEYS
    unknown = sorted(set(given) - allowed)
    if unknown:
        only_yaml = "／".join(sorted(_YAML_KEYS - _SHARED_KEYS))
        hint = "" if fmt == "yaml" else f"（{only_yaml} 只對 yaml 有意義）"
        raise LintrcInvalid(
            f"{where}有不認得的鍵：{'、'.join(unknown)}{hint}。"
            f"下一步：只用 {'／'.join(sorted(allowed))}"
        )
    rules = base
    for key, value in given.items():
        rules = _SETTERS[key](rules, value, where)
    return rules


# 每個鍵怎麼驗、怎麼套。資料，不是邏輯：多一個設定就多一列。
def _yaml_version(rules: FormatRules, value: object, where: str) -> FormatRules:
    if value != "1.2":
        raise LintrcInvalid(
            f"{where}的 yaml_version「{value}」不支援：只有 1.2（§6.3 的 parser 設定）。"
            "下一步：寫 \"1.2\" 或拿掉這一行"
        )
    return rules


def _boolean_literals(rules: FormatRules, value: object, where: str) -> FormatRules:
    if value == "any":
        return replace(rules, boolean_literals=None)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise LintrcInvalid(
            f"{where}的 boolean_literals 要是字串清單（如 [\"true\", \"false\"]）或 \"any\"。"
            "下一步：改成其中一種"
        )
    return replace(rules, boolean_literals=frozenset(value))


def _flag(value: object, name: str, where: str) -> bool:
    if not isinstance(value, bool):
        raise LintrcInvalid(f"{where}的 {name} 要是 true 或 false。下一步：改成布林值")
    return value


def _allow_octal(rules: FormatRules, value: object, where: str) -> FormatRules:
    return replace(rules, allow_octal_prefix=_flag(value, "allow_octal_prefix", where))


def _allow_scientific(rules: FormatRules, value: object, where: str) -> FormatRules:
    return replace(
        rules, allow_scientific_notation=_flag(value, "allow_scientific_notation", where)
    )


def _duplicate_key(rules: FormatRules, value: object, where: str) -> FormatRules:
    if value not in _SEVERITIES:
        raise LintrcInvalid(
            f"{where}的 duplicate_key「{value}」不在允許的選項裡：只有 error｜warn，**沒有 ignore**"
            "——重複 key 的靜默失效是本系統要解決的問題，不能關掉。下一步：改成 error 或 warn"
        )
    return replace(rules, duplicate_key=str(value))


def _trailing_whitespace(rules: FormatRules, value: object, where: str) -> FormatRules:
    if value not in _TRAILING:
        raise LintrcInvalid(
            f"{where}的 trailing_whitespace「{value}」不在允許的選項裡：error｜warn｜ignore。"
            "下一步：改成其中之一"
        )
    return replace(rules, trailing_whitespace=str(value))


def _indent(rules: FormatRules, value: object, where: str) -> FormatRules:
    style = value.get("style") if isinstance(value, Mapping) else None
    width = value.get("width") if isinstance(value, Mapping) else None
    if style != "space":
        raise LintrcInvalid(
            f"{where}的 indent 的 style「{style}」不支援：YAML 的縮排只能用空白（Tab 不合規格）。"
            "下一步：寫 style = \"space\""
        )
    if not isinstance(width, int) or isinstance(width, bool) or width < 1:
        raise LintrcInvalid(
            f"{where}的 indent 的 width「{width}」要是正整數。下一步：例如 width = 2"
        )
    return replace(rules, indent_width=width)


_SETTERS: dict[str, Callable[[FormatRules, object, str], FormatRules]] = {
    "yaml_version": _yaml_version,
    "boolean_literals": _boolean_literals,
    "allow_octal_prefix": _allow_octal,
    "allow_scientific_notation": _allow_scientific,
    "duplicate_key": _duplicate_key,
    "trailing_whitespace": _trailing_whitespace,
    "indent": _indent,
}
