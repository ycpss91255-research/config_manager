"""T16 — config 屬性與群組。core/attributes 的單元規格（#286）。

`update_attributes(清單, uid, 屬性) -> 新清單`：改名稱、群組、主機、說明。屬性是給人看的與
給樹分組用的，**不是身分**——身分是 uid，改名不影響任何關聯（歷史、草稿、搜尋都以 uid 找）。

T16 的 `group_tree` 那幾條（樹依群組重建、多重群組、未分群、父節點彙總最嚴重狀態）不在這裡：
樹由介面依每筆條目的 `groups` 當場建出來、沒有快取，那些行為在 T11 以瀏覽器驗。

核心層測試在無檔案系統、無 git、無網路下執行（CLAUDE.md）。
"""

import pytest

from config_manager.core.attributes import Attributes, describe_change, update_attributes
from config_manager.core.errors import AttributeInvalid, AttributesUnchanged, EntryNotFound
from config_manager.core.models import ConfigList, Defaults, FileEntry, Permissions

_DEFAULTS = Permissions(owner="root", group="root", mode="0644")


def _entry(uid, name, groups=(), description=None):
    return FileEntry(
        uid=uid, name=name, hostname="amr01", source=f"files/amr01/{name}.yaml",
        target=f"/etc/{name}.yaml", format="yaml", groups=list(groups), description=description,
    )


def _listing(*entries):
    return ConfigList(list_version=1, defaults=Defaults(permissions=_DEFAULTS), files=list(entries))


def _wanted(name="nav2", hostname="amr01", groups=(), description=None):
    return Attributes(name=name, hostname=hostname, groups=tuple(groups), description=description)


def test_updating_replaces_the_four_attributes_of_that_entry_only():
    listing = _listing(_entry("aaaaaaa1", "nav2"), _entry("bbbbbbb2", "camera", ["vision"]))

    updated = update_attributes(
        listing, "aaaaaaa1",
        _wanted("navigation", "site-a", ["navigation", "safety"], "Nav2 導航參數"),
    )

    first, second = updated.files
    assert (first.name, first.hostname, first.groups, first.description) == (
        "navigation", "site-a", ["navigation", "safety"], "Nav2 導航參數",
    )
    assert second == listing.files[1]  # 另一筆原封不動
    assert listing.files[0].name == "nav2"  # 傳入的那份沒被改


def test_renaming_changes_neither_the_uid_nor_where_the_content_lives():
    # 改名不影響任何關聯：uid、來源複本的位置、目標路徑都不變。
    listing = _listing(_entry("aaaaaaa1", "nav2"))

    entry = update_attributes(listing, "aaaaaaa1", _wanted("navigation")).files[0]

    assert (entry.uid, entry.source, entry.target) == (
        "aaaaaaa1", "files/amr01/nav2.yaml", "/etc/nav2.yaml",
    )


def test_surrounding_spaces_are_trimmed_and_an_empty_description_is_dropped():
    listing = _listing(_entry("aaaaaaa1", "nav2", description="舊說明"))

    entry = update_attributes(
        listing, "aaaaaaa1", _wanted("  navigation ", groups=[" safety "], description="   ")
    ).files[0]

    assert (entry.name, entry.groups, entry.description) == ("navigation", ["safety"], None)


@pytest.mark.parametrize(
    ("wanted", "field"),
    [
        (_wanted(name="   "), "name"),  # 空名稱
        (_wanted(name="nav@2"), "name"),  # @ 是參照形式 <name>@<hostname>-<uid> 的分隔
        (_wanted(name="nav\n2"), "name"),  # 控制字元會重塑變更紀錄的主旨
        (_wanted(name="n" * 81), "name"),
        (_wanted(hostname=""), "hostname"),
        (_wanted(hostname="amr 01"), "hostname"),
        (_wanted(hostname=".."), "hostname"),
        (_wanted(groups=["nav", " "]), "groups"),  # 空的群組名
        (_wanted(groups=["nav", "nav"]), "groups"),  # 重複的群組
        (_wanted(groups=["a\tb"]), "groups"),
        (_wanted(description="第一行\n第二行"), "description"),
        (_wanted(description="說" * 201), "description"),
    ],
)
def test_an_invalid_attribute_is_refused_naming_the_field(wanted, field):
    listing = _listing(_entry("aaaaaaa1", "old"))

    with pytest.raises(AttributeInvalid) as caught:
        update_attributes(listing, "aaaaaaa1", wanted)

    assert caught.value.field == field and "下一步" in str(caught.value)


def test_saving_without_changing_anything_is_refused_rather_than_recorded():
    # 什麼都沒改就不該留一筆空的變更紀錄。
    listing = _listing(_entry("aaaaaaa1", "nav2", ["navigation"], "說明"))

    with pytest.raises(AttributesUnchanged):
        update_attributes(listing, "aaaaaaa1", _wanted("nav2", "amr01", ["navigation"], "說明"))


def test_an_unknown_uid_is_a_named_error():
    with pytest.raises(EntryNotFound, match="zzzzzzz9"):
        update_attributes(_listing(_entry("aaaaaaa1", "nav2")), "zzzzzzz9", _wanted())


def test_the_change_is_described_field_by_field_for_the_record():
    # 變更紀錄的說明：改了哪幾項、改成什麼。沒動的不提。
    before = _entry("aaaaaaa1", "nav2", ["navigation"], "舊說明")
    renamed = before.model_copy(update={"name": "navigation"})
    regrouped = before.model_copy(update={"groups": ["perception", "safety"], "description": None})
    ungrouped = before.model_copy(update={"groups": [], "hostname": "site-a"})

    assert describe_change(before, renamed) == "名稱改為 navigation"
    assert describe_change(before, regrouped) == "群組改為 perception、safety；清除說明"
    assert describe_change(before, ungrouped) == "主機改為 site-a；移出所有群組"
    assert describe_change(before, before.model_copy(update={"description": "新"})) == "更新說明"
