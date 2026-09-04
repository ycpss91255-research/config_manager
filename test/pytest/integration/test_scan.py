"""T21 — 差異掃描。測試介面：io/scan 的 scan。

把 T15（讀清單）、T20（算雜湊）、T2（判定）接起來的那一層。它有自己的測試
介面，是因為「接起來」本身會出錯——順序接反、把來源當目標、把 None 當成
一致——而那三個介面各自全綠都擋不住這類錯誤。

以真實的檔案系統測（io 層），用 tmp_path。
"""

import pytest

from config_manager.core.state import State
from config_manager.io.errors import SourceMissing
from config_manager.io.scan import scan

_HEADER = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def _entry(uid, name, source, target):
    return f"""
[[files]]
uid      = "{uid}"
name     = "{name}"
hostname = "amr01"
source   = "{source}"
target   = "{target}"
format   = "yaml"
groups   = []
"""


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_empty_config_list_scans_to_an_empty_result(tmp_path):
    # 什麼都還沒納管是合法狀態——entrypoint 種下的就是這份清單檔。
    _write(tmp_path / "config-list.toml", _HEADER)

    assert scan(str(tmp_path)) == []


def test_absent_target_is_reported_as_missing(tmp_path):
    target = tmp_path / "deployed" / "nav.yaml"
    _write(tmp_path / "files" / "nav.yaml", "max_vel_x: 0.8\n")
    _write(
        tmp_path / "config-list.toml",
        _HEADER + _entry("mfz3k9q1", "nav", "files/nav.yaml", str(target)),
    )

    assert [state for _, state in scan(str(tmp_path))] == [State.MISSING]


def test_target_matching_source_is_reported_as_in_sync(tmp_path):
    target = tmp_path / "deployed" / "nav.yaml"
    _write(tmp_path / "files" / "nav.yaml", "max_vel_x: 0.8\n")
    _write(target, "max_vel_x: 0.8\n")
    _write(
        tmp_path / "config-list.toml",
        _HEADER + _entry("mfz3k9q1", "nav", "files/nav.yaml", str(target)),
    )

    assert [state for _, state in scan(str(tmp_path))] == [State.IN_SYNC]


def test_target_differing_from_source_is_reported_as_drift(tmp_path):
    # 有人繞過介面直接改了目標（設計文件 §5.4）。
    target = tmp_path / "deployed" / "nav.yaml"
    _write(tmp_path / "files" / "nav.yaml", "max_vel_x: 0.8\n")
    _write(target, "max_vel_x: 1.2\n")
    _write(
        tmp_path / "config-list.toml",
        _HEADER + _entry("mfz3k9q1", "nav", "files/nav.yaml", str(target)),
    )

    assert [state for _, state in scan(str(tmp_path))] == [State.DRIFT]


def test_each_entry_is_judged_independently_and_in_list_order(tmp_path):
    # 三筆不同狀態同時存在。順序與清單檔一致——畫面的排序不該由掃描決定。
    first = tmp_path / "deployed" / "a.yaml"
    second = tmp_path / "deployed" / "b.yaml"
    _write(tmp_path / "files" / "a.yaml", "a: 1\n")
    _write(tmp_path / "files" / "b.yaml", "b: 1\n")
    _write(tmp_path / "files" / "c.yaml", "c: 1\n")
    _write(first, "a: 1\n")
    _write(second, "b: 2\n")
    _write(
        tmp_path / "config-list.toml",
        _HEADER
        + _entry("mfz3k9q1", "a", "files/a.yaml", str(first))
        + _entry("mfz3k9q2", "b", "files/b.yaml", str(second))
        + _entry("mfz3k9q3", "c", "files/c.yaml", str(tmp_path / "deployed" / "c.yaml")),
    )

    result = scan(str(tmp_path))

    assert [entry.uid for entry, _ in result] == ["mfz3k9q1", "mfz3k9q2", "mfz3k9q3"]
    assert [state for _, state in result] == [State.IN_SYNC, State.DRIFT, State.MISSING]


def test_source_vanishing_after_startup_raises_naming_the_entry(tmp_path):
    # 啟動時 T15 驗過來源都在。掃描時不在了，代表有人動了 repo——這不是四種
    # 狀態之一，把它折進「未部署」會讓一個壞掉的 repo 看起來只是還沒 apply。
    _write(
        tmp_path / "config-list.toml",
        _HEADER + _entry("mfz3k9q1", "nav", "files/nav.yaml", "/opt/robot/nav.yaml"),
    )

    with pytest.raises(SourceMissing) as exc:
        scan(str(tmp_path))

    assert "nav@amr01-mfz3k9q1" in str(exc.value)
