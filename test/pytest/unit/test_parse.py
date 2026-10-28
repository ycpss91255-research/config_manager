"""T6 — 格式解析與原樣寫回。core/parse 的單元規格。

核心層測試在無檔案系統、無 git、無網路下執行（CLAUDE.md）：待解析的 text 直接以
字串餵進去，不碰磁碟。
"""

import pytest

from config_manager.core import errors as core_errors
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
    # json 只換掉被改的那個值的原文，縮排、key 順序跟著原文不動（#17 的 D2、F9）。
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


# ── F9 的邊界：介面送回整個清單時常見的幾種改法，沒改的部分都逐行不動 ──────────────
# 介面的清單操作（web/index.html）：改值、刪一項、上下移、新增時複製最後一項。每一種送來的都是
# 最外層的整個清單。新增的那一項怎麼寫這次不規定，只比對它以外的行。

_STAGES_YAML = (
    "stages:\n"
    "  - name: alpha  # 主線\n"
    "    ports: [8201, 8202]\n"
    "    gain: 1.00\n"
    "  - name: beta   # 備援\n"
    "    ports: [8301]\n"
    "    gain: 2.50\n"
)


def test_removing_one_element_and_changing_another_keeps_the_survivors_own_writing():
    # 刪掉 alpha、同時把 beta 的 gain 改成 3（介面送整數）：beta 帶著自己的註解與一行的 ports，
    # 被改的 gain 寫出帶小數點。
    parsed = parse(_STAGES_YAML, "yaml")

    set_value(parsed, "stages", [{"name": "beta", "ports": [8301], "gain": 3}])

    expected = "stages:\n  - name: beta   # 備援\n    ports: [8301]\n    gain: 3.0\n"
    assert _lines(dump(parsed)) == _lines(expected)


def test_swapping_two_elements_moves_each_with_its_own_comment_and_writing():
    # 上下移：兩項都沒改（介面把 1.00 送成 1、2.50 送成 2.5），各自帶著註解、一行的 ports、
    # `2.50` 與 `1.00` 的寫法換位置。
    parsed = parse(_STAGES_YAML, "yaml")

    set_value(
        parsed,
        "stages",
        [
            {"name": "beta", "ports": [8301], "gain": 2.5},
            {"name": "alpha", "ports": [8201, 8202], "gain": 1},
        ],
    )

    expected = (
        "stages:\n"
        "  - name: beta   # 備援\n"
        "    ports: [8301]\n"
        "    gain: 2.50\n"
        "  - name: alpha  # 主線\n"
        "    ports: [8201, 8202]\n"
        "    gain: 1.00\n"
    )
    assert _lines(dump(parsed)) == _lines(expected)


def test_appending_a_copy_of_the_last_element_keeps_every_existing_line():
    # 新增＝複製最後一項附在後面：原有的每一行逐字不動，新的那一項接在後面（怎麼寫不規定）。
    parsed = parse(_STAGES_YAML, "yaml")

    beta = {"name": "beta", "ports": [8301], "gain": 2.5}
    set_value(parsed, "stages", [{"name": "alpha", "ports": [8201, 8202], "gain": 1}, beta, beta])

    original = _lines(_STAGES_YAML)
    assert _lines(dump(parsed))[: len(original)] == original


def test_sending_a_list_back_unchanged_keeps_a_nested_mixed_number_list_as_written():
    # 沒改的 `[1, 0.5]` 不能因為「有一項是 double 就全轉」的清單規則變成 `[1.0, 0.5]`。
    text = "items:\n  - w: [1, 0.5]\n    tag: a\n"
    parsed = parse(text, "yaml")

    set_value(parsed, "items", [{"w": [1, 0.5], "tag": "a"}])

    assert _lines(dump(parsed)) == _lines(text)


def test_a_null_inside_a_list_element_survives_removing_a_sibling():
    parsed = parse(
        "stages:\n  - name: alpha\n    extra: null\n  - name: beta\n    extra: null\n", "yaml"
    )

    set_value(parsed, "stages", [{"name": "beta", "extra": None}])

    assert _lines(dump(parsed)) == _lines("stages:\n  - name: beta\n    extra: null\n")


