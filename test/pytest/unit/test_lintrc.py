"""T3 第 1 層的規則可配置——`.lintrc.toml`（#43，設計 §6.4）。core/lintrc 的單元規格。

§6.3 的正規形式白名單是目前版本的規則，不是永久定義。規則抽到 config-repo 的 `.lintrc.toml`、
隨 config 版控：`[yaml]`／`[yaml.overrides]`／`[toml]` 等分節；個別檔案的豁免要填理由。
**沒有這個檔就是預設值**（＝§6.3 的全套），所以既有的 repo 行為不變。

`duplicate_key` 只有 `error`｜`warn`，**沒有 `ignore`**：重複 key 的靜默失效是本系統要解決的
核心問題之一，允許關掉會使那個保證失效。

核心層：純函式，收規則檔的文字與 config 的文字，不碰檔案。
"""

import pytest

from config_manager.core.errors import LintrcInvalid
from config_manager.core.lintrc import DEFAULT, default_text, parse_lintrc, rules_for
from config_manager.core.problem import ERROR, WARNING
from config_manager.core.validate import check

_BOOL_YAML = "enabled: yes\n"
_OCTAL_YAML = "mode: 0755\n"
_SCI_YAML = "big: 1e3\n"
_DUP_YAML = "a: 1\na: 2\n"
_TRAIL_YAML = "a: 1   \n"
_INDENT_YAML = "a:\n   b: 1\n"


def _rules(text, fmt="yaml", source="files/x.yaml"):
    return rules_for(parse_lintrc(text), fmt, source)


def _severities(text, fmt="yaml", lintrc=None):
    return [p.severity for p in check(text, fmt, lintrc=lintrc)]


# ── 預設值＝現在的行為 ───────────────────────────────────────────────────────


def test_the_default_rules_match_the_canonical_form_whitelist():
    for text in (_BOOL_YAML, _OCTAL_YAML, _SCI_YAML, _DUP_YAML, _TRAIL_YAML, _INDENT_YAML):
        assert _severities(text) == [ERROR], text
        assert _severities(text, lintrc=rules_for(DEFAULT, "yaml", "files/x.yaml")) == [ERROR], text


def test_the_default_text_parses_back_to_the_defaults():
    # 文件裡附的範例檔要真的是預設值——兩份漂移就會有人照範例改卻看不到差別。
    assert parse_lintrc(default_text()) == DEFAULT


def test_an_empty_file_is_the_defaults_too():
    assert parse_lintrc("") == DEFAULT


# ── 每個設定都真的改變驗證行為 ───────────────────────────────────────────────


def test_accepting_more_boolean_spellings_stops_rejecting_them():
    rules = _rules('[yaml]\nboolean_literals = ["true", "false", "yes", "no"]\n')

    assert check(_BOOL_YAML, "yaml", lintrc=rules) == []
    assert _severities("enabled: on\n", lintrc=rules) == [ERROR]  # 沒列到的仍擋


def test_any_boolean_spelling_switches_the_check_off():
    rules = _rules('[yaml]\nboolean_literals = "any"\n')

    assert check("a: on\nb: True\n", "yaml", lintrc=rules) == []


def test_capitalised_true_is_not_a_canonical_boolean_by_default():
    # 設計 §6.3：True／TRUE 也在拒絕的那一欄——YAML 1.2 讀成布林，但不是白名單上的寫法。
    assert _severities("a: True\n") == [ERROR]


def test_allowing_the_octal_prefix_accepts_leading_zeros_and_0o():
    rules = _rules("[yaml]\nallow_octal_prefix = true\n")

    assert check("mode: 0755\nalt: 0o17\n", "yaml", lintrc=rules) == []
    assert _severities("n: 1_000\n", lintrc=rules) == [ERROR]  # 底線分隔沒有開關，照擋


