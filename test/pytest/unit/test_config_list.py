"""T1 — 清單檔載入與寫回。測試介面：core/config_list 的 load / dump。

用語依 CONTEXT.md。測試寫在公開介面上（load），不驗內部實作。
"""

import pytest

from config_manager.core.config_list import dump, load
from config_manager.core.models import Permissions
from config_manager.core.errors import (
    DuplicateTarget,
    DuplicateUid,
    InvalidFormat,
    TargetEscape,
    UnknownField,
)


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
    # 訊息必須以完整參照形式 <name>@<hostname>-<uid> 指出是哪兩筆（CONTEXT
    # 身分欄位；不變式 2：大聲失敗）。斷言整串而非三個子字串——子字串各自
    # 出現不代表它們被組合成操作者查得到的那個識別碼。
    assert "navigation-params@amr01-mfz3k9q1" in message
    assert "docker-daemon@amr01-mfz3k9q1" in message


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
    assert "navigation-params@amr01-mfz3k9q1" in message
    assert "docker-daemon@amr01-mfz3k9r7" in message
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


# 一份含註解與單引號值的清單檔，用來檢查新增條目後既有部分不被動到。
_APPEND_TEXT = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

# 導航參數，勿手改
[[files]]
uid      = "mfz3k9q1"
name     = 'navigation-params'
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = ["navigation"]
"""


def test_appending_an_entry_keeps_existing_entries_verbatim():
    config_list = load(_APPEND_TEXT)
    # 由既有條目複製出一筆新條目（避免在測試中重列所有欄位）。
    new_entry = config_list.files[0].model_copy(
        update={
            "uid": "mfz3k9z9",
            "name": "camera-driver",
            "source": "files/amr01/camera.yaml",
            "target": "/opt/robot/config/camera.yaml",
        }
    )
    config_list.files.append(new_entry)

    result = dump(config_list, _APPEND_TEXT)

    # 既有內容（含註解、單引號、順序）逐字保留，且新條目出現。
    assert _APPEND_TEXT in result
    assert "mfz3k9z9" in result


def test_entry_optional_fields_load_correctly():
    # §4.3 的選填欄位：inline permissions 覆蓋、schema、requires_privilege。
    text = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid         = "mfz3k9r7"
name        = "docker-daemon"
hostname    = "amr01"
source      = "files/system/daemon.json"
target      = "/etc/docker/daemon.json"
format      = "json"
groups      = ["system"]
description = "Docker daemon 設定"
schema      = ".schemas/daemon.json"
requires_privilege = true
permissions = { owner = "root", group = "docker", mode = "0600" }
"""

    entry = load(text).files[0]
    assert (
        entry.requires_privilege,
        entry.schema_path,
        entry.description,
        entry.permissions.mode,
    ) == (True, ".schemas/daemon.json", "Docker daemon 設定", "0600")


def test_unrecognized_field_raises_named_exception_with_line_number():
    # 無法辨識的欄位是格式錯誤，須大聲失敗並指名行號（不變式 2 / PDF §329）。
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
bogus_field = "x"
"""

    with pytest.raises(UnknownField) as exc:
        load(text)

    message = str(exc.value)
    expected_line = next(
        i for i, line in enumerate(text.splitlines(), 1) if "bogus_field" in line
    )
    assert "bogus_field" in message
    assert str(expected_line) in message


def test_config_list_file_cannot_inject_warnings():
    # 清單檔嘗試設定內部 warnings 欄位，應被當作未知欄位拒絕（防注入、回歸測試）。
    text = """\
list_version = 1
warnings = ["INJECTED FROM FILE"]

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""

    with pytest.raises(UnknownField) as exc:
        load(text)
    assert "warnings" in str(exc.value)


def test_valid_config_list_loads_top_level_and_defaults():
    # core/models 無獨立測試介面，T1 是 list_version 與 defaults 的唯一觀察點。
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
"""

    config_list = load(text)
    assert (
        config_list.list_version,
        config_list.defaults.permissions.owner,
        config_list.defaults.permissions.group,
        config_list.defaults.permissions.mode,
    ) == (1, "root", "root", "0644")


def test_clean_config_list_has_no_warnings():
    # 只有重複才警示：乾淨清單的 warnings 為空（釘住警示的觸發條件）。
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
name     = "docker-daemon"
hostname = "amr02"
source   = "files/b.json"
target   = "/etc/b.json"
format   = "json"
groups   = []
"""

    assert load(text).warnings == []


# 三筆條目、各自帶前導註解、混用引號樣式與行內註解，供改動與移除的規格使用。
# 這些規格斷言的是**整份輸出逐位元組相同**——只斷言「改的那一筆對了」擋不住
# 其餘部分被重寫，而原樣保留正是這個系統存在的理由（ADR-00000029）。
_EDIT_TEXT = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"          # 字串，避免被解析為整數

# 導航參數，勿手改
[[files]]
uid      = "mfz3k9q1"
name     = 'navigation-params'   # 單引號
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = ["navigation"]
schema   = ".schemas/nav2.json"

# Docker daemon 設定
[[files]]
uid      = "mfz3k9r7"
name     = "docker-daemon"
hostname = "amr01"
source   = "files/system/daemon.json"
target   = "/etc/docker/daemon.json"
format   = "json"
groups   = ["system"]
permissions = { owner = "root", group = "root", mode = "0644" }

