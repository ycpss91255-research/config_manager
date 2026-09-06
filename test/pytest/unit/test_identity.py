"""T5 — 身分推導。測試介面：core/identity 的 derive_name / new_uid。

用語依 CONTEXT.md。derive_name 由目標路徑推導人可讀名稱；new_uid 由匯入
時刻產生永不變的 uid（時鐘由外部注入，ADR-00000011）。
"""

import datetime
import string

import pytest

from config_manager.core.errors import NameUnderivable
from config_manager.core.identity import derive_name, new_uid

_UID_LEN = 8

# 訊息裡那個合格的例子，與 T5 第一列的推導範例是同一條路徑。斷言整串而非
# 「有沒有斜線」：一則說「要絕對路徑」卻沒給例子的訊息，三要素只有兩個。
_QUALIFYING_EXAMPLE = "/opt/robot/navigation/params.yaml"

# 推不出名稱的形狀，逐一列出而不是只留代表。四種來源：沒有分隔符、有分隔符但相對、
# 絕對但只有一層、以及空層級落在頭／中／尾。分開列是因為它們走的是不同的守門。
_UNDERIVABLE_TARGET_PATHS = (
    "",
    "params",
    "params.yaml",
    "navigation/params.yaml",
    "./params.yaml",
    "../params.yaml",
    "/",
    "//",
    "/params.yaml",
    "/opt/robot/",
    "/opt//params.yaml",
)


def _rejection(target_path: str) -> str:
    """derive_name 拒絕這個輸入時說的話。

    裸例外刻意不接住——它就是判準說的第三種結果，讓它從這一格炸出來，
    traceback 才帶得出是哪一個輸入。
    """
    with pytest.raises(NameUnderivable) as exc:
        derive_name(target_path)
    return str(exc.value)


def test_derive_name_from_nested_target_path():
    # /opt/robot/navigation/params.yaml → navigation-params（取最後二層、去副檔名）。
    assert derive_name("/opt/robot/navigation/params.yaml") == "navigation-params"


def test_derive_name_strips_any_extension_not_only_yaml():
    # /etc/docker/daemon.json → docker-daemon（副檔名不限 .yaml）。
    assert derive_name("/etc/docker/daemon.json") == "docker-daemon"


def test_derive_name_converts_underscores_to_hyphens():
    # 底線轉連字號：/opt/app/max_speed.yaml → app-max-speed。
    assert derive_name("/opt/app/max_speed.yaml") == "app-max-speed"


def test_derive_name_dedupes_overlapping_levels():
    # 去重疊層級：/etc/docker/docker.json → docker（不是 docker-docker）。
    assert derive_name("/etc/docker/docker.json") == "docker"


def test_derive_name_rejects_a_relative_path_naming_what_it_received():
    # 相對路徑 → 具名例外，訊息指名收到的那一個路徑（不變式 2：大聲且具體）。
    #
    # 先前這裡丟的是 parts[-2] 的裸 IndexError。一個 IndexError 說不出被拒絕的是
    # 什麼、也說不出該傳什麼進來，呼叫端只能靠 traceback 的行號回推——那正是核心層
    # 其餘失敗都用具名例外的理由。
    with pytest.raises(NameUnderivable) as exc:
        derive_name("params.yaml")

    assert "params.yaml" in str(exc.value)


def test_derive_name_rejects_a_relative_path_that_already_has_two_levels():
    # 有兩層但仍是相對路徑 → 一樣拒絕。T4 白名單判定要求絕對路徑，而這一條擋的是
    # 「層數夠了就放行」——navigation/params.yaml 推得出 navigation-params，
    # 一個看起來完全正常、但來源不是目標位置的名字。
    with pytest.raises(NameUnderivable):
        derive_name("navigation/params.yaml")


def test_derive_name_rejection_shows_a_qualifying_absolute_path():
    # 三要素的「該怎麼改」：訊息給一個合格的例子，不是只說「請改成絕對路徑」。
    with pytest.raises(NameUnderivable) as exc:
        derive_name("params.yaml")

    assert f"下一步：改傳絕對路徑，例如 {_QUALIFYING_EXAMPLE}" in str(exc.value)


def test_derive_name_rejects_an_absolute_path_with_no_parent_level():
    # /params.yaml 是絕對路徑，但根目錄之下只有一層。上層目錄是空字串，湊出來的
    # 名字是 `-params`——一個帶空層級的名字，接進 <name>@<hostname>-<uid> 之後
    # 分不出哪一段是 name。
    with pytest.raises(NameUnderivable) as exc:
        derive_name("/params.yaml")

    assert "/params.yaml" in str(exc.value)


