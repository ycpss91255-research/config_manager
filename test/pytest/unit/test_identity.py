"""T5 — 身分推導。測試介面：core/identity 的 derive_name / new_uid。

用語依 CONTEXT.md。derive_name 由目標路徑推導人可讀名稱；new_uid 由匯入
時刻產生永不變的 uid（時鐘由外部注入，ADR-00000011）。
"""

import datetime
import string

from config_manager.core.identity import derive_name, new_uid

_UID_LEN = 8


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
