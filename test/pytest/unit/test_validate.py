"""core/validate — T3 第 1 層：語法、正規形式白名單、重複 key（#16）。

第 1 層是**硬擋**（CONTEXT.md「驗證層級」）：能否解析，以及所有值是否符合正規形式白名單。
每個問題都要含行號與修正建議（三要素的「在哪裡」與「該怎麼改」；「檔案」由呼叫端補上，
核心層不知道路徑）——這本身是被測的行為。

依格式套有意義的規則（D3）：yaml 全套；toml／json／ini 只套「能否解析＋重複 key＋尾隨空白」，
縮排規則不套。重複 key 須**列出所有出現行號**，不是只報第二次。

核心層：純函式，收字串、回問題清單，不碰檔案。
"""

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
