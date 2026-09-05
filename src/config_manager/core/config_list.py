"""core/config_list — 清單檔載入 / 完整性檢查 / 寫回（PDF §3.3, §4.3）。

純邏輯，不做 I/O（ADR-00000011）：load/dump 收字串、不讀磁碟。
"""

import re
from pathlib import PurePosixPath
from collections.abc import Iterable

import tomlkit
from tomlkit import items

from config_manager.core.errors import (
    DumpMismatch,
    DuplicateTarget,
    DuplicateUid,
    InvalidFormat,
    TargetEscape,
    UnknownField,
)
from config_manager.core.models import ConfigList, FileEntry, Permissions

# tomlkit 容器 body 的一項：沒有鍵的是空白與註解，有鍵的是真正的值。
_BodyItem = tuple[items.Key | None, items.Item]

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
    removed = [
        index
        for index, table in enumerate(files.body)
        if table.get("uid") not in model_uids
    ]
    if removed:
        # 搬前導註解要排在新增與改動之後：註解一旦進了 trivia.indent，tomlkit 會
        # 把註解文字裡的空白當成縮排，套到後續新增的鍵上（`# 相機驅動` 的那個
        # 空格會變成新鍵前面的一格）。只在真的要刪的時候搬，順序就不會咬到。
        _adopt_leading_trivia(doc, files)
        for index in reversed(removed):
            del files[index]

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


def _take_trailing_items(body: list[_BodyItem], stop: int) -> list[_BodyItem]:
    """取走 body[:stop] 尾端那段空白與註解，回傳被取走的項目。

    只動尾端，所以容器內部「鍵 → 位置」的對照不受影響——被取走的項目本來就
    沒有鍵，排在它們前面的鍵位置也沒有變。
    """
    start = stop
    while start > 0 and body[start - 1][0] is None:
        start -= 1
    run = body[start:stop]
    del body[start:stop]
    return run


def _take_trailing_trivia(body: list[_BodyItem], stop: int) -> str:
    """同上，但回傳被取走那段的原文。"""
    return "".join(item.as_string() for _, item in _take_trailing_items(body, stop))


def _body_before_files(doc: tomlkit.TOMLDocument) -> tuple[list[_BodyItem], int]:
    """找出 files 這個 AOT 前方那段空白與註解所在的容器 body 與位置。

    tomlkit 把「`[[files]]` 上方那幾行」存在**前一個容器**的尾端。第一筆條目的
    前導註解因此不在 AOT 裡，而在前一個 table 的最深處——`[defaults.permissions]`
    的尾端就是這個 repo 的清單檔實際長的樣子。
    """
    body: list[_BodyItem] = doc.body
    index = next(
        position
        for position, (key, _) in enumerate(body)
        if key is not None and key.key == "files"
    )
    while index > 0:
        previous = body[index - 1][1]
        if not isinstance(previous, items.Table):
            break
        body = previous.value.body
        index = len(body)
    return body, index


def _adopt_leading_trivia(doc: tomlkit.TOMLDocument, files: items.AoT) -> None:
    """把每一筆條目上方的空白與註解，搬到那一筆條目自己身上。

    tomlkit 解析後，一筆條目的前導註解掛在**前一筆**的尾端。照那個形狀直接刪掉
    一筆，被刪的那筆會把自己的註解留給下一筆，而下一筆的註解跟著它一起消失
    ——輸出裡「那一筆不見了」成立，「其餘條目原樣保留」卻不成立，兩件事在這裡
    分道揚鑣。搬完之後每一筆自帶前導註解，刪除就只是刪除。

    搬移本身不改變輸出的任何一個位元組，但**只在真的要刪的時候呼叫**：理由寫在
    `dump` 裡那一段（tomlkit 會把註解文字裡的空白當成後續新增鍵的縮排）。
    """
    tables = list(files.body)
    if not tables:
        return

    body, index = _body_before_files(doc)
    _prepend_indent(tables[0], _take_trailing_trivia(body, index))
    for previous, current in zip(tables, tables[1:], strict=False):
        previous_body: list[_BodyItem] = previous.value.body
        _prepend_indent(
            current, _take_trailing_trivia(previous_body, len(previous_body))
        )