def test_a_changed_string_in_a_list_keeps_its_quotes():
    # 引號不能掉：`'auto'` 寫成裸字 auto 無妨，但 `'on'`／`'off'` 這類裸字用 YAML 1.1 讀的 ROS
    # 會當成布林——被改的字串沿用原本那一項的引號。
    parsed = parse("modes: ['on', 'off']\n", "yaml")

    set_value(parsed, "modes", ["on", "auto"])

    assert dump(parsed) == "modes: ['on', 'auto']\n"


def test_null_list_items_and_a_key_named_null_round_trip_as_written():
    text = "l:\n  - null  # 空\n  - 1\nspare:\nnull: 1\n"

    assert _lines(dump(parse(text, "yaml"))) == _lines(text)


def test_a_null_next_to_a_merge_key_round_trips_as_written():
    text = "base: &b\n  k: 1\nder:\n  <<: *b\n  m: null\n"

    assert _lines(dump(parse(text, "yaml"))) == _lines(text)


def test_setting_a_null_to_none_again_keeps_it_written_as_null():
    parsed = parse("a: null\nb: 1\n", "yaml")

    set_value(parsed, "a", None)
    set_value(parsed, "b", 2)

    assert dump(parsed) == "a: null\nb: 2\n"


_STAGES_JSON = (
    "{\n"
    '  "stages": [\n'
    '    {"name": "alpha", "ports": [8201, 8202]},\n'
    "    {\n"
    '      "name": "beta",\n'
    '      "gain": 1.0\n'
    "    }\n"
    "  ]\n"
    "}\n"
)


def test_removing_the_first_json_element_keeps_the_others_byte_identical():
    parsed = parse(_STAGES_JSON, "json")

    set_value(parsed, "stages", [{"name": "beta", "gain": 1}])

    expected = _STAGES_JSON.replace('    {"name": "alpha", "ports": [8201, 8202]},\n', "")
    assert _lines(dump(parsed)) == _lines(expected)


def test_appending_to_a_json_list_keeps_everything_before_it_as_written():
    # 新增的項接在最後一項之後：之前的原文一字不差（最後一項後面多一個逗號是 json 語法要的）。
    parsed = parse(_STAGES_JSON, "json")

    alpha = {"name": "alpha", "ports": [8201, 8202]}
    set_value(parsed, "stages", [alpha, {"name": "beta", "gain": 1}, {"name": "beta", "gain": 1}])

    kept = _STAGES_JSON[: _STAGES_JSON.index("    }\n") + len("    }")]
    assert dump(parsed).startswith(kept + ",")


def test_growing_a_crlf_json_list_keeps_crlf_line_endings():
    text = '{\r\n  "l": [\r\n    1,\r\n    2\r\n  ]\r\n}\r\n'
    parsed = parse(text, "json")

    set_value(parsed, "l", [1, 2, 3])

    assert dump(parsed) == '{\r\n  "l": [\r\n    1,\r\n    2,\r\n    3\r\n  ]\r\n}\r\n'


def test_rewriting_a_tab_indented_json_list_keeps_tabs():
    text = '{\n\t"l": [\n\t\t{\n\t\t\t"a": 1\n\t\t}\n\t]\n}\n'
    parsed = parse(text, "json")

    set_value(parsed, "l", [{"a": 1}, {"a": 2}])

    expected = '{\n\t"l": [\n\t\t{\n\t\t\t"a": 1\n\t\t},\n\t\t{\n\t\t\t"a": 2\n\t\t}\n\t]\n}\n'
    assert dump(parsed) == expected


def test_editing_a_duplicated_json_key_changes_the_occurrence_values_reads():
    # json.loads 取後者；改到的也是後者，values() 讀到的才是改過的值。
    parsed = parse('{"a": 1, "b": 2, "a": 3}', "json")

    set_value(parsed, "a", 9)

    assert dump(parsed) == '{"a": 1, "b": 2, "a": 9}'


