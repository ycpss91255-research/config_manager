"""T1 — 清單檔載入與寫回。測試介面：core/config_list 的 load / dump。

用語依 CONTEXT.md。測試寫在公開介面上（load），不驗內部實作。
"""

import pytest

from core.config_list import load
from core.errors import DuplicateUid


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
