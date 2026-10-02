"""core/validate — T3 第 1 層：語法、正規形式白名單、重複 key（#16）；第 2 層：結構描述（#39）。

第 1 層是**硬擋**（CONTEXT.md「驗證層級」）：能否解析，以及所有值是否符合正規形式白名單。
每個問題都要含行號與修正建議（三要素的「在哪裡」與「該怎麼改」；「檔案」由呼叫端補上，
核心層不知道路徑）——這本身是被測的行為。

依格式套有意義的規則（D3）：yaml 全套；toml／json／ini 只套「能否解析＋重複 key＋尾隨空白」，
縮排規則不套。重複 key 須**列出所有出現行號**，不是只報第二次。

第 2 層依 schema 檢查**解析後的資料**：型別、必填、拼錯的 key、數值範圍。同一份 schema 驗
yaml／toml／json 的等價內容，結果一致。每個問題除了行號與建議，另帶**欄位路徑**。

核心層：純函式，收字串、回問題清單，不碰檔案。
"""

from config_manager.core.schema_check import field_hints
from config_manager.core.validate import check


def _lines(problems):
    return sorted(p.line for p in problems)


def _messages(problems):
    return "\n".join(p.message for p in problems)


# ── 合法內容 ─────────────────────────────────────────────────────────────────


def test_canonical_yaml_passes_with_an_empty_list():
    text = "enabled: true\nport: 8080\nratio: 0.5\nname: amr01\nnothing: null\n"

    assert check(text, "yaml") == []


# ── 能否解析 ─────────────────────────────────────────────────────────────────


def test_a_syntax_error_is_a_problem_naming_the_line():
    problems = check("a: [1, 2\nb: 3\n", "yaml")

    assert problems and problems[0].line is not None


# ── 正規形式白名單：布林 ────────────────────────────────────────────────────


def test_yes_is_rejected_with_line_and_a_suggested_spelling():
    problems = check("enabled: yes\n", "yaml")

    assert _lines(problems) == [1]
    assert "true" in problems[0].suggestion


def test_on_off_and_single_letters_are_rejected_too():
    text = "a: on\nb: off\nc: y\nd: n\n"

    assert _lines(check(text, "yaml")) == [1, 2, 3, 4]


# ── 正規形式白名單：整數 ────────────────────────────────────────────────────


def test_leading_zero_hex_underscore_and_plus_integers_are_rejected():
    text = "a: 08\nb: 0x1F\nc: 1_000\nd: +5\n"

    assert _lines(check(text, "yaml")) == [1, 2, 3, 4]


# ── 正規形式白名單：浮點 ────────────────────────────────────────────────────


def test_scientific_bare_dot_inf_and_nan_floats_are_rejected():
    text = "a: 1e3\nb: .5\nc: 1.\nd: inf\ne: nan\n"

    assert _lines(check(text, "yaml")) == [1, 2, 3, 4, 5]


def test_a_trailing_zero_float_is_a_warning_not_an_error():
    # `1.10` 解析為 1.1、尾數退掉，易與版本號字串混淆——列出來讓人確認，但不硬擋。
    problems = check("version: 1.10\n", "yaml")

    assert [p.severity for p in problems] == ["warning"]


# ── 正規形式白名單：null ────────────────────────────────────────────────────


def test_null_spellings_other_than_null_are_rejected():
    text = "a: ~\nb: Null\nc: NULL\n"

    assert _lines(check(text, "yaml")) == [1, 2, 3]


# ── 縮排與尾隨空白（yaml）──────────────────────────────────────────────────


def test_a_tab_indent_is_rejected_naming_the_line():
    problems = check("root:\n\tchild: 1\n", "yaml")

    assert _lines(problems) == [2]


def test_an_indent_that_is_not_a_multiple_of_two_is_rejected():
    problems = check("root:\n   child: 1\n", "yaml")

    assert _lines(problems) == [2]


def test_trailing_whitespace_is_rejected_naming_the_line():
    problems = check("a: 1  \nb: 2\n", "yaml")

    assert _lines(problems) == [1]


# ── 重複 key：列出所有出現行號 ─────────────────────────────────────────────


def test_a_duplicate_yaml_key_lists_every_line_it_appears_on():
    text = "a: 1\nb: 2\na: 3\nc: 4\na: 5\n"
    problems = check(text, "yaml")

    assert len(problems) == 1
    assert problems[0].lines == (1, 3, 5)
    assert "a" in problems[0].message


