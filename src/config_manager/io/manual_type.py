"""io/manual_type — 把人工指定的型別寫進 config-repo 的 schema（#285、ADR-00000021）。

指定與清除的規則在 `core/manual_types`（純邏輯）；這裡做 I/O 的那一半：讀這份 config 現在的
schema 與來源複本、確認要指定的是一個存在的單一參數、確認新型別與現值相容，再交給
`io/schema.store_schema` 寫回並記一筆 `meta`（指定與清除各一筆，與參數值的變更分開，§7.5.3）。

**與現值不相容就擋在指定這一步**：把 `0.8` 指定成整數，這份 config 當場就不合格、之後連存
都存不了。要改型別得先讓值符合（不變式 4：無法兩全時落向安全）。
"""

from __future__ import annotations

from config_manager.core.errors import PathNotSpecifiable, TypeIncompatible, TypeNotSpecified
from config_manager.core.inference import infer_types
from config_manager.core.manual_types import clear, specify
from config_manager.core.models import FileEntry
from config_manager.core.parse import parse, values
from config_manager.core.validate import check
from config_manager.io.errors import SchemaUnavailable
from config_manager.io.parsers import read_source
from config_manager.io.schema import entry_of, read_schema, store_schema

# 型別在介面與變更紀錄裡的名字（設計 §7.5.1：float 顯示為 double）。
TYPE_LABEL = {"int": "int", "float": "double", "bool": "bool", "string": "string"}
_CONTAINERS = frozenset({"dict", "list"})


def specify_type(
    repo: str, uid: str, path: str, type_name: str | None, author: str
) -> FileEntry:
    """把 `uid` 那份 config 的 `path` 指定成 `type_name`；`type_name` 是 None 就清除那個指定。

    回更新後的條目。清到 schema 什麼規則都不剩時，schema 檔與條目的指向一起拿掉。
    """
    entry = entry_of(repo, uid)
    label = f"{entry.name}@{entry.hostname}"
    if entry.format == "raw":
        raise SchemaUnavailable(
            f"「{label}」的格式是 raw（不解析、只做版控），沒有參數可以指定型別。"
            "下一步：這份維持未驗證；要把關就以可解析的格式重新納管"
        )
    schema = read_schema(repo, entry)
    if type_name is None:
        if schema is None:
            raise TypeNotSpecified(
                f"「{label}」沒有 schema，也就沒有人工指定的型別可以清除。"
                "下一步：重新整理這份 config，確認要清除的是標著「已指定」的那一列"
            )
        return store_schema(repo, uid, clear(schema, path), f"清除「{path}」的型別指定", author)
    text = read_source(repo, entry.source)
    _require_single_parameter(text, entry, path)
    new_schema = specify(schema, path, type_name)
    broken = [p for p in check(text, entry.format, schema=new_schema) if p.path == path]
    if broken:
        raise TypeIncompatible(
            f"「{path}」現在的值不符合 {TYPE_LABEL[type_name]}：{broken[0].message}，沒有指定。"
            f"下一步：先把值改成符合的（{broken[0].suggestion}）並進版，再指定型別"
        )
    summary = f"指定「{path}」的型別為 {TYPE_LABEL[type_name]}"
    return store_schema(repo, uid, new_schema, summary, author)


def _require_single_parameter(text: str, entry: FileEntry, path: str) -> None:
    """`path` 必須是這份 config 裡一個存在的單一參數（不是物件、清單或清單的元素）。"""
    kind = infer_types(values(parse(text, entry.format))).get(path)
    if "[" in path or kind is None or kind in _CONTAINERS:
        raise PathNotSpecifiable(
            f"「{path}」不是「{entry.name}@{entry.hostname}」裡可以指定型別的單一參數"
            "（不存在，或是物件、清單、清單的元素）。"
            "下一步：重新整理這份 config，只對欄位表裡的單一參數指定型別"
        )
