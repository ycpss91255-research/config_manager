"""T6 — 格式解析與原樣寫回。core/parse 的單元規格。

核心層測試在無檔案系統、無 git、無網路下執行（CLAUDE.md）：待解析的 text 直接以
字串餵進去，不碰磁碟。
"""

import pytest

from config_manager.core.errors import SyntaxParse, UnknownPath, UnsupportedFormat
from config_manager.core.parse import Parsed, dump, parse, set_value, values


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


# ── #217：INI 逐位元組往返（存原文，比照 json；configobj 只用於驗語法／取值）───────


def test_ini_round_trips_byte_identical_without_a_trailing_newline():
    # configobj 無條件補尾換行；存原文才保住「原檔沒有尾換行」（#217）。
    text = "a = 1"
    assert dump(parse(text, "ini")) == text


def test_ini_round_trips_byte_identical_for_an_empty_file():
    # 空檔不該變成 "\n"（#217）。
    assert dump(parse("", "ini")) == ""


def test_ini_round_trips_byte_identical_keeping_quotes():
    # configobj 去引號（值語意同、位元組不同）；存原文保住引號樣式（#217）。
    text = 'a = "hello world"\n'
    assert dump(parse(text, "ini")) == text


def test_ini_round_trips_byte_identical_keeping_crlf():
    # configobj 把 CRLF 壓成 LF；存原文保住換行樣式（#217）。
    text = "a = 1\r\n"
    assert dump(parse(text, "ini")) == text


def test_ini_round_trips_byte_identical_keeping_trailing_whitespace():
    # configobj 吃掉尾隨空白；存原文保住（#217）。
    text = "a = 1  \n"
    assert dump(parse(text, "ini")) == text


_INI_WITH_SEMICOLONS = (
    "; 舊式 INI 設定（驗證用範例）\n[motion]\nmax_vel = 0.55\n  ; 縮排的分號註解\nenabled = true\n"
    "\n[net]\n# 井號註解\nhost = 192.168.1.10\n"
)


def test_ini_semicolon_comment_lines_are_comments_not_syntax_errors():
    # 人工驗證 U06：`;` 開頭的整行註解是 INI 最常見的寫法（Windows 與多數工具的預設），
    # configobj 預設只認 `#`。這類檔案要解析得過、值照樣取得到，註解不變成鍵。
    data = values(parse(_INI_WITH_SEMICOLONS, "ini"))

    assert data == {
        "motion": {"max_vel": "0.55", "enabled": "true"},
        "net": {"host": "192.168.1.10"},
    }


def test_ini_with_semicolon_comments_round_trips_byte_identical():
    # 原始內容保留：`;` 不會被改寫成 `#`。
    assert dump(parse(_INI_WITH_SEMICOLONS, "ini")) == _INI_WITH_SEMICOLONS


def test_setting_a_value_keeps_the_semicolon_comment_lines_untouched():
    parsed = parse(_INI_WITH_SEMICOLONS, "ini")

    set_value(parsed, "motion.max_vel", "0.8")

    assert dump(parsed) == _INI_WITH_SEMICOLONS.replace("max_vel = 0.55", "max_vel = 0.8")


def test_an_ini_syntax_error_after_a_semicolon_comment_reports_the_right_line():
    # 把 `;` 當註解不能弄亂行號。
    broken_line = 2
    with pytest.raises(SyntaxParse) as exc:
        parse("; 註解\n[未閉合\nkey = value\n", "ini")

    assert exc.value.line == broken_line


def test_ini_values_are_extractable_for_type_inference():
    # 存原文之後，型別推斷需要的值仍取得出來（比照 json：values() 再 parse 一次）。
    data = values(parse("[s]\nn = 3\nname = amr01\n", "ini"))

    assert data["s"]["n"] == "3" and data["s"]["name"] == "amr01"


# ── 改動一個值後原樣寫回（T6 第二列，#17）──────────────────────────────────
# 路徑文法與 core/inference 的欄位路徑一致：以 `.` 相接、鍵內字面的點寫成 `\.`；清單元素
# 以 `[索引]` 指定（inference 用無索引的 `[]` 表「元素型別」，編輯要指到哪一個）。


