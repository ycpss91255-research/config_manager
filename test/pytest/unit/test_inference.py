"""T12 — 型別推斷與歧義偵測。core/inference 的單元規格。

涵蓋 T12 的兩半：`infer_types(資料) -> {欄位路徑: 型別}`（#9），以及
`find_ambiguous(原文, format) -> [歧義]`（#10）。

`draft_schema` 與人工指定型別**不**在這裡：它們不在 v0.2.0 的範圍（#9 明寫「只做這
一層，不產生完整 JSON Schema」），寫規格會是寫在還沒議定要落地的行為上。

核心層測試在無檔案系統、無 git、無網路下執行（CLAUDE.md）：資料直接以 Python 結構
餵進去。
"""

import datetime

from config_manager.core.inference import find_ambiguous, infer_types
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


def test_a_key_containing_a_dot_does_not_collide_with_a_nested_path():
    # key 本身含 `.` 會與巢狀路徑撞號、靜默覆蓋、遺失型別，且結果隨欄位順序變（#219，
    # 不變式 2）。跳脫含點的 key（`a\.b`）後，它與巢狀 `a.b` 是不同鍵，兩個型別都在。
    types = infer_types({"a.b": "hello", "a": {"b": 123}})
    assert types == {"a\\.b": "string", "a": "dict", "a.b": "int"}


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


# ── 歧義偵測（T12 的 find_ambiguous，#10）─────────────────────────────────────


def test_bool_word_is_reported_with_its_line_and_value():
    # `no` 在 YAML 1.1 是布林 false、1.2 是字串 "no"——兩種讀法都成立，所以要問人。
    found = find_ambiguous("name: amr01\nflag: no\n", "yaml")
    assert [(a.line, a.value) for a in found] == [(2, "no")]


def test_leading_zero_number_is_reported():
    # 0755 可能是八進位、可能是十進位，也可能根本是檔案權限字串。
    found = find_ambiguous("mode: 0755\n", "yaml")
    assert [(a.line, a.value) for a in found] == [(1, "0755")]


def test_trailing_zero_decimal_is_reported():
    # 1.10 讀成浮點數之後，尾數那個 0 就消失了。
    found = find_ambiguous("ratio: 1.10\n", "yaml")
    assert [(a.line, a.value) for a in found] == [(1, "1.10")]


def test_list_item_value_is_reported():
    found = find_ambiguous("flags:\n  - no\n", "yaml")
    assert [(a.line, a.value) for a in found] == [(2, "no")]


def test_quoted_value_is_not_ambiguous():
    # 已經加引號就沒有歧義——作者已經表明它是字串。
    assert find_ambiguous('flag: "no"\n', "yaml") == []


def test_unambiguous_values_are_not_reported():
    assert find_ambiguous("name: amr01\ncount: 3\nflag: true\n", "yaml") == []


def test_comment_line_is_not_scanned():
    assert find_ambiguous("# no\nname: amr01\n", "yaml") == []


def test_inline_comment_is_not_scanned():
    assert find_ambiguous("name: amr01  # no\n", "yaml") == []


def test_the_possible_readings_name_both_bool_and_string():
    # 光說「這個值有歧義」不夠——要說得出它可以被讀成哪幾種，人才決定得了。
    readings = " ".join(find_ambiguous("flag: no\n", "yaml")[0].readings)
    assert "布林" in readings and "字串" in readings


def test_reported_value_is_verbatim_not_corrected():
    # 不自動修正：回報的是原樣的 `no`，不是改寫後的 `"no"`（#10）。
    found = find_ambiguous("flag: no\n", "yaml")
    assert found[0].value == "no"


def test_formats_with_explicit_typing_have_no_ambiguity():
    # json／toml 的型別由寫法決定，ini 全部是字串——都沒有這類歧義。
    assert find_ambiguous('{"flag": false}\n', "json") == []


def test_block_scalar_continuation_is_not_treated_as_a_value():
    # 區塊純量的續行既沒有鍵也不是清單項目——不當成值，否則多行字串裡的 `no`
    # 會被誤報成歧義。
    assert find_ambiguous("note: |\n  no\n", "yaml") == []


def test_block_scalar_content_shaped_like_key_value_is_not_reported():
    # `script: |` 之下的字面文字長得像 `key: 值`（如 shell 腳本），不是 YAML 的鍵值，
    # 不該把裡面的 yes／no 誤報成歧義（#219）。
    assert find_ambiguous("script: |\n  run: yes\n  retry: no\n", "yaml") == []


def test_block_scalar_content_shaped_like_a_list_item_is_not_reported():
    # `- 值` 形狀的區塊內文也不誤報（#219 本文明列的另一半）。
    assert find_ambiguous("script: |\n  - yes\n  - no\n", "yaml") == []


def test_block_scalar_content_after_a_blank_line_is_not_reported():
    # 區塊純量常含空行；空行（縮排 0）不該提早中止跳過、讓其後內文又被誤報（#219）。
    assert find_ambiguous("script: |\n  run: yes\n\n  retry: no\n", "yaml") == []


def test_a_value_after_the_block_scalar_ends_is_still_reported():
    # 區塊結束後（縮排回到標頭層級以下）真正的 key: 值仍要偵測——別過度跳過（#219）。
    found = find_ambiguous("script: |\n  run: yes\nflag: no\n", "yaml")
    assert [(a.line, a.value) for a in found] == [(3, "no")]
