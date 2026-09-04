"""core/config_list — 清單檔載入 / 完整性檢查 / 寫回（PDF §3.3, §4.3）。

純邏輯，不做 I/O（ADR-00000011）：load/dump 收字串、不讀磁碟。
"""

import re
from pathlib import PurePosixPath
from collections.abc import Iterable

import tomlkit

from core.errors import (
    DumpMismatch,
    DuplicateTarget,
    DuplicateUid,
    InvalidFormat,
    TargetEscape,
    UnknownField,
)
from core.models import ConfigList, FileEntry

# 被管理 config 的允許格式（T6 亦處理這些）。raw = 不解析、只做版控。
ALLOWED_FORMATS = ("yaml", "json", "toml", "ini", "raw")

# 允許的鍵集（用清單檔的鍵名，含 schema 別名）。不含 warnings 等內部欄位。
_TOP_KEYS = {"list_version", "defaults", "files"}
_DEFAULTS_KEYS = {"permissions"}
_PERM_KEYS = {"owner", "group", "mode"}
_ENTRY_KEYS = {
    "uid", "name", "hostname", "source", "target", "format", "groups",
    "description", "schema", "requires_privilege", "permissions",
}


def load(text: str) -> ConfigList:
    """把 config 清單檔的原始文字解析為已驗證的資料模型。"""
    doc = tomlkit.parse(text)
    _check_unknown_fields(doc, text)
    config_list = ConfigList.model_validate(doc.unwrap())
    _check_integrity(config_list)
    return config_list


def dump(config_list: ConfigList, original: str) -> str:
    """把清單檔寫回文字，保留原樣（註解、順序、引號樣式）。

    原樣資訊為原始清單檔文字；以 tomlkit 重新解析後在其上套用變更，
    未觸動的條目逐位元組保留。目前支援的變更：新增條目（附加於既有之後）。
    """
    doc = tomlkit.parse(original)
    files = doc.get("files")
    if files is None:
        files = tomlkit.aot()
        doc["files"] = files

    doc_by_uid = {table.get("uid"): table for table in files}
    for entry in config_list.files:
        doc_table = doc_by_uid.get(entry.uid)
        if doc_table is None:
            files.append(_entry_to_table(entry))
        elif FileEntry.model_validate(doc_table.unwrap()) != entry:
            raise DumpMismatch(
                f"dump 尚不支援改動既有條目：{entry.ref} 與原始清單檔內容不符"
                "（目前只支援未改動與新增）"
            )

    model_uids = {entry.uid for entry in config_list.files}
    removed = [uid for uid in doc_by_uid if uid not in model_uids]
    if removed:
        raise DumpMismatch(
            f"dump 尚不支援移除既有條目：uid {removed} 已從清單移除"
            "（目前只支援未改動與新增）"
        )

    return tomlkit.dumps(doc)


def _find_line(text: str, key: str) -> int | None:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for lineno, line in enumerate(text.splitlines(), 1):
        if pattern.match(line):
            return lineno
    return None


def _reject_unknown(
    keys: Iterable[str], allowed: set[str], text: str, where: str
) -> None:
    for key in keys:
        if key not in allowed:
            line = _find_line(text, key)
            loc = f"第 {line} 行" if line is not None else where
            raise UnknownField(
                f"無法辨識的欄位「{key}」（{loc}）；{where} 不接受此欄位"
            )


def _check_unknown_fields(doc: "tomlkit.TOMLDocument", text: str) -> None:
    """在轉為資料模型前，對照鍵集攔下未知欄位並指名行號（PDF §329）。"""
    _reject_unknown(doc.keys(), _TOP_KEYS, text, "清單檔頂層")

    defaults = doc.get("defaults")
    if defaults is not None:
        _reject_unknown(defaults.keys(), _DEFAULTS_KEYS, text, "defaults")
        perms = defaults.get("permissions")
        if perms is not None:
            _reject_unknown(perms.keys(), _PERM_KEYS, text, "defaults.permissions")

    files = doc.get("files")
    if files is not None:
        for entry in files:
            _reject_unknown(entry.keys(), _ENTRY_KEYS, text, "檔案條目")
            eperm = entry.get("permissions")
            if eperm is not None and hasattr(eperm, "keys"):
                _reject_unknown(eperm.keys(), _PERM_KEYS, text, "條目的 permissions")


def _entry_to_table(entry: FileEntry) -> "tomlkit.items.Table":
    """把一筆新條目渲染為 tomlkit 表（既有條目不經此路徑，故不影響其原樣）。"""
    table = tomlkit.table()
    table["uid"] = entry.uid
    table["name"] = entry.name
    table["hostname"] = entry.hostname
    table["source"] = entry.source
    table["target"] = entry.target
    table["format"] = entry.format
    table["groups"] = entry.groups
    if entry.description is not None:
        table["description"] = entry.description
    return table


def _check_integrity(config_list: ConfigList) -> None:
    """跨條目的完整性檢查。硬錯誤丟具名例外，軟問題收進 warnings。"""
    seen_uid: dict[str, FileEntry] = {}
    seen_target: dict[str, FileEntry] = {}
    seen_name_host: dict[tuple[str, str], FileEntry] = {}
    for entry in config_list.files:
        if ".." in PurePosixPath(entry.target).parts:
            raise TargetEscape(
                f"目標路徑含 ..（逃逸風險）：{entry.ref} 的目標「{entry.target}」"
            )

        if entry.format not in ALLOWED_FORMATS:
            allowed = "／".join(ALLOWED_FORMATS)
            raise InvalidFormat(
                f"format 非允許值：{entry.ref} 的 format「{entry.format}」"
                f"，允許值為 {allowed}"
            )

        first_uid = seen_uid.get(entry.uid)
        if first_uid is not None:
            raise DuplicateUid(
                f"uid 重複：{first_uid.ref} 與 {entry.ref} 共用 uid「{entry.uid}」"
            )
        seen_uid[entry.uid] = entry

        first_target = seen_target.get(entry.target)
        if first_target is not None:
            raise DuplicateTarget(
                f"目標位置重複：{first_target.ref} 與 {entry.ref} "
                f"共用目標「{entry.target}」"
            )
        seen_target[entry.target] = entry

        key = (entry.name, entry.hostname)
        first_pair = seen_name_host.get(key)
        if first_pair is not None:
            config_list.warnings.append(
                f"警示：{first_pair.ref} 與 {entry.ref} 共用相同的 name 與 "
                f"hostname（uid 仍唯一，功能不受影響）"
            )
        else:
            seen_name_host[key] = entry
