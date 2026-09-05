"""core/config_list — 清單檔載入 / 完整性檢查 / 寫回（PDF §3.3, §4.3）。

純邏輯，不做 I/O（ADR-00000011）：load/dump 收字串、不讀磁碟。
"""

import re
from pathlib import PurePosixPath
from collections.abc import Iterable

import tomlkit

from config_manager.core.errors import (
    DumpMismatch,
    DuplicateTarget,
    DuplicateUid,
    InvalidFormat,
    TargetEscape,
    UnknownField,
)
from config_manager.core.models import ConfigList, FileEntry, Permissions

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
# 條目的選填欄位（清單檔鍵名）。改動後不再帶值的，那一鍵要從清單檔消失。
_OPTIONAL_ENTRY_KEYS = frozenset(
    {"description", "schema", "requires_privilege", "permissions"}
)


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
    未觸動的部分逐位元組保留。三種變更皆支援：新增（附加於既有之後）、
    改動（就地更新該條目，只寫真的變了的鍵）、移除。
    """
    doc = tomlkit.parse(original)
    files = doc.get("files")
    if files is None:
        files = tomlkit.aot()
        doc["files"] = files

    doc_by_uid = _index_by_uid(files)
    for entry in config_list.files:
        doc_table = doc_by_uid.get(entry.uid)
        if doc_table is None:
            files.append(_entry_to_table(entry))
        else:
            _update_table(doc_table, entry)

    model_uids = {entry.uid for entry in config_list.files}
    removed = [uid for uid in doc_by_uid if uid not in model_uids]
    if removed:
        raise DumpMismatch(
            f"dump 尚不支援移除既有條目：uid {removed} 已從清單移除"
            "（目前只支援未改動與新增）。"
            "下一步：把這幾筆放回模型；解除納管要走 unmanage，不是從清單刪掉"
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
                f"無法辨識的欄位「{key}」（{loc}）；{where} 不接受此欄位。"
                f"下一步：刪掉該欄位，或改成 {where} 接受的欄位名"
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


def _index_by_uid(files: "tomlkit.items.AoT") -> dict[str, "tomlkit.items.Table"]:
    """以 uid 為索引指向原樣資訊裡的每一筆條目。uid 永不變（ADR-00000012）。"""
    return {table.get("uid"): table for table in files}


def _entry_values(entry: FileEntry) -> list[tuple[str, object]]:
    """一筆條目要出現在清單檔裡的鍵與值，依 PDF §4.3 的欄位順序。

    不帶值的選填欄位不在其中。新增（`_entry_to_table`）與改動（`_update_table`）
    共用這一份對應，兩條路徑才不會各自漂移——選填欄位只寫在其中一邊，正是
    「原本有、改後沒有」與反過來的情況被靜默丟掉的來源。
    """
    values: list[tuple[str, object]] = [
        ("uid", entry.uid),
        ("name", entry.name),
        ("hostname", entry.hostname),
        ("source", entry.source),
        ("target", entry.target),
        ("format", entry.format),
        ("groups", entry.groups),
    ]
    if entry.description is not None:
        values.append(("description", entry.description))
    if entry.schema_path is not None:
        values.append(("schema", entry.schema_path))  # 清單檔的鍵名是 schema
    if entry.requires_privilege:
        values.append(("requires_privilege", entry.requires_privilege))
    if entry.permissions is not None:
        values.append(("permissions", entry.permissions))
    return values


def _toml_value(value: object) -> object:
    """把模型的值換成寫進清單檔的形狀。permissions 是 inline table。"""
    if isinstance(value, Permissions):
        table = tomlkit.inline_table()
        table["owner"] = value.owner
        table["group"] = value.group
        table["mode"] = value.mode
        return table
    return value


def _entry_to_table(entry: FileEntry) -> "tomlkit.items.Table":
    """把一筆新條目渲染為 tomlkit 表（既有條目走 _update_table，原樣不受影響）。"""
    table = tomlkit.table()
    for key, value in _entry_values(entry):
        table[key] = _toml_value(value)
    return table


def _update_table(table: "tomlkit.items.Table", entry: FileEntry) -> None:
    """把改動就地套用在既有條目上，只寫真的變了的鍵。

    只寫變了的鍵，未改的欄位才留得住原本的引號樣式與行內註解——賦值會重建那個
    值，原樣資訊隨之消失。permissions 逐子鍵比對，理由相同：改一個 mode 不該把
    整個 inline table 重排一次。
    """
    values = _entry_values(entry)
    for key, value in values:
        current = table.get(key)
        if isinstance(value, Permissions) and isinstance(current, dict):
            for field, field_value in (
                ("owner", value.owner),
                ("group", value.group),
                ("mode", value.mode),
            ):
                if current.get(field) != field_value:
                    current[field] = field_value
        elif current != value:
            table[key] = _toml_value(value)

    written = {key for key, _ in values}
    for key in _OPTIONAL_ENTRY_KEYS - written:
        if key in table:
            del table[key]


def _check_integrity(config_list: ConfigList) -> None:
    """跨條目的完整性檢查。硬錯誤丟具名例外，軟問題收進 warnings。"""
    seen_uid: dict[str, FileEntry] = {}
    seen_target: dict[str, FileEntry] = {}
    seen_name_host: dict[tuple[str, str], FileEntry] = {}
    for entry in config_list.files:
        if ".." in PurePosixPath(entry.target).parts:
            raise TargetEscape(
                f"目標路徑含 ..（逃逸風險）：{entry.ref} 的目標「{entry.target}」。"
                f"下一步：把目標改寫成不含 .. 的路徑"
            )

        if entry.format not in ALLOWED_FORMATS:
            allowed = "／".join(ALLOWED_FORMATS)
            raise InvalidFormat(
                f"format 非允許值：{entry.ref} 的 format「{entry.format}」"
                f"，允許值為 {allowed}。"
                f"下一步：改成其中一個；format 明寫，不由副檔名推斷"
            )

        first_uid = seen_uid.get(entry.uid)
        if first_uid is not None:
            raise DuplicateUid(
                f"uid 重複：{first_uid.ref} 與 {entry.ref} 共用 uid「{entry.uid}」。"
                f"下一步：移除重複的那一筆——uid 納管後永不變更，不該有兩筆共用"
            )
        seen_uid[entry.uid] = entry

        first_target = seen_target.get(entry.target)
        if first_target is not None:
            raise DuplicateTarget(
                f"目標位置重複：{first_target.ref} 與 {entry.ref} "
                f"共用目標「{entry.target}」。"
                f"下一步：改掉其中一筆的目標位置——寫出順序會決定最終內容"
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
