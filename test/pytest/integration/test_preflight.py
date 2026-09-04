"""T15 — 啟動前置檢查。測試介面：io/preflight 的 preflight。

契約在做實事之前先自我驗證（不變式 2）：掛載壞掉、清單檔不可解析、來源內容
不存在時立即失敗，而不是等容器起來後才在某個請求裡爆開。

以真實的檔案系統測（io 層），用 tmp_path。
"""

import pytest

from config_manager.io.errors import (
    ConfigListMissing,
    ConfigListUnparsable,
    SourceMissing,
)
from config_manager.io.preflight import preflight

_MINIMAL_LIST = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""

_ONE_ENTRY = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = []
"""


def _write_list(repo, text):
    (repo / "config-list.toml").write_text(text, encoding="utf-8")


def _write_source(repo, relative):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("max_vel_x: 0.8\n", encoding="utf-8")


def test_missing_config_list_raises_named_exception_naming_the_path(tmp_path):
    # repo 在，清單檔不在。這不是首次啟動——首次啟動時 entrypoint 已經種下它了
    # ——所以是有人刪了它或掛錯路徑（不變式 2：大聲失敗）。
    with pytest.raises(ConfigListMissing) as exc:
        preflight(str(tmp_path))

    assert "config-list.toml" in str(exc.value)


def test_unparsable_config_list_raises_named_exception_with_the_parse_error(tmp_path):
    # 壞掉的 TOML。訊息要帶得出解析器說了什麼，否則操作者只知道「壞了」。
    (tmp_path / "config-list.toml").write_text("list_version = [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigListUnparsable) as exc:
        preflight(str(tmp_path))

    assert "config-list.toml" in str(exc.value)


def test_minimal_config_list_with_no_entries_passes(tmp_path):
    # 什麼都還沒納管是合法狀態，不是故障——entrypoint 種下的就是這份檔案。
    _write_list(tmp_path, _MINIMAL_LIST)

    preflight(str(tmp_path))


def test_missing_source_content_raises_named_exception_with_the_ref_and_path(tmp_path):
    # 清單檔說管了某份 config，repo 裡卻沒有它的內容。這是 ADR-00000002 的
    # 「唯一真實來源」被掏空——起得來但每一次 apply 都會失敗，正是不變式 2
    # 要求在啟動時就攔下的狀況。原 T1 的來源存在性移到這裡（#60）。
    _write_list(tmp_path, _ONE_ENTRY)

    with pytest.raises(SourceMissing) as exc:
        preflight(str(tmp_path))

    message = str(exc.value)
    assert "navigation-params@amr01-mfz3k9q1" in message
    assert "files/amr01/nav2_params.yaml" in message


def test_undeployed_target_is_not_a_preflight_failure(tmp_path):
    # 只查來源側。目標尚未部署是合法狀態——「未部署」是 T2 的四種狀態之一，
    # 不是故障；因為目標不存在就拒絕啟動，等於讓系統無法完成第一次 apply。
    _write_list(tmp_path, _ONE_ENTRY)
    _write_source(tmp_path, "files/amr01/nav2_params.yaml")

    preflight(str(tmp_path))
