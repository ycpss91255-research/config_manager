"""T6 — 格式解析與原樣寫回。core/parse 的單元規格。

核心層測試在無檔案系統、無 git、無網路下執行（CLAUDE.md）：待解析的 text 直接以
字串餵進去，不碰磁碟。
"""

import pytest

from config_manager.core.errors import SyntaxParse, UnsupportedFormat
from config_manager.core.parse import Parsed, dump, parse


def test_raw_format_round_trips_byte_identical():
    text = "任意內容: [這不是, 合法 yaml\n  # 不平衡\n"
    assert dump(parse(text, "raw")) == text


def test_unsupported_format_raises_named_error():
    with pytest.raises(UnsupportedFormat):
        parse("x = 1", "xml")


def test_dump_of_unsupported_format_raises_named_error():
    with pytest.raises(UnsupportedFormat):
        dump(Parsed(fmt="xml", document="x"))


def test_yaml_round_trips_byte_identical_with_comments_and_quotes():
    text = '# ROS 2 參數\nrobot_name: "amr01"\nmax_speed: 1.5\nsensors:\n- lidar\n- camera\n'
    assert dump(parse(text, "yaml")) == text


def test_yaml_syntax_error_raises_with_line_number():
    with pytest.raises(SyntaxParse) as caught:
        parse("key: [未閉合\n", "yaml")
    assert caught.value.line is not None


def test_yaml_1_2_keeps_yes_as_string_not_boolean():
    # 1.1 會把 `yes` 當成布林 True，1.2 不會——明確指定 1.2 迴避這個陷阱（#8）。
    parsed = parse("flag: yes\n", "yaml")
    assert parsed.document["flag"] == "yes"


def test_toml_round_trips_byte_identical_with_comment():
    text = '# 設定\nname = "amr01"\ncount = 3\n'
    assert dump(parse(text, "toml")) == text


def test_toml_syntax_error_raises_with_line_number():
    with pytest.raises(SyntaxParse) as caught:
        parse("[未閉合\n", "toml")
    assert caught.value.line is not None


def test_json_round_trips_byte_identical():
    text = '{\n  "name": "amr01",\n  "count": 3\n}\n'
    assert dump(parse(text, "json")) == text


def test_json_syntax_error_raises_with_line_number():
    with pytest.raises(SyntaxParse) as caught:
        parse('{"name": }\n', "json")
    assert caught.value.line is not None


def test_ini_round_trips_byte_identical_with_comment_and_section():
    text = "# 註解\n[section]\nkey = value\nnum = 3\n"
    assert dump(parse(text, "ini")) == text


def test_ini_syntax_error_raises_with_line_number():
    with pytest.raises(SyntaxParse) as caught:
        parse("[未閉合\nkey = value\n", "ini")
    assert caught.value.line is not None


def test_parser_is_chosen_by_format_argument_not_by_content():
    # 同一份文字，format 欄位決定用哪一支解析器——不由副檔名或內容推斷（#8）。
    text = "a: 1\n"
    assert isinstance(parse(text, "raw").document, str)
    assert parse(text, "yaml").document["a"] == 1