# 相機驅動
[[files]]
uid      = "mfz3k9z9"
name     = "camera-driver"
hostname = "amr01"
source   = "files/amr01/camera.yaml"
target   = "/opt/robot/config/camera.yaml"
format   = "yaml"
groups   = ["sensor"]
"""


def test_editing_an_entry_leaves_every_other_entry_byte_identical():
    # 改動既有條目後寫回：該條目更新，其餘條目的註解、順序、引號樣式逐位元組不變
    # （PDF §8.2 v0.1.0 檢查點第三條）。期望值以獨立字面量寫下。
    expected = _EDIT_TEXT.replace(
        'target   = "/etc/docker/daemon.json"',
        'target   = "/etc/docker/daemon.json.new"',
    )

    config_list = load(_EDIT_TEXT)
    config_list.files[1].target = "/etc/docker/daemon.json.new"

    assert dump(config_list, _EDIT_TEXT) == expected


def test_editing_one_field_leaves_the_entrys_other_fields_verbatim():
    # 改動只動到該欄位：同一條目未改的欄位保留原本的引號樣式與行內註解，
    # permissions 也只改變了的那個子鍵，不把整個 inline table 重排一次。
    expected = _EDIT_TEXT.replace('mode = "0644" }', 'mode = "0600" }')

    config_list = load(_EDIT_TEXT)
    config_list.files[1].permissions = Permissions(
        owner="root", group="root", mode="0600"
    )

    assert dump(config_list, _EDIT_TEXT) == expected


def test_an_optional_field_that_loses_its_value_disappears_from_the_list_file():
    # 選填欄位由有變無：那一鍵要從清單檔消失。留著等於寫回一個模型沒說的值。
    expected = _EDIT_TEXT.replace('schema   = ".schemas/nav2.json"\n', "")

    config_list = load(_EDIT_TEXT)
    config_list.files[0].schema_path = None

    assert dump(config_list, _EDIT_TEXT) == expected


def test_an_optional_field_that_gains_a_value_appears_in_the_list_file():
    # 反過來的方向：由無變有時那一鍵要出現。兩個方向都會靜默丟東西（不變式 2）。
    expected = _EDIT_TEXT.replace(
        'groups   = ["sensor"]\n',
        'groups   = ["sensor"]\nrequires_privilege = true\n',
    )

    config_list = load(_EDIT_TEXT)
    config_list.files[2].requires_privilege = True

    assert dump(config_list, _EDIT_TEXT) == expected


# 中間那一筆的完整區塊，含它自己的前導註解與後面那個空行。移除它之後，
# 這一整塊要消失，而前後兩筆各自的註解要留在原位、不被挪用。
_DOCKER_ENTRY_BLOCK = """\
# Docker daemon 設定
[[files]]
uid      = "mfz3k9r7"
name     = "docker-daemon"
hostname = "amr01"
source   = "files/system/daemon.json"
target   = "/etc/docker/daemon.json"
format   = "json"
groups   = ["system"]
permissions = { owner = "root", group = "root", mode = "0644" }

"""


def test_removing_an_entry_takes_its_comment_and_leaves_the_others_verbatim():
    # 移除既有條目後寫回：該條目連同它的註解一起消失，其餘條目的註解、順序、
    # 引號樣式逐位元組不變——tomlkit 把註解掛在前一筆，所以「下一筆繼承了別人
    # 的註解」是這裡真正會發生的靜默損壞。
    expected = _EDIT_TEXT.replace(_DOCKER_ENTRY_BLOCK, "")

    config_list = load(_EDIT_TEXT)
    del config_list.files[1]

    assert dump(config_list, _EDIT_TEXT) == expected


def test_removing_the_first_entry_also_takes_the_comment_above_it():
    # 第一筆的前導註解不在 AOT 裡，而在它前面那個 table 的尾端。留著它，
    # 第二筆就會頂著第一筆的註解——與上一條是同一種損壞的另一個位置。
    expected = _EDIT_TEXT.replace(
        """\
# 導航參數，勿手改
[[files]]
uid      = "mfz3k9q1"
name     = 'navigation-params'   # 單引號
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = ["navigation"]
schema   = ".schemas/nav2.json"

""",
        "",
    )

    config_list = load(_EDIT_TEXT)
    del config_list.files[0]

    assert dump(config_list, _EDIT_TEXT) == expected


def test_appending_an_entry_keeps_its_optional_fields():
    # 附加的新條目若帶選填欄位（schema／requires_privilege／permissions），
    # 寫回後不得被丟棄——靜默丟欄位即靜默失敗（不變式 2、PDF §4.3）。
    original = """\
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
"""

    config_list = load(original)
    config_list.files.append(
        config_list.files[0].model_copy(
            update={
                "uid": "mfz3k9z9",
                "name": "docker-daemon",
                "source": "files/b.json",
                "target": "/etc/b.json",
                "format": "json",
                "schema_path": ".schemas/daemon.json",
                "requires_privilege": True,
                "permissions": Permissions(owner="root", group="docker", mode="0600"),
            }
        )
    )

    result = dump(config_list, original)

    dropped = [
        token
        for token in (".schemas/daemon.json", "requires_privilege", "0600")
        if token not in result
    ]
    assert dropped == []