def _prepend_indent(table: items.Table, trivia: str) -> None:
    """把一段原文接到 table 的前導縮排前面（AOT 的表頭就從這裡渲染）。"""
    if trivia:
        table.trivia.indent = trivia + table.trivia.indent


def _index_by_uid(files: items.AoT) -> dict[str, items.Table]:
    """以 uid 為索引指向原樣資訊裡的每一筆條目。

    uid 是唯一的真實識別碼、納管後永不變更（ADR-00000012），dump 靠它把模型的
    每一筆對回原文的那一筆。定位不了就不能繼續：缺 uid 的那一筆會被當成「已從
    清單移除」而刪掉，共用 uid 的兩筆只有一筆認得出來、另一筆改不到也刪不掉。
    兩者都是靜默丟資料，所以在動任何東西之前先擋（不變式 2）。

    `load` 擋得住這兩種清單檔，但 `original` 是獨立參數，沒有東西保證它經過
    `load`——契約在做實事之前先自我驗證。
    """
    indexed: dict[str, items.Table] = {}
    for position, table in enumerate(files.body, 1):
        uid = table.get("uid")
        if uid is None:
            raise DumpMismatch(
                f"原樣資訊的第 {position} 筆條目沒有 uid：dump 以 uid 把模型的"
                "每一筆對回原文的那一筆，沒有 uid 就對不回去。"
                "下一步：把該筆的 uid 補回原始清單檔——uid 納管後永不變更，"
                "不該有條目沒有它"
            )
        if uid in indexed:
            raise DumpMismatch(
                f"原樣資訊有兩筆共用 uid「{uid}」（第 {position} 筆與它之前的"
                "一筆）：共用 uid 時 dump 只認得出其中一筆，另一筆改不到也刪不掉。"
                "下一步：移除重複的那一筆——uid 是唯一的真實識別碼，不該有兩筆共用"
            )
        indexed[uid] = table
    return indexed


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


def _update_permissions(table: dict[str, object], permissions: Permissions) -> None:
    """逐子鍵更新既有的 permissions，只寫變了的那一個。

    整個換掉會把 `{ owner = …, mode = "0644" }` 重排成 tomlkit 的預設間距，
    未改的兩個子鍵跟著失去原樣——改一個 mode 不該動到另外兩個。
    """
    for key, value in (
        ("owner", permissions.owner),
        ("group", permissions.group),
        ("mode", permissions.mode),
    ):
        if table.get(key) != value:
            table[key] = value


def _update_table(table: "tomlkit.items.Table", entry: FileEntry) -> None:
    """把改動就地套用在既有條目上，只寫真的變了的鍵。

    只寫變了的鍵，未改的欄位才留得住原本的引號樣式與行內註解——賦值會重建那個
    值，原樣資訊隨之消失。permissions 逐子鍵比對，理由相同：改一個 mode 不該把
    整個 inline table 重排一次。
    """
    # 條目尾端那段空白與註解，渲染出來是**下一筆條目**的前導註解。tomlkit 的
    # append 一律加在容器最尾端，所以先把它取下來——否則新加的選填欄位會落到
    # 下一筆的註解下方，而那一行看起來仍然「有寫出來」。
    body: list[_BodyItem] = table.value.body
    trailing = _take_trailing_items(body, len(body))

    values = _entry_values(entry)
    for key, value in values:
        current = table.get(key)
        if isinstance(value, Permissions) and isinstance(current, dict):
            _update_permissions(current, value)
        elif current != value:
            table[key] = _toml_value(value)

    written = {key for key, _ in values}
    for key in _OPTIONAL_ENTRY_KEYS - written:
        if key in table:
            del table[key]

    body.extend(trailing)


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