def test_allowing_scientific_notation_accepts_1e3():
    rules = _rules("[yaml]\nallow_scientific_notation = true\n")

    assert check(_SCI_YAML, "yaml", lintrc=rules) == []
    assert _severities("x: .5\n", lintrc=rules) == [ERROR]  # 省略整數部分沒有開關，照擋


def test_duplicate_keys_can_be_downgraded_to_a_warning_but_not_ignored():
    warned = _rules('[yaml]\nduplicate_key = "warn"\n')

    problems = check(_DUP_YAML, "yaml", lintrc=warned)

    assert [p.severity for p in problems] == [WARNING] and "重複" in problems[0].message
    with pytest.raises(LintrcInvalid, match="ignore"):
        parse_lintrc('[yaml]\nduplicate_key = "ignore"\n')


def test_trailing_whitespace_can_be_a_warning_or_ignored():
    assert _severities(_TRAIL_YAML, lintrc=_rules('[yaml]\ntrailing_whitespace = "warn"\n')) == [
        WARNING
    ]
    ignored = _rules('[yaml]\ntrailing_whitespace = "ignore"\n')
    assert check(_TRAIL_YAML, "yaml", lintrc=ignored) == []


def test_the_indent_width_is_configurable_and_tabs_stay_forbidden():
    rules = _rules("[yaml]\nindent = { style = \"space\", width = 3 }\n")

    assert check(_INDENT_YAML, "yaml", lintrc=rules) == []
    assert _severities("a:\n  b: 1\n", lintrc=rules) == [ERROR]
    assert _severities("a:\n\tb: 1\n", lintrc=rules) == [ERROR]


def test_toml_and_json_only_take_the_keys_that_mean_something_for_them():
    rules = _rules('[toml]\nduplicate_key = "warn"\n', fmt="toml")

    assert _severities("a = 1\na = 2\n", "toml", lintrc=rules) == [WARNING]
    with pytest.raises(LintrcInvalid, match="boolean_literals"):
        parse_lintrc('[toml]\nboolean_literals = "any"\n')


# ── 個別檔案的豁免：要填理由 ─────────────────────────────────────────────────


def test_an_override_applies_to_that_source_only_and_needs_a_reason():
    text = (
        '[yaml]\nduplicate_key = "error"\n\n'
        '[yaml.overrides]\n"files/legacy/old.yaml" = { boolean_literals = "any", '
        'reason = "第三方產生，暫不修改" }\n'
    )
    lintrc = parse_lintrc(text)

    exempt = rules_for(lintrc, "yaml", "files/legacy/old.yaml")
    assert check(_BOOL_YAML, "yaml", lintrc=exempt) == []
    assert _severities(_BOOL_YAML, lintrc=rules_for(lintrc, "yaml", "files/other.yaml")) == [ERROR]
    assert lintrc.overrides["files/legacy/old.yaml"].reason == "第三方產生，暫不修改"


def test_an_override_without_a_reason_is_refused():
    with pytest.raises(LintrcInvalid, match="reason"):
        parse_lintrc('[yaml.overrides]\n"files/a.yaml" = { boolean_literals = "any" }\n')


# ── 規則檔本身寫錯要被指出來 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("[yaml]\nduplicate_key = \"maybe\"\n", "duplicate_key"),
        ("[yaml]\ntypo = 1\n", "typo"),
        ("[yaml]\nyaml_version = \"1.1\"\n", "yaml_version"),  # 只支援 1.2（§6.3 的 parser 設定）
        ("[yaml]\nindent = { style = \"tab\", width = 2 }\n", "style"),
        ("[yaml]\nindent = { style = \"space\", width = 0 }\n", "width"),
        ("[yaml]\nboolean_literals = [\"true\", 1]\n", "boolean_literals"),
        ("[cfg]\nduplicate_key = \"error\"\n", "cfg"),
        ("[yaml\n", "TOML"),
    ],
)
def test_a_malformed_lintrc_is_refused_saying_what_is_wrong(text, said):
    with pytest.raises(LintrcInvalid, match=said) as caught:
        parse_lintrc(text)

    assert "下一步" in str(caught.value)