def test_setting_a_yaml_value_keeps_comments_quotes_and_order():
    text = "# top\nspeed: 1  # fast\nname: 'x'\n"
    parsed = parse(text, "yaml")

    set_value(parsed, "speed", 2.0)

    # 註解文字、引號樣式、鍵順序、其餘各行逐位元組保留。唯一的正規化：被改那一行的行內
    # 註解前空白由 ruamel 重排成一個（重建值時不保留原本的欄位對齊）——記在 T6 第二列。
    assert dump(parsed) == "# top\nspeed: 2.0 # fast\nname: 'x'\n"


def test_setting_a_double_writes_a_decimal_point_not_an_integer():
    # ROS 2 最常見的型別錯誤：1 與 1.0 是不同型別。改 double 後寫出的一定帶小數點。
    parsed = parse("ratio: 0.5\n", "yaml")

    set_value(parsed, "ratio", 1.0)

    assert dump(parsed) == "ratio: 1.0\n"


def test_setting_an_integer_on_a_double_field_still_writes_a_decimal_point():
    # 介面經 JSON 送來的 5.0 到後端已是 int 5；型別由原值決定——原值是 double 就寫 5.0，
    # 不因新值的寫法把欄位降成 int（#21）。yaml／toml／json 三種都要守。
    outputs = []
    samples = (("ratio: 0.5\n", "yaml"), ("ratio = 0.5\n", "toml"), ('{"ratio": 0.5}', "json"))
    for text, fmt in samples:
        parsed = parse(text, fmt)
        set_value(parsed, "ratio", 5)
        outputs.append(dump(parsed))

    assert outputs == ["ratio: 5.0\n", "ratio = 5.0\n", '{"ratio": 5.0}']


def test_setting_a_nested_yaml_value_by_dotted_path_and_list_index():
    parsed = parse("a:\n  b:\n    - 1\n    - 2\n", "yaml")

    set_value(parsed, "a.b[1]", 5)

    assert dump(parsed) == "a:\n  b:\n    - 1\n    - 5\n"


def test_a_key_containing_a_literal_dot_is_addressed_with_an_escaped_dot():
    parsed = parse("x.y: 1\n", "yaml")

    set_value(parsed, "x\\.y", 2)

    assert dump(parsed) == "x.y: 2\n"


def test_setting_a_toml_value_keeps_comments_and_sections():
    text = "# c\nspeed = 1 # fast\n\n[s]\nname = 'x'\n"
    parsed = parse(text, "toml")

    set_value(parsed, "speed", 2.0)

    assert dump(parsed) == "# c\nspeed = 2.0 # fast\n\n[s]\nname = 'x'\n"


def test_setting_a_json_value_keeps_the_original_indent_and_key_order():
    # json 沒有註解與引號樣式可失；偵測原檔縮排後重新序列化，保 key 順序（#17 的 D2）。
    text = '{\n    "speed": 1,\n    "name": "x"\n}\n'
    parsed = parse(text, "json")

    set_value(parsed, "speed", 2.0)

    assert dump(parsed) == '{\n    "speed": 2.0,\n    "name": "x"\n}\n'


def test_setting_an_ini_value_splices_in_place_keeping_comment_and_spacing():
    # configobj 寫回會毀格式（#217），所以 ini 走原地文字替換（#17 的 D2）。
    text = "[main]\nspeed = 1  ; fast\nname = x\n"
    parsed = parse(text, "ini")

    set_value(parsed, "main.speed", 2.0)

    assert dump(parsed) == "[main]\nspeed = 2.0  ; fast\nname = x\n"


def test_setting_an_unknown_path_raises_a_named_error_naming_the_path():
    parsed = parse("a: 1\n", "yaml")

    with pytest.raises(UnknownPath) as exc:
        set_value(parsed, "b.c", 1)

    assert "b.c" in str(exc.value)


def test_raw_cannot_be_edited_by_path():
    # raw 不解析、沒有結構——沒有「哪個值」可改。
    with pytest.raises(UnsupportedFormat):
        set_value(parse("anything", "raw"), "a", 1)
