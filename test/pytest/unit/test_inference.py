"""T12 — 型別推斷。core/inference 的單元規格。

本檔只涵蓋 T12 的**推斷**那一半（#9）：`infer_types(資料) -> {欄位路徑: 型別}`。
`draft_schema` 與人工指定型別不在 v0.2.0 的範圍（#9 明寫「只做這一層，不產生完整
JSON Schema」），所以不在這裡寫規格——那會是寫在還沒議定要落地的行為上。

核心層測試在無檔案系統、無 git、無網路下執行（CLAUDE.md）：資料直接以 Python 結構
餵進去。
"""

import datetime

from config_manager.core.inference import infer_types
from config_manager.core.parse import parse


def test_basic_scalar_types_are_inferred():
    types = infer_types({"name": "amr01", "count": 3, "ratio": 1.5, "flag": True})
    assert types == {
        "name": "string",
        "count": "int",
        "ratio": "float",
        "flag": "bool",
    }


def test_float_and_int_are_distinguished():
    # ROS 型別錯誤的來源：1.0 是浮點、1 是整數，混掉就是一次上線後才發現的錯（T12）。
    assert infer_types({"a": 1, "b": 1.0}) == {"a": "int", "b": "float"}


def test_boolean_is_not_read_as_int():
    # Python 的 bool 是 int 的子類別：判斷順序反了，True 會被記成 int。
    assert infer_types({"flag": True}) == {"flag": "bool"}


def test_nested_field_paths_are_dotted():
    types = infer_types({"outer": {"inner": 1}})
    assert types == {"outer": "dict", "outer.inner": "int"}


def test_list_element_type_is_inferred():
    types = infer_types({"items": [1, 2]})
    assert types == {"items": "list", "items[]": "int"}


def test_empty_list_element_type_is_unknown():
    # 空陣列推不出元素型別——標示為未知，不是猜一個（T12）。
    types = infer_types({"items": []})
    assert types == {"items": "list", "items[]": "unknown"}


def test_list_of_dicts_yields_paths_for_element_fields():
    types = infer_types({"servers": [{"host": "a", "port": 80}]})
    assert types == {
        "servers": "list",
        "servers[]": "dict",
        "servers[].host": "string",
        "servers[].port": "int",
    }


def test_null_value_is_recorded_as_null():
    # yaml 的 `key:` 與 json 的 null 都會落到這裡；記成 null，不是漏掉那個欄位。
    assert infer_types({"missing": None}) == {"missing": "null"}


def test_value_outside_the_recorded_type_set_is_unknown():
    # toml 的原生日期與 yaml 的時間戳不在 #9 的型別集合（string／int／float／bool／
    # list／dict）裡——記成 unknown，不是硬塞一個看起來合理的型別。
    assert infer_types({"when": datetime.date(2026, 1, 1)}) == {"when": "unknown"}


def test_types_are_inferred_from_a_parsed_yaml_document():
    # 實際餵進來的是 core/parse 的解析結果（ruamel 的容器與純量），不是純 dict——
    # 推斷必須讀得懂那些型別，否則這一層在真實資料上等於沒作用。
    parsed = parse("name: amr01\ncount: 3\nratio: 1.5\nflag: true\n", "yaml")
    assert infer_types(parsed.document) == {
        "name": "string",
        "count": "int",
        "ratio": "float",
        "flag": "bool",
    }


def test_types_are_inferred_from_a_parsed_toml_document():
    parsed = parse('name = "amr01"\ncount = 3\nratio = 1.5\nflag = true\n', "toml")
    assert infer_types(parsed.document) == {
        "name": "string",
        "count": "int",
        "ratio": "float",
        "flag": "bool",
    }
