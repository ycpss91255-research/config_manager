"""T3 第 3 層 — 跨欄位規則（#41）。core/rules 的單元規格。

型別正確但數值不合理的 config 比型別錯誤的危險：型別錯誤讓 node 啟動失敗、立刻暴露；
`inflation_radius` 設太小要到撞車才發現。第 3 層檢查欄位之間合不合理——**只警告、不硬擋**
（可填理由略過，#42），現場異常時介面才不會變成路障。

規則的寫法（#41 動工前定案）：一條規則＝欄位、六種固定比較方式之一、另一個欄位或固定值；
另有「值必須出現在另一份 config 的某個欄位裡」的跨 config 對照。規則檔本身寫錯要被指出來，
不默默當成沒有那條規則。

核心層：純函式。規則檔的文字與對照用的值都由呼叫端傳進來，不碰檔案。
"""

import pytest

from config_manager.core.errors import RulesInvalid
from config_manager.core.problem import WARNING
from config_manager.core.rules import Rules, check_rules, parse_rules
from config_manager.core.validate import check

_RULES = """\
[[rules]]
id = "min-below-max"
left = "min_vel"
op = "<"
right = "max_vel"
message = "最小速度要小於最大速度"

[[rules]]
id = "inflation-covers-robot"
left = "costmap.inflation_radius"
op = ">"
right = "robot_radius"

[[rules]]
id = "retries-capped"
left = "retries"
op = "<="
value = 5
"""
_GOOD = (
    "min_vel: 0.1\nmax_vel: 0.8\nrobot_radius: 0.3\nretries: 3\n"
    "costmap:\n  inflation_radius: 0.55\n"
)


def _rules(text=_RULES, known=None, faults=()):
    return Rules(parse_rules(text), known or {}, tuple(faults))


def _found(problems):
    return [(p.rule, p.path, p.line) for p in problems]


def test_content_that_satisfies_every_rule_yields_nothing():
    assert check_rules(_GOOD, "yaml", _rules()) == []


def test_two_fields_in_the_wrong_order_are_a_warning_naming_both():
    text = _GOOD.replace("min_vel: 0.1", "min_vel: 0.9")

    problems = check_rules(text, "yaml", _rules())

    assert _found(problems) == [("min-below-max", "min_vel", 1)]
    problem = problems[0]
    assert problem.severity == WARNING
    # 說明是哪兩個欄位衝突、各是多少，並帶上規則自己的說明。
    assert all(part in problem.message for part in ("min_vel", "0.9", "max_vel", "0.8"))
    assert "最小速度要小於最大速度" in problem.message and problem.suggestion


def test_a_nested_field_is_compared_and_located_on_its_own_line():
    text = _GOOD.replace("inflation_radius: 0.55", "inflation_radius: 0.2")

    assert _found(check_rules(text, "yaml", _rules())) == [
        ("inflation-covers-robot", "costmap.inflation_radius", 6)
    ]


def test_a_field_is_compared_against_a_fixed_value():
    text = _GOOD.replace("retries: 3", "retries: 9")

    problems = check_rules(text, "yaml", _rules())

    assert _found(problems) == [("retries-capped", "retries", 4)]
    assert "5" in problems[0].message


@pytest.mark.parametrize(
    ("op", "left", "holds"),
    [("<", 1, True), ("<", 2, False), ("<=", 2, True), (">", 3, True), (">", 2, False),
     (">=", 2, True), ("==", 2, True), ("==", 3, False), ("!=", 3, True), ("!=", 2, False)],
)
def test_each_of_the_six_comparisons_means_what_it_says(op, left, holds):
    rules = _rules(f'[[rules]]\nid = "r"\nleft = "a"\nop = "{op}"\nvalue = 2\n')

    assert (check_rules(f"a: {left}\n", "yaml", rules) == []) is holds


def test_the_same_rules_judge_yaml_toml_and_json_alike():
    # 規則作用於解析後的資料，與原始格式無關（設計 §3.4）。
    rules = _rules('[[rules]]\nid = "r"\nleft = "lo"\nop = "<"\nright = "hi"\n')
    texts = (("lo: 5\nhi: 1\n", "yaml"), ("lo = 5\nhi = 1\n", "toml"),
             ('{"lo": 5,\n "hi": 1}\n', "json"))

    results = [check_rules(text, fmt, rules) for text, fmt in texts]

    assert [[p.rule for p in found] for found in results] == [["r"]] * 3
    assert len({found[0].message for found in results}) == 1


