"""T7 — 變更紀錄。測試介面：io/git 的 record / history / revert。

對真實的臨時 git repo 操作，並且**透過 history() 驗證，不直接跑 git log**——
繞過介面去讀底層，測到的就不是這個介面的行為（T7 的測試方式）。

commit 訊息格式為 `<類型>(<uid>): <說明>`，scope 只放 uid：name 與 hostname
都可改，寫進歷史會讓前後兩筆對不起來（ADR-00000007）。
"""

import subprocess

import pytest

from config_manager.io.errors import UnknownKind
from config_manager.io.git import history, record, revert

AUTHOR = "劉宇盈 <yy@example.invalid>"


def _repo(tmp_path):
    """一個有初始 commit 的臨時 config-repo。"""
    path = tmp_path / "config-repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path),
         "-c", "user.name=seed", "-c", "user.email=seed@example.invalid",
         "commit", "-q", "--allow-empty", "-m", "chore: 起點"],
        check=True,
    )
    return path


def test_a_recorded_change_is_found_in_history_with_its_author(tmp_path):
    repo = _repo(tmp_path)
    (repo / "nav2.yaml").write_text("max_vel: 0.8\n")

    record(str(repo), "mfz3k9q1", "cfg", "調整 max_vel 至 0.8", AUTHOR)
    entries = history(str(repo), "mfz3k9q1")

    assert len(entries) == 1
    assert (entries[0].kind, entries[0].uid, entries[0].summary, entries[0].author) == (
        "cfg",
        "mfz3k9q1",
        "調整 max_vel 至 0.8",
        AUTHOR,
    )


def test_a_kind_outside_the_allowed_set_is_refused(tmp_path):
    # 類型限 import / cfg / revert / adopt / meta / unmanage（ADR-00000007）。
    repo = _repo(tmp_path)
    (repo / "nav2.yaml").write_text("max_vel: 0.8\n")

    with pytest.raises(UnknownKind):
        record(str(repo), "mfz3k9q1", "wip", "還沒寫完", AUTHOR)

    assert history(str(repo), "mfz3k9q1") == []


def test_history_filtered_by_kind_leaves_the_others_out(tmp_path):
    repo = _repo(tmp_path)
    (repo / "nav2.yaml").write_text("max_vel: 0.8\n")
    record(str(repo), "mfz3k9q1", "cfg", "設為 0.8", AUTHOR)
    (repo / "nav2.yaml").write_text("max_vel: 1.2\n")
    record(str(repo), "mfz3k9q1", "meta", "移至 perception 群組", AUTHOR)

    assert [entry.kind for entry in history(str(repo), "mfz3k9q1", "cfg")] == ["cfg"]


def _two_versions(tmp_path):
    """一個 uid 的兩筆變更：先設 0.8，再設 1.2。回傳 repo 與第一筆的 sha。"""
    repo = _repo(tmp_path)
    (repo / "nav2.yaml").write_text("max_vel: 0.8\n")
    record(str(repo), "mfz3k9q1", "cfg", "設為 0.8", AUTHOR)
    first = history(str(repo), "mfz3k9q1")[0].sha
    (repo / "nav2.yaml").write_text("max_vel: 1.2\n")
    record(str(repo), "mfz3k9q1", "cfg", "設為 1.2", AUTHOR)
    return repo, first


def test_revert_adds_a_record_and_leaves_the_earlier_ones_in_place(tmp_path):
    # 退版以反向變更實作：多一筆新紀錄，先前的都還在（ADR-00000005）。
    repo, first = _two_versions(tmp_path)

    revert(str(repo), "mfz3k9q1", first, "nav2.yaml", AUTHOR)

    assert [entry.kind for entry in history(str(repo), "mfz3k9q1")] == [
        "revert",
        "cfg",
        "cfg",
    ]


def test_revert_puts_back_the_content_of_that_version(tmp_path):
    repo, first = _two_versions(tmp_path)

    revert(str(repo), "mfz3k9q1", first, "nav2.yaml", AUTHOR)

    assert (repo / "nav2.yaml").read_text() == "max_vel: 0.8\n"