def test_a_json_path_below_a_scalar_raises_a_named_error_naming_the_path():
    parsed = parse('{"a": 1}', "json")

    with pytest.raises(UnknownPath) as exc:
        set_value(parsed, "a.b", 2)

    assert "a.b" in str(exc.value)


def test_reordering_a_list_of_scalars_moves_each_end_of_line_comment_with_its_item():
    # 純量清單的行尾註解記在清單身上、以位置為索引：調序後要跟著那一項走，不能留在原位置。
    parsed = parse("recovery:\n  - spin  # 先轉\n  - wait  # 再等\n", "yaml")

    set_value(parsed, "recovery", ["wait", "spin"])

    assert _lines(dump(parsed)) == _lines("recovery:\n  - wait  # 再等\n  - spin  # 先轉\n")


# ── 審查補上的情境：json 清單按值配對、只有一項的清單新增、介面重排了鍵、只改了種類 ──────────


def test_changing_a_value_inside_a_json_list_changes_only_that_line():
    # 介面改 json 清單裡的一個值，送回的是長度相同的整個清單：只有那一行不同，int 送來的寫成 2.0。
    parsed = parse(_STAGES_JSON, "json")

    alpha = {"name": "alpha", "ports": [8201, 8202]}
    set_value(parsed, "stages", [alpha, {"name": "beta", "gain": 2}])

    expected = _STAGES_JSON.replace('"gain": 1.0', '"gain": 2.0')
    assert _lines(dump(parsed)) == _lines(expected)


def test_swapping_json_elements_moves_each_as_written():
    parsed = parse(_STAGES_JSON, "json")

    alpha = {"name": "alpha", "ports": [8201, 8202]}
    set_value(parsed, "stages", [{"name": "beta", "gain": 1}, alpha])

    expected = (
        "{\n"
        '  "stages": [\n'
        "    {\n"
        '      "name": "beta",\n'
        '      "gain": 1.0\n'
        "    },\n"
        '    {"name": "alpha", "ports": [8201, 8202]}\n'
        "  ]\n"
        "}\n"
    )
    assert _lines(dump(parsed)) == _lines(expected)