def test_duplicate_detection_is_scoped_to_the_same_parent_in_yaml():
    # 不同父層底下同名的 key 不是重複。
    text = "x:\n  a: 1\ny:\n  a: 2\n"

    assert check(text, "yaml") == []


def test_the_same_key_in_different_list_items_is_not_a_duplicate():
    # 清單的每個項目各自是一個物件：`topic` 在兩個項目裡各出現一次，不是重複。先前只有項目的
    # 第一個鍵（緊接在 `- ` 後面那個）被算進項目的範圍，其餘的鍵落到清單的父層而互撞，
    # 合法的內容因此存不了草稿。
    text = "sources:\n  - name: scan\n    topic: /scan\n  - name: cam\n    topic: /cam\n"

    assert check(text, "yaml") == []


def test_list_items_written_at_the_parent_indent_are_scoped_the_same_way():
    # 清單項目與父鍵同一層縮排（`sources:` 下一行直接 `- `）也是合法 YAML。
    text = "sources:\n- name: scan\n  topic: /scan\n- name: cam\n  topic: /cam\nlast: 1\n"

    assert check(text, "yaml") == []


def test_a_key_repeated_inside_one_list_item_is_still_a_duplicate():
    text = "sources:\n  - name: scan\n    topic: /scan\n    topic: /other\n"

    problems = check(text, "yaml")

    assert len(problems) == 1
    assert problems[0].lines == (3, 4)


def test_a_list_item_whose_keys_start_on_the_next_line_has_its_own_scope():
    # `-` 單獨一行、鍵從下一行開始，同樣是一個獨立的項目。
    text = "sources:\n  -\n    topic: /scan\n  -\n    topic: /cam\n"

    assert check(text, "yaml") == []


def test_nested_keys_under_a_list_item_do_not_collide_across_items():
    text = (
        "sources:\n  - name: scan\n    qos:\n      depth: 5\n"
        "  - name: cam\n    qos:\n      depth: 10\n"
    )

    assert check(text, "yaml") == []


# ── 其他格式：解析＋重複 key＋尾隨空白；縮排規則不套（D3）─────────────────


def test_toml_duplicate_key_in_the_same_table_lists_every_line():
    text = 'a = 1\nb = 2\na = 3\n'
    problems = check(text, "toml")

    assert [p.lines for p in problems] == [(1, 3)]


def test_toml_indentation_is_not_judged():
    # toml 的縮排沒有語意，套 yaml 的 2 空白規則只會產生無意義的誤報。
    text = "[server]\n   port = 80\n\thost = 'x'\n"

    assert check(text, "toml") == []


def test_json_duplicate_key_in_the_same_object_lists_every_line():
    # json.loads 對重複 key 靜默取後者——那正是要在這裡大聲說出來的。
    text = '{\n  "a": 1,\n  "b": 2,\n  "a": 3\n}\n'
    problems = check(text, "json")

    assert [p.lines for p in problems] == [(2, 4)]


def test_json_duplicates_in_different_objects_are_not_duplicates():
    text = '{"x": {"a": 1}, "y": {"a": 2}}\n'

    assert check(text, "json") == []


def test_ini_duplicate_key_in_the_same_section_lists_every_line():
    text = "[main]\na = 1\nb = 2\na = 3\n"
    problems = check(text, "ini")

    assert [p.lines for p in problems] == [(2, 4)]


def test_ini_with_semicolon_comments_passes_the_first_layer():
    # 人工驗證 U06 的那份檔案：分號註解不是語法錯誤，也不算重複的鍵。
    text = "; 舊式 INI 設定\n[motion]\nmax_vel = 0.55\n; 第二個註解\nenabled = true\n"

    assert check(text, "ini") == []


def test_trailing_whitespace_is_rejected_in_every_format():
    assert _lines(check("a = 1 \n", "toml")) == [1]
    assert _lines(check('{"a": 1} \n', "json")) == [1]
    assert _lines(check("[s]\na = 1 \n", "ini")) == [2]


# ── 三要素 ───────────────────────────────────────────────────────────────────


def test_every_problem_carries_a_line_and_a_suggestion():
    text = "a: yes\nb: 08\nc: 1e3  \nd: ~\n"

    for problem in check(text, "yaml"):
        assert problem.line is not None
        assert problem.suggestion


# ── 第 2 層：結構描述（#39）──────────────────────────────────────────────────

