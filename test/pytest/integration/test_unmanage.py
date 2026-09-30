"""io/unmanage — 解除納管（#28）。

編排層，同 `io/onboard` 的處理：效果全透過既有介面觀察——清單檔條目用 `core/config_list.load`
（T1）、紀錄用 `io/git.history`（T7）、來源複本與 target 直接看檔案系統。這裡擋的是接線：
解除後條目不見、來源複本不見、**target 還在**、有一筆 unmanage 紀錄；寫入中途失敗整批回滾。

整合層：真的碰檔案系統與 git。
"""

import subprocess

import pytest

from config_manager.core.config_list import load
from config_manager.io.errors import UnmanageLeftBehind, UnmanageNotFound
from config_manager.io.git import history
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.unmanage import unmanage

_AUTHOR = "陳小明 <ming@example.com>"
_SEED = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / CONFIG_LIST_NAME).write_text(_SEED, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=seed", "-c", "user.email=s@e.x",
         "commit", "-q", "-m", "chore: 種下清單檔"],
        check=True,
    )
    return repo


def _managed(tmp_path, repo, name="nav2.yaml"):
    """納管一份來源檔，回 (條目, target 路徑)。"""
    root = tmp_path / "managed"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(b"max_vel: 0.8\n")
    request = OnboardRequest(source_path=str(path), fmt="yaml", allowed_roots=(str(root),))
    return onboard(str(repo), request, _AUTHOR), path


def _git_state(repo):
    def _git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        ).stdout
    return _git("rev-parse", "HEAD").strip(), _git("status", "--porcelain")


def test_unmanaging_removes_the_entry_and_the_source_copy_but_keeps_the_target(tmp_path):
    # §5.5：解除管理不改變系統當前行為——target 一個位元組都不動；系統只是不再追蹤它。
    repo = _repo(tmp_path)
    entry, target = _managed(tmp_path, repo)

    removed = unmanage(str(repo), entry.uid, _AUTHOR)

    assert removed.uid == entry.uid
    assert [e.uid for e in load((repo / CONFIG_LIST_NAME).read_text(encoding="utf-8")).files] == []
    assert not (repo / entry.source).exists()
    assert target.read_bytes() == b"max_vel: 0.8\n"


def test_unmanaging_records_an_unmanage_change_after_the_import(tmp_path):
    # 解除納管本身留紀錄（uid 的歷史：import → unmanage），來源複本的歷史內容仍在 git 裡。
    repo = _repo(tmp_path)
    entry, _ = _managed(tmp_path, repo)

    unmanage(str(repo), entry.uid, _AUTHOR)

    kinds = [change.kind for change in history(str(repo), entry.uid)]
    assert kinds == ["unmanage", "import"]
    assert history(str(repo), entry.uid)[0].author == _AUTHOR
    assert _git_state(repo)[1] == ""  # 工作區乾淨：刪除與清單檔都進了那筆 commit


def test_unmanaging_one_leaves_the_other_entries_verbatim(tmp_path):
    repo = _repo(tmp_path)
    first, _ = _managed(tmp_path, repo, "a.yaml")
    second, _ = _managed(tmp_path, repo, "b.yaml")

    unmanage(str(repo), first.uid, _AUTHOR)

    remaining = load((repo / CONFIG_LIST_NAME).read_text(encoding="utf-8")).files
    assert [e.uid for e in remaining] == [second.uid]
    assert (repo / second.source).exists()


def test_unmanaging_an_unknown_uid_is_a_named_error_and_touches_nothing(tmp_path):
    repo = _repo(tmp_path)
    _managed(tmp_path, repo)
    before = _git_state(repo)

    with pytest.raises(UnmanageNotFound):
        unmanage(str(repo), "zzzzzzz9", _AUTHOR)

    assert _git_state(repo) == before


def test_a_failed_commit_rolls_the_repo_back(tmp_path, monkeypatch):
    # 清單檔寫了、來源複本刪了、也 stage 了，commit 失敗 → 全部還原，repo 回到解除前。
    repo = _repo(tmp_path)
    entry, _ = _managed(tmp_path, repo)
    before = _git_state(repo)

    def _commit_boom(*_args, **_kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.unmanage.record", _commit_boom)

    with pytest.raises(RuntimeError, match="commit 壞了"):
        unmanage(str(repo), entry.uid, _AUTHOR)

    assert _git_state(repo) == before
    assert (repo / entry.source).read_bytes() == b"max_vel: 0.8\n"


def test_a_rollback_that_cannot_finish_fails_loudly_naming_the_leftover(tmp_path, monkeypatch):
    # 回滾還原清單檔那一步也失敗 → UnmanageLeftBehind 指名殘留、原本的失敗掛在 __cause__。
    repo = _repo(tmp_path)
    entry, _ = _managed(tmp_path, repo)
    seen = []

    def _write_then_fail(*_args, **_kwargs):
        seen.append(1)
        if len(seen) == 1:
            return  # 解除當下那一次寫入「成功」（實際不寫，讓還原那次的失敗獨立可見）
        raise OSError("磁碟寫不進去")

    def _commit_boom(*_args, **_kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.unmanage.write_config_list", _write_then_fail)
    monkeypatch.setattr("config_manager.io.unmanage.record", _commit_boom)

    with pytest.raises(UnmanageLeftBehind) as caught:
        unmanage(str(repo), entry.uid, _AUTHOR)

    assert CONFIG_LIST_NAME in str(caught.value) and "commit 壞了" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