def test_a_value_must_appear_in_the_referenced_field_of_another_config():
    # 外部一致性（#41 定案為跨 config 對照）：這裡用到的 frame 必須是另一份 config 定義過的。
    text = (
        '[[rules]]\nid = "frame-is-defined"\nleft = "robot_frame"\n'
        'in = { config = "bbbbbbb2", field = "frames" }\n'
    )
    rules = _rules(text, known={("bbbbbbb2", "frames"): ("base_link", "odom")})

    assert check_rules("robot_frame: odom\n", "yaml", rules) == []
    problems = check_rules("robot_frame: base_lnk\n", "yaml", rules)

    assert _found(problems) == [("frame-is-defined", "robot_frame", 1)]
    assert "base_lnk" in problems[0].message and "base_link" in problems[0].suggestion


def test_a_rule_whose_field_is_not_in_the_content_says_so_instead_of_passing():
    # 規則用到的欄位不在這份 config 裡：沒辦法檢查——說出來，不當成通過（不變式 2）。
    problems = check_rules("max_vel: 0.8\n", "yaml", _rules())

    assert ("min-below-max", "min_vel") in [(p.rule, p.path) for p in problems]
    assert "不在" in next(p.message for p in problems if p.rule == "min-below-max")


def test_values_that_cannot_be_ordered_are_reported_not_raised():
    rules = _rules('[[rules]]\nid = "r"\nleft = "a"\nop = "<"\nright = "b"\n')

    problems = check_rules("a: fast\nb: 3\n", "yaml", rules)

    assert [p.rule for p in problems] == ["r"] and "比不了" in problems[0].message


def test_a_fault_in_the_rules_file_surfaces_as_a_warning_on_every_check():
    # 規則檔壞了（或對照的 config 讀不到）：跨欄位檢查沒跑完，這件事本身要讓人看到。
    rules = Rules(parse_rules(""), {}, ("第 2 條（min-below-max）的 op「<<」不是允許的比較方式",))

    problems = check_rules(_GOOD, "yaml", rules)

    assert [(p.rule, p.severity) for p in problems] == [("rules-file", WARNING)]
    assert "<<" in problems[0].message


def test_check_adds_the_third_layer_after_the_first_two():
    text = _GOOD.replace("min_vel: 0.1", "min_vel: 0.9")

    problems = check(text, "yaml", rules=_rules())

    assert [(p.rule, p.severity) for p in problems] == [("min-below-max", WARNING)]
    assert check(text, "yaml") == []  # 沒給規則就不跑第 3 層


# ── 規則檔本身的檢查（保護一：寫錯要被指出來）──────────────────────────────────

_BASE = '[[rules]]\nid = "r"\nleft = "a"\nop = "<"\n'


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ('[[rules]]\nleft = "a"\nop = "<"\nright = "b"\n', "id"),  # 沒有代號
        ('[[rules]]\nid = "r"\nop = "<"\nright = "b"\n', "left"),  # 沒有左邊的欄位
        ('[[rules]]\nid = "r"\nleft = "a"\nop = "<<"\nright = "b"\n', "<<"),  # 不認得的比較方式
        ('[[rules]]\nid = "r"\nleft = "a"\nop = "<"\n', "right"),  # 沒有比較對象
        (_BASE + 'right = "b"\nvalue = 1\n', "value"),  # 兩個都給
        (_BASE + 'right = "b"\ntypo = 1\n', "typo"),  # 不認得的鍵
        ('[[rules]]\nid = "bad id"\nleft = "a"\nop = "<"\nright = "b"\n', "bad id"),  # 代號含空白
        ('[[rules]]\nid = "r"\nleft = "a"\nin = { config = "x" }\n', "field"),  # 對照缺欄位
        ('[[rule]]\nid = "r"\n', "rule"),  # 頂層鍵打錯
        ("[[rules]]\nid = \n", "TOML"),  # 語法錯
    ],
)
def test_a_malformed_rules_file_is_refused_saying_which_rule_and_what(text, said):
    with pytest.raises(RulesInvalid, match=said) as caught:
        parse_rules(text)

    assert "下一步" in str(caught.value)


def test_two_rules_with_the_same_id_are_refused():
    # 代號記在略過的紀錄裡（override(<代號>)）——重複就分不出是哪一條被略過。
    text = (
        '[[rules]]\nid = "r"\nleft = "a"\nop = "<"\nright = "b"\n'
        '[[rules]]\nid = "r"\nleft = "c"\nop = "<"\nright = "d"\n'
    )

    with pytest.raises(RulesInvalid, match="重複"):
        parse_rules(text)


def test_an_empty_rules_file_has_no_rules():
    assert check_rules(_GOOD, "yaml", _rules("")) == []