def test_derive_name_rejects_a_target_path_that_names_no_file():
    # /opt/robot/ 指的是一個目錄，不是目標位置。檔名層是空的，湊出來的是 `robot-`。
    with pytest.raises(NameUnderivable):
        derive_name("/opt/robot/")


def test_derive_name_rejects_an_empty_level_in_the_middle_of_the_path():
    # /opt//params.yaml 兩層都在的樣子只是切出來的假象：上層目錄是空字串。
    # 空層級出現在路徑中間與出現在頭尾是同一件事，守門不該只認得後者。
    with pytest.raises(NameUnderivable):
        derive_name("/opt//params.yaml")


def test_derive_name_rejection_of_a_short_absolute_path_shows_a_qualifying_example():
    # 與相對路徑那一則分開的訊息：這裡缺的不是「絕對」，是「兩層都在」。
    with pytest.raises(NameUnderivable) as exc:
        derive_name("/params.yaml")

    assert f"下一步：改傳兩層都在的絕對路徑，例如 {_QUALIFYING_EXAMPLE}" in str(exc.value)


def test_derive_name_answers_every_underivable_shape_with_this_repos_own_exception():
    # 判準（#126）：任何輸入，要嘛回一個名字，要嘛丟一個**這個 repo 定義的**例外，
    # 沒有第三種。裸 IndexError 是第三種。
    #
    # 這一則寫下來的當下就是綠的——它的紅由突變檢查給出：拿掉任一條守門，這裡
    # 就有輸入落回 IndexError（`params.yaml`）或落回一個帶空層級的名字（`/`）。
    # 上面那幾則各自釘住一個形狀，這一則釘住的是「清單本身沒有漏洞」。
    assert all(
        target_path in _rejection(target_path)
        for target_path in _UNDERIVABLE_TARGET_PATHS
    )


def test_new_uid_is_eight_char_base36():

    now = datetime.datetime(2026, 9, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
    uid = new_uid(now)
    base36 = string.digits + string.ascii_lowercase
    # uid 由毫秒時間戳轉 base36，長度 8 碼。
    assert len(uid) == _UID_LEN and all(c in base36 for c in uid)


def test_new_uid_value_comes_from_the_injected_clock():

    # 期望值是手算的，不是用實作的算法重算一次（TEST-PLAN 撰寫規則）：
    #   已知錨點 2020-01-01T00:00:00Z = 1577836800 秒
    #   2020-01-01 → 2026-01-01 共 2192 天（2020 與 2024 為閏年）
    #     1577836800 + 2192 * 86400 = 1767225600
    #   2026-01-01 → 2026-09-04 共 246 天（2026 非閏年）
    #     1767225600 + 246 * 86400 = 1788480000 秒 = 1788480000000 毫秒
    #   1788480000000 轉 base36：
    #     22 * 36^7 = 1724011610112  餘 64468389888   → m
    #     29 * 36^6 =   63126687744  餘  1341702144   → t
    #     22 * 36^5 =    1330255872  餘    11446272   → m
    #      6 * 36^4 =      10077696  餘     1368576   → 6
    #     29 * 36^3 =       1353024  餘       15552   → t
    #     12 * 36^2 =         15552  餘           0   → c
    #      0 * 36^1, 0 * 36^0                         → 00
    now = datetime.datetime(2026, 9, 4, tzinfo=datetime.timezone.utc)
    # 時鐘注入正是這個介面存在的理由：值必須由傳入的時刻決定，而不是系統時間。
    assert new_uid(now) == "mtm6tc00"


def test_new_uid_increments_within_the_same_millisecond():

    now = datetime.datetime(2026, 9, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
    first = new_uid(now)
    second = new_uid(now, previous=first)
    # 同一毫秒內的兩次呼叫不會相同：後者遞增 1。int(x, 36) 為獨立的 base36 解碼。
    assert int(second, 36) == int(first, 36) + 1


def test_new_uid_is_string_sortable_in_time_order():

    earlier = datetime.datetime(2026, 9, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
    later = datetime.datetime(2026, 9, 4, 12, 0, 1, tzinfo=datetime.timezone.utc)
    # 可排序 = 保留納管順序：字串比較的順序需與時間一致（固定 8 碼寬度是關鍵）。
    assert new_uid(earlier) < new_uid(later)