def test_removing_one_json_element_and_changing_another_keeps_the_survivors_untouched_lines():
    # 刪掉 alpha、把 beta 改名：beta 沒改的 `"gain": 2.50` 照原寫法，只有改名那一行不同。
    text = (
        "{\n"
        '  "stages": [\n'
        '    {"name": "alpha", "gain": 1.0},\n'
        "    {\n"
        '      "name": "beta",\n'
        '      "gain": 2.50\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    parsed = parse(text, "json")

    set_value(parsed, "stages", [{"name": "beta2", "gain": 2.5}])

    expected = (
        '{\n  "stages": [\n    {\n      "name": "beta2",\n      "gain": 2.50\n    }\n  ]\n}\n'
    )
    assert _lines(dump(parsed)) == _lines(expected)


def test_appending_to_a_one_element_json_list_keeps_the_existing_element_as_written():
    # 只有一項時按新增（複製最後一項）：原本那一項仍是一行，一行的 ports 也不被拆開。
    text = '{\n  "stages": [\n    {"name": "alpha", "ports": [8201, 8202]}\n  ]\n}\n'
    parsed = parse(text, "json")

    alpha = {"name": "alpha", "ports": [8201, 8202]}
    set_value(parsed, "stages", [alpha, alpha])

    assert dump(parsed).startswith(
        '{\n  "stages": [\n    {"name": "alpha", "ports": [8201, 8202]},'
    )


def test_json_object_keys_sent_back_in_another_order_count_as_unchanged():
    # JS 物件把數字樣子的鍵排到前面：鍵與值都沒變就是沒改，原文一字不動。
    text = '{"o": {"name": "a", "10": 1.0, "2": 2}, "x": 1}'
    parsed = parse(text, "json")

    set_value(parsed, "o", {"2": 2, "10": 1, "name": "a"})

    assert dump(parsed) == text


def test_a_new_element_in_a_json_double_list_is_written_with_a_decimal_point():
    parsed = parse('{"gains": [0.5, 1.5]}', "json")

    set_value(parsed, "gains", [0.5, 1.5, 2])

    assert dump(parsed) == '{"gains": [0.5, 1.5, 2.0]}'


def test_appending_to_a_crlf_json_list_of_objects_writes_no_bare_newline():
    text = '{\r\n  "l": [\r\n    {\r\n      "a": 1\r\n    }\r\n  ]\r\n}\r\n'
    parsed = parse(text, "json")

    set_value(parsed, "l", [{"a": 1}, {"a": 2}])

    assert "\n" not in dump(parsed).replace("\r\n", "")


def test_a_changed_yaml_element_keeps_its_key_order_when_keys_come_back_reordered():
    parsed = parse(_STAGES_YAML, "yaml")

    set_value(
        parsed,
        "stages",
        [
            {"gain": 3, "ports": [8201, 8202], "name": "alpha"},
            {"name": "beta", "ports": [8301], "gain": 2.5},
        ],
    )

    expected = _STAGES_YAML.replace("    gain: 1.00\n", "    gain: 3.0\n")
    assert _lines(dump(parsed)) == _lines(expected)


def test_changing_only_the_type_of_a_list_item_is_written():
    # Python 裡 True == 1，寫出來卻是不同的字：只改了種類也算改了。
    parsed = parse("l: [1, 2]\n", "yaml")

    set_value(parsed, "l", [True, 2])

    assert dump(parsed) == "l: [true, 2]\n"


def test_a_reordered_json_object_with_one_changed_value_changes_only_that_value():
    # 鍵被 JS 重排、又改了其中一個值：照原文的鍵順序只換那個值，沒改的 `1.0` 照原寫法。
    parsed = parse('{"o": {"name": "a", "10": 1.0, "2": 2}, "x": 1}', "json")

    set_value(parsed, "o", {"2": 3, "10": 1, "name": "a"})

    assert dump(parsed) == '{"o": {"name": "a", "10": 1.0, "2": 3}, "x": 1}'


# ── F16／F15：set_value 寫入的字串值重新 parse 後讀回相同（值與型別都相同）────────────
# 人工驗收後讀程式發現：ini 的 `_ini_with` 把 `;`／`#` 之後全當註解、新值原樣塞入不加引號，
# 而讀值的 configobj 去引號、吃前後空白、逗號分清單、`#` 起行內註解、`%(name)s` 插值——
# 寫的規則和讀的規則不同，寫進去的和讀回來的就不一樣。預期值一律是「寫進去的那個字串」。
# 無法無損表示的值（ini 含換行、含兩種三引號）要具名拒絕、文件一個位元組都不變，不靜默轉換。

_INI_ONE_KEY = "[s]\nname = v\n"


def _read_back_ini(text: str, value: str) -> tuple[str, object]:
    parsed = parse(text, "ini")
    set_value(parsed, "s.name", value)
    out = dump(parsed)
    return out, values(parse(out, "ini"))["s"]["name"]


def _unrepresentable():
    found = getattr(core_errors, "UnrepresentableValue", None)
    assert found is not None, "core.errors 還沒有 UnrepresentableValue（無法無損表示的值）"
    return found


def test_ini_a_value_containing_a_semicolon_is_replaced_whole_not_kept_as_a_comment():
    # F16-1：`a;b` 整個是值（configobj 不把 `;` 當行內註解），改成 abc 不能留下 `;b`。
    _, read = _read_back_ini("[s]\nname = a;b\n", "abc")
    assert read == "abc"


def test_ini_a_new_value_containing_a_hash_reads_back_whole_and_keeps_the_inline_comment():
    # F16-2：值裡的 `#` 不能變成註解；原本的行內註解 `#c` 要留著。
    out, read = _read_back_ini("[s]\nname = a #c\n", "x#y")
    assert (read, "#c" in out) == ("x#y", True)


def test_ini_a_new_value_keeps_its_leading_and_trailing_whitespace():
    # F16-3：前後空白是值的一部分（configobj 的規則是加引號才保得住）。
    _, read = _read_back_ini('[s]\nname = "a b"\n', " a b ")
    assert read == " a b "


def test_ini_a_new_value_containing_double_quotes_reads_back():
    # F16-4：值裡有一種引號，要用另一種引號包起來，讀回才相同、檔案才解析得了。
    _, read = _read_back_ini(_INI_ONE_KEY, 'say "hi"')
    assert read == 'say "hi"'


def test_ini_a_new_value_containing_a_comma_stays_one_string():
    # F16-5：configobj 把沒加引號的逗號當清單分隔；寫回要讓它讀成一個字串，型別不能變。
    _, read = _read_back_ini(_INI_ONE_KEY, "a, b")
    assert (read, isinstance(read, str)) == ("a, b", True)


def test_ini_a_new_value_containing_a_backslash_reads_back():
    # F16-6：反斜線不是跳脫（configobj 預設不處理）；寫回不得被吃掉或加倍。
    _, read = _read_back_ini(_INI_ONE_KEY, "C:\\dir\\x")
    assert read == "C:\\dir\\x"


def test_ini_a_new_value_that_looks_like_interpolation_reads_back_literally():
    # F16-7：`%(x)s` 是字面，不是插值；`100%` 也只是字。
    text = "[s]\nx = 1\nname = v\n"
    first, _ = _read_back_ini(text, "%(x)s")
    _, second = _read_back_ini(text, "100%")
    assert (values(parse(first, "ini"))["s"]["name"], second) == ("%(x)s", "100%")


def test_ini_a_new_value_containing_a_newline_is_refused_and_the_document_is_unchanged():
    # F16-8／B1：ini 不支援多行值；塞換行會把下一行變成壞行。要具名拒絕、文件不動。
    parsed = parse(_INI_ONE_KEY, "ini")
    with pytest.raises(_unrepresentable()):
        set_value(parsed, "s.name", "a\nb")
    assert dump(parsed) == _INI_ONE_KEY


def test_ini_a_value_with_both_kinds_of_triple_quotes_is_refused_and_the_document_is_unchanged():
    # configobj 文件：同時含兩種三引號的值沒有安全的引號寫法——無法無損表示就拒絕。
    parsed = parse(_INI_ONE_KEY, "ini")
    with pytest.raises(_unrepresentable()):
        set_value(parsed, "s.name", "a'''b\"\"\"c")
    assert dump(parsed) == _INI_ONE_KEY


def test_ini_the_semicolon_belongs_to_the_value_and_the_hash_after_a_space_is_the_comment():
    # F16-13：`a;b #c` 的值是 `a;b`、註解是 `#c`——改值後寫出的那一行要是 `name = abc #c`。
    out, read = _read_back_ini("[s]\nname = a;b #c\n", "abc")
    assert (out, read) == ("[s]\nname = abc #c\n", "abc")


def test_ini_an_existing_triple_quoted_multiline_value_survives_editing_another_key():
    # B1 的另一半：原本就有的三引號多行值不碰它、改別的鍵不能連帶壞掉或被拒絕（F16-9）。
    text = "[s]\nnotes = '''a\nb'''\ncount = 1\n"
    parsed = parse(text, "ini")
    set_value(parsed, "s.count", "2")
    out = dump(parsed)
    assert (out, values(parse(out, "ini"))["s"]["notes"]) == (text.replace("= 1", "= 2"), "a\nb")


_AWKWARD = ' a\nb, "c" #d \\e;f \'g\' 100% '


@pytest.mark.parametrize(
    ("fmt", "text", "untouched"),
    [
        ("yaml", "name: v\ncount: 1\n", "count: 1\n"),
        ("json", '{"name": "v", "count": 1}\n', '"count": 1'),
        ("toml", 'name = "v"\ncount = 1\n', "count = 1\n"),
    ],
)
def test_a_string_with_newlines_quotes_hashes_and_commas_reads_back_the_same(fmt, text, untouched):
    # F15 的下游：三種格式都有無損寫法，寫法由函式庫定、值不能變；沒改的行照舊。
    parsed = parse(text, fmt)
    set_value(parsed, "name", _AWKWARD)
    out = dump(parsed)
    assert (values(parse(out, fmt))["name"], untouched in out) == (_AWKWARD, True)
