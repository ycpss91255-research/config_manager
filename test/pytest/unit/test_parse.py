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

    # 註解文字、引號樣式、鍵順序、其餘各行逐位元組保留。唯一的正規化：被改那一行的新值寫到了
    # 原本註解的欄位，註解前就退成一個空白（寫得下時留在原欄位）——記在 T6 的說明段落。
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


def test_replacing_a_double_list_keeps_new_integer_looking_elements_as_doubles():
    # #46：清單整個替換時，原本是 double 的清單，新元素寫成 1 也要寫出 1.0（與單一欄位同一條規則）。
    parsed = parse("gains: [0.5, 1.5]\n", "yaml")

    set_value(parsed, "gains", [0.5, 1])

    assert dump(parsed) == "gains: [0.5, 1.0]\n"


def test_replacing_an_integer_list_leaves_integers_alone():
    parsed = parse("ids: [1, 2]\n", "yaml")

    set_value(parsed, "ids", [2, 1, 3])

    assert dump(parsed) == "ids: [2, 1, 3]\n"


# ── F9：改了別的值，沒改的部分照原寫法寫回（人工驗收 U21、U36，T6 末三列）──────────────
# 用的是驗收時真的進版過的檔（cm-verify/accept.sh 準備的內容，逐字照抄；雜湊與驗收環境的原檔相同）。
# 期望值＝原檔只換掉被改的那幾行，被改的行取自驗收時目標檔實際寫出的樣子；送進 set_value 的路徑與值
# 照介面實際送的（數字經 JSON 送來，1.0 到這裡是 1；清單送最外層的整個清單）。一律逐行比對。

_ACCEPT_ROBOT_YAML = (  # sha256 854ae57f…：準備時的 robot.yaml
    "# 驗收用：機器人參數（U07、U19–U26、U29、U31、U33、U34、U36）\n"
    "robot:\n"
    "  name: accept-bot      # 字串\n"
    "  enabled: true         # 布林\n"
    "  max_vel: 0.55         # 小數（公尺／秒）\n"
    "  min_vel: 0.1\n"
    "  retries: 3            # 整數\n"
    "  mode: normal          # 選項：normal／safe／fast（schema 設好之後才有下拉）\n"
    "ports: [8101, 8102]\n"
    "stages:\n"
    "  - name: alpha\n"
    "    ports: [8201, 8202]\n"
    "  - name: beta\n"
    "    ports: [8301]\n"
    "notes: null             # 型別未知：未結構化編輯（U36）\n"
)

# sha256 c83e7038…：U36 動手前的 robot.yaml（U21 已把 notes 寫成空白、U29 把 max_vel 改成 1.5）。
_ROBOT_YAML_BEFORE_U36 = (
    _ACCEPT_ROBOT_YAML.replace("  max_vel: 0.55         #", "  max_vel: 1.5          #")
    .replace("  retries: 3            #", "  retries: 5            #")
    .replace("notes: null             #", "notes:                  #")
)

_ACCEPT_SENSOR_JSON = (  # sha256 5ac89d1b…：準備時的 sensor.json
    "{\n"
    '  "sensor": "lidar",\n'
    '  "enabled": true,\n'
    '  "rate_hz": 10,\n'
    '  "range_m": 12.5,\n'
    '  "max_vel": 0.55,\n'
    '  "filters": ["median", "voxel"]\n'
    "}\n"
)


def _lines(text):
    return text.splitlines(keepends=True)


def test_a_yaml_null_round_trips_as_written():
    # 沒改任何值也一樣：`notes: null` 不能變成 `notes:`（F9 的根源在寫回空值，不在改值）。
    assert _lines(dump(parse(_ACCEPT_ROBOT_YAML, "yaml"))) == _lines(_ACCEPT_ROBOT_YAML)


def test_an_empty_yaml_null_round_trips_as_written():
    # 寫成空白的空值仍是空白——「一律寫成 null」不算修好，那是反方向改寫沒改的行。
    text = "spare:\nb: 1\n"

    assert _lines(dump(parse(text, "yaml"))) == _lines(text)


def test_changing_other_yaml_values_keeps_an_untouched_null_as_written():
    # U21：只改 max_vel 與 retries，沒改的 `notes: null` 照原寫法寫回。
    parsed = parse(_ACCEPT_ROBOT_YAML, "yaml")

    set_value(parsed, "robot.max_vel", 1)
    set_value(parsed, "robot.retries", 5)

    expected = _ACCEPT_ROBOT_YAML.replace(
        "  max_vel: 0.55         #", "  max_vel: 1.0          #"
    ).replace("  retries: 3            #", "  retries: 5            #")
    assert _lines(dump(parsed)) == _lines(expected)


def test_replacing_a_list_keeps_its_untouched_parts_line_by_line():
    # U36：介面改了 alpha 的 ports，送出的是頂層 ports 與最外層的整個 stages。被改的 alpha
    # ports 寫成一行或分行這次不規定（使用者記為觀察）；它前後沒改的每一行都要逐字不動，
    # 包括 beta 的 `ports: [8301]` 與空白的 `notes:`。
    parsed = parse(_ROBOT_YAML_BEFORE_U36, "yaml")

    set_value(parsed, "ports", [8103, 8102])
    set_value(
        parsed,
        "stages",
        [{"name": "alpha", "ports": [8203, 8202]}, {"name": "beta", "ports": [8301]}],
    )

    before = _lines(_ROBOT_YAML_BEFORE_U36.replace("ports: [8101, 8102]", "ports: [8103, 8102]"))
    alpha_ports = before.index("    ports: [8201, 8202]\n")
    head, tail = before[:alpha_ports], before[alpha_ports + 1 :]
    out = _lines(dump(parsed))
    assert (out[: len(head)], out[len(out) - len(tail) :]) == (head, tail)


def test_changing_a_json_value_keeps_untouched_lines_as_written():
    # sensor.json 只改 rate_hz：一行的 filters 陣列仍是原本那一行，其餘各行逐位元組不動。
    parsed = parse(_ACCEPT_SENSOR_JSON, "json")

    set_value(parsed, "rate_hz", 30)

    expected = _ACCEPT_SENSOR_JSON.replace('"rate_hz": 10', '"rate_hz": 30')
    assert _lines(dump(parsed)) == _lines(expected)


def test_changing_two_json_values_keeps_a_multi_line_array_as_written():
    # 分行的陣列照原本的換行、縮排、排列（json.dumps 會一項一行，收成一行也不對）；
    # 介面存草稿時對每條路徑依序 set_value，連改兩個值也只有那兩行不同。
    text = (
        '{\n  "rate_hz": 10,\n  "filters": [\n    "median", "voxel"\n  ],\n  "range_m": 12.5\n}\n'
    )
    parsed = parse(text, "json")

    set_value(parsed, "rate_hz", 30)
    set_value(parsed, "range_m", 15.5)

    expected = text.replace('"rate_hz": 10', '"rate_hz": 30').replace(
        '"range_m": 12.5', '"range_m": 15.5'
    )
    assert _lines(dump(parsed)) == _lines(expected)