_SCHEMA = {
    "type": "object",
    "properties": {
        "robot": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "max_vel": {"type": "number", "minimum": 0, "maximum": 2},
                "retries": {"type": "integer"},
                "mode": {"enum": ["auto", "manual"]},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}

_YAML_BAD = "robot:\n  max_vel: fast\n  retries: 3\n  name: amr01\n"
_TOML_BAD = '[robot]\nmax_vel = "fast"\nretries = 3\nname = "amr01"\n'
_JSON_BAD = (
    '{\n  "robot": {\n    "max_vel": "fast",\n'
    '    "retries": 3,\n    "name": "amr01"\n  }\n}\n'
)


def _found(problems):
    return [(p.path, p.line) for p in problems]


def test_content_that_fits_the_schema_passes():
    text = "robot:\n  name: amr01\n  max_vel: 0.8\n  retries: 3\n  mode: auto\n"

    assert check(text, "yaml", schema=_SCHEMA) == []


def test_without_a_schema_the_second_layer_does_not_run():
    assert check(_YAML_BAD, "yaml") == []


def test_a_value_of_the_wrong_type_names_the_field_and_its_line():
    problems = check(_YAML_BAD, "yaml", schema=_SCHEMA)

    assert _found(problems) == [("robot.max_vel", 2)]
    assert "robot.max_vel" in problems[0].message and "數字" in problems[0].message
    assert problems[0].suggestion


def test_the_same_schema_judges_yaml_toml_and_json_alike():
    # JSON Schema 驗的是解析後的資料結構，不是 JSON 檔：三種格式的等價內容，結果一致。
    results = [
        check(text, fmt, schema=_SCHEMA)
        for text, fmt in ((_YAML_BAD, "yaml"), (_TOML_BAD, "toml"), (_JSON_BAD, "json"))
    ]

    assert [[p.path for p in found] for found in results] == [["robot.max_vel"]] * 3
    assert len({found[0].message for found in results}) == 1
    assert [found[0].line for found in results] == [2, 2, 3]


def test_a_number_out_of_range_is_a_problem_stating_the_limit():
    text = "robot:\n  name: amr01\n  max_vel: 2.5\n"

    problems = check(text, "yaml", schema=_SCHEMA)

    assert _found(problems) == [("robot.max_vel", 3)]
    assert "2.5" in problems[0].message and "2" in problems[0].suggestion


def test_a_missing_required_field_is_named_at_its_parent():
    text = "robot:\n  max_vel: 0.8\n"

    problems = check(text, "yaml", schema=_SCHEMA)

    assert _found(problems) == [("robot.name", 1)]
    assert "必填" in problems[0].message


def test_a_misspelled_key_is_named_with_the_closest_known_key():
    text = "robot:\n  name: amr01\n  max_vell: 0.8\n"

    problems = check(text, "yaml", schema=_SCHEMA)

    assert _found(problems) == [("robot.max_vell", 3)]
    assert "max_vel" in problems[0].suggestion


def test_a_value_outside_the_enum_lists_the_allowed_values():
    text = "robot:\n  name: amr01\n  mode: turbo\n"

    problems = check(text, "yaml", schema=_SCHEMA)

    assert _found(problems) == [("robot.mode", 3)]
    assert "auto" in problems[0].suggestion and "manual" in problems[0].suggestion


def test_a_float_is_not_accepted_where_an_integer_is_required():
    # ROS 區分 1 與 1.0：整數參數寫成 3.0 會讓 node 啟動失敗，所以這裡比 JSON Schema 的預設嚴。
    text = "robot:\n  name: amr01\n  retries: 3.0\n"

    problems = check(text, "yaml", schema=_SCHEMA)

    assert _found(problems) == [("robot.retries", 3)]
    assert "整數" in problems[0].message


def test_an_integer_is_accepted_where_a_number_is_required():
    text = "robot:\n  name: amr01\n  max_vel: 1\n"

    assert check(text, "yaml", schema=_SCHEMA) == []


def test_a_problem_inside_a_list_element_points_at_that_element():
    schema = {
        "type": "object",
        "properties": {
            "servers": {
                "type": "array",
                "items": {"type": "object", "properties": {"port": {"type": "integer"}}},
            }
        },
    }
    yaml_text = "servers:\n  - host: a\n  - host: b\n    port: eighty\n"
    toml_text = (
        '[[servers]]\nhost = "a"\nport = 80\n\n'
        '[[servers]]\nhost = "b"\nport = "eighty"\n'
    )
    json_text = (
        '{"servers": [\n  {"host": "a", "port": 80},\n'
        '  {"host": "b",\n   "port": "eighty"}\n]}\n'
    )

    assert _found(check(yaml_text, "yaml", schema=schema)) == [("servers[1].port", 4)]
    assert _found(check(toml_text, "toml", schema=schema)) == [("servers[1].port", 7)]
    assert _found(check(json_text, "json", schema=schema)) == [("servers[1].port", 4)]


def test_a_key_containing_a_dot_is_escaped_in_the_path():
    # 路徑文法與欄位表的參數列一致：key 內的字面點跳脫成 `\.`，介面才對得到那一列。
    schema = {"type": "object", "properties": {"a.b": {"type": "integer"}}}

    problems = check('"a.b": text\n', "yaml", schema=schema)

    assert [p.path for p in problems] == ["a\\.b"]


def test_an_ini_value_is_located_by_section_and_key():
    schema = {
        "type": "object",
        "properties": {"net": {"type": "object", "properties": {"mode": {"enum": ["dhcp"]}}}},
    }

    problems = check("[net]\nmode = static\n", "ini", schema=schema)

    assert _found(problems) == [("net.mode", 2)]


def test_a_rule_this_layer_has_no_wording_for_is_still_reported():
    # schema 用了這裡沒有專屬訊息的關鍵字（如 pattern）：照樣回報、指名規則，不靜默放行。
    schema = {"type": "object", "properties": {"name": {"type": "string", "pattern": "^amr"}}}

    problems = check("name: robot7\n", "yaml", schema=schema)

    assert _found(problems) == [("name", 1)]
    assert "pattern" in problems[0].message


def test_syntax_that_does_not_parse_is_only_a_first_layer_problem():
    # 解析不下去就沒有值可以驗——第 2 層不跑，回第 1 層的語法問題。
    problems = check("robot: [unclosed\n", "yaml", schema=_SCHEMA)

    assert len(problems) == 1 and problems[0].path is None


def test_raw_is_never_checked_against_a_schema():
    assert check("anything at all", "raw", schema=_SCHEMA) == []


def test_every_schema_problem_carries_a_path_a_line_and_a_suggestion():
    text = "robot:\n  max_vel: fast\n  retries: 3.5\n  mode: turbo\n  extra: 1\n"

    problems = check(text, "yaml", schema=_SCHEMA)

    # 五種問題各一：型別、整數、列舉、多出來的 key、缺必填。
    assert sorted(p.path for p in problems) == [
        "robot.extra", "robot.max_vel", "robot.mode", "robot.name", "robot.retries",
    ]
    assert all(p.line and p.suggestion for p in problems)


# ── schema 給介面的提示（#40）────────────────────────────────────────────────
# 欄位表依 schema 套範圍、步進、列舉選項與說明。保證仍在後端（第 2 層）；這份提示只讓介面在
# 輸入當下就說得出問題。鍵與 `infer_types` 同一套路徑文法，介面才對得到那一列。


def test_hints_carry_range_step_choices_and_description_per_field():
    schema = {
        "type": "object",
        "properties": {
            "max_vel": {"type": "number", "minimum": 0, "maximum": 2, "multipleOf": 0.1,
                        "description": "最大線速度（m/s）"},
            "mode": {"enum": ["auto", "manual"]},
            "ratio": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
        },
    }

    assert field_hints(schema) == {
        "max_vel": {"minimum": 0, "maximum": 2, "multipleOf": 0.1,
                    "description": "最大線速度（m/s）"},
        "mode": {"enum": ["auto", "manual"]},
        "ratio": {"exclusiveMinimum": 0, "exclusiveMaximum": 1},
    }


def test_hints_follow_nested_objects_and_list_elements():
    schema = {
        "type": "object",
        "properties": {
            "robot": {"type": "object", "properties": {"speed": {"maximum": 3}}},
            "servers": {
                "type": "array",
                "items": {"type": "object", "properties": {"port": {"minimum": 1}}},
            },
            "ids": {"type": "array", "items": {"minimum": 0}},
        },
    }

    assert field_hints(schema) == {
        "robot.speed": {"maximum": 3},
        "servers[].port": {"minimum": 1},
        "ids[]": {"minimum": 0},
    }


def test_fields_without_any_hint_are_not_listed():
    # 只鎖型別的骨架沒有東西要提示——回空的，不是每個欄位一個空殼。
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}}

    assert field_hints(schema) == {}


def test_a_key_containing_a_dot_is_escaped_in_the_hint_path():
    schema = {"type": "object", "properties": {"a.b": {"maximum": 1}}}

    assert field_hints(schema) == {"a\\.b": {"maximum": 1}}
