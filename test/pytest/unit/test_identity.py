"""T5 — 身分推導。測試介面：core/identity 的 derive_name / new_uid。

用語依 CONTEXT.md。derive_name 由目標路徑推導人可讀名稱；new_uid 由匯入
時刻產生永不變的 uid（時鐘由外部注入，ADR-00000011）。
"""

from core.identity import derive_name, new_uid


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
    import datetime
    import string

    now = datetime.datetime(2026, 9, 4, 12, 0, 0, tzinfo=datetime.timezone.utc)
    uid = new_uid(now)
    base36 = string.digits + string.ascii_lowercase
    # uid 由毫秒時間戳轉 base36，長度 8 碼。
    assert len(uid) == 8 and all(c in base36 for c in uid)
