"""T1 — 清單檔載入與寫回。測試介面：core/config_list 的 load / dump。

用語依 CONTEXT.md。測試寫在公開介面上（load），不驗內部實作。
"""

import pytest

from core.config_list import dump, load
from core.errors import DuplicateTarget, DuplicateUid, InvalidFormat, TargetEscape


def test_valid_config_list_loads_with_correct_field_values():
    # 一份合法的 config 清單檔（結構依 §4.3）。期望值以獨立字面量寫下，
    # 不以與實作相同的方式重算（撰寫規則：期望值來自獨立來源）。
    text = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = ["navigation"]
"""

    config_list = load(text)

    entry = config_list.files[0]
    assert (
        entry.uid,
        entry.name,
        entry.hostname,
        entry.source,
        entry.target,
        entry.format,
        entry.groups,
    ) == (
        "mfz3k9q1",
        "navigation-params",
        "amr01",
        "files/amr01/nav2_params.yaml",
        "/opt/robot/config/nav2_params.yaml",
        "yaml",
        ["navigation"],
    )


def test_duplicate_uid_raises_named_exception_identifying_both_entries():
    # 兩筆共用同一個 uid。uid 是唯一的真實識別碼（CONTEXT），重號是靜默 bug 的來源。
    text = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/a.yaml"
target   = "/opt/a.yaml"
format   = "yaml"
groups   = []

[[files]]
uid      = "mfz3k9q1"
name     = "docker-daemon"
hostname = "amr01"
source   = "files/b.json"
target   = "/etc/b.json"
format   = "json"
groups   = []
"""

    with pytest.raises(DuplicateUid) as exc:
        load(text)

    message = str(exc.value)
    # 訊息必須指出是哪兩筆，且點名重複的 uid（不變式 2：大聲失敗）。
    assert "navigation-params" in message
    assert "docker-daemon" in message
    assert "mfz3k9q1" in message


def test_duplicate_target_raises_named_exception_identifying_both_entries():
    # 兩筆寫到同一個目標位置。寫出順序決定最終結果，是靜默 bug（不變式 2）。
    text = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/a.yaml"
target   = "/opt/shared.yaml"
format   = "yaml"
groups   = []

[[files]]
uid      = "mfz3k9r7"
name     = "docker-daemon"
hostname = "amr01"
source   = "files/b.yaml"
target   = "/opt/shared.yaml"
format   = "yaml"
groups   = []
"""

    with pytest.raises(DuplicateTarget) as exc:
        load(text)

    message = str(exc.value)
    assert "navigation-params" in message
    assert "docker-daemon" in message
    assert "/opt/shared.yaml" in message


def test_target_containing_dotdot_raises_named_exception():
    # 目標路徑含 .. 可逃逸到預期目錄外，是寫出的逃逸風險（逃逸防護）。
    text = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/a.yaml"
target   = "/opt/robot/../../etc/evil.yaml"
format   = "yaml"
groups   = []
"""

    with pytest.raises(TargetEscape) as exc:
        load(text)

    message = str(exc.value)
    assert "navigation-params" in message
    assert "/opt/robot/../../etc/evil.yaml" in message


def test_disallowed_format_raises_named_exception():
    # format 明寫、不由副檔名推斷；允許值為 yaml/json/toml/ini/raw。其餘須大聲失敗。
    text = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/a.xml"
target   = "/opt/a.xml"
format   = "xml"
groups   = []
"""

    with pytest.raises(InvalidFormat) as exc:
        load(text)

    message = str(exc.value)
    assert "navigation-params" in message
    assert "xml" in message


def test_duplicate_name_hostname_is_a_warning_not_an_exception():
    # (name, hostname) 重複只是警示，不是例外：uid 已保證唯一（警示與錯誤的分界）。
    text = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/a.yaml"
target   = "/opt/a.yaml"
format   = "yaml"
groups   = []

[[files]]
uid      = "mfz3k9r7"
name     = "navigation-params"
hostname = "amr01"
source   = "files/b.yaml"
target   = "/opt/b.yaml"
format   = "yaml"
groups   = []
"""

    config_list = load(text)  # 不得丟例外

    joined = " ".join(config_list.warnings)
    # 警示須指出是哪兩筆（以各自的 uid 區分，name 與 hostname 相同）。
    assert "mfz3k9q1" in joined and "mfz3k9r7" in joined


# 一份帶註解、空行、選填欄位、inline table 的清單檔，供原樣寫回的測試使用。
_ROUNDTRIP_TEXT = """\
list_version = 1

# 未個別指定時套用的預設權限
[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"          # 字串，避免被解析為整數

[[files]]
uid      = "mfz3k9q1"                             # 匯入時間戳，永不變
name     = "navigation-params"
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = ["navigation"]
description = "Nav2 導航參數"

[[files]]
uid      = "mfz3k9r7"
name     = "docker-daemon"
hostname = "amr01"
source   = "files/system/etc__docker__daemon.json"
target   = "/etc/docker/daemon.json"
format   = "json"
groups   = ["system"]
permissions = { owner = "root", group = "root", mode = "0644" }
"""


def test_dump_of_unchanged_config_list_is_byte_identical_to_input():
    config_list = load(_ROUNDTRIP_TEXT)
    # 清單檔本身也要原樣保留：未改動時逐位元組相同。
    assert dump(config_list, _ROUNDTRIP_TEXT) == _ROUNDTRIP_TEXT
