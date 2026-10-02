"""io/attributes — 把屬性的修改寫回清單檔並記一筆 meta（#286）。

編排層，同 `io/unmanage` 的處理：效果全透過既有介面觀察——清單檔條目用 `core/config_list.load`
（T1）、紀錄用 `io/git.history`（T7）。屬性的規則本身在 T16（`core/attributes`）；這裡擋的是
接線：寫回去了、其餘條目原樣、記了一筆 meta 且作者對、不合法的什麼都不寫、失敗回滾。

整合層：真的碰檔案系統與 git。
"""

import subprocess

import pytest

from config_manager.core.attributes import Attributes
from config_manager.core.config_list import load
from config_manager.core.errors import AttributeInvalid
from config_manager.io.attributes import update
from config_manager.io.errors import AttributesLeftBehind
from config_manager.io.git import history
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import CONFIG_LIST_NAME

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
    root = tmp_path / "managed"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(b"max_vel: 0.8\n")
    request = OnboardRequest(source_path=str(path), fmt="yaml", allowed_roots=(str(root),))
    return onboard(str(repo), request, _AUTHOR)


def _listed(repo):
    return load((repo / CONFIG_LIST_NAME).read_text(encoding="utf-8")).files


def _state(repo):
    def _git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        ).stdout
    return _git("rev-parse", "HEAD").strip(), _git("status", "--porcelain")


def _wanted(entry, **changes):
    values = {"name": entry.name, "hostname": entry.hostname, "groups": tuple(entry.groups),
              "description": entry.description, **changes}
    return Attributes(**values)


def test_the_new_attributes_are_written_back_and_the_other_entry_is_untouched(tmp_path):
    repo = _repo(tmp_path)
    first = _managed(tmp_path, repo, "a.yaml")
    second = _managed(tmp_path, repo, "b.yaml")

    updated = update(
        str(repo), first.uid,
        _wanted(first, name="navigation", groups=("navigation", "safety"), description="導航"),
        _AUTHOR,
    )

    stored = {entry.uid: entry for entry in _listed(repo)}
    assert updated == stored[first.uid]
    assert (updated.name, updated.groups, updated.description) == (
        "navigation", ["navigation", "safety"], "導航",
    )
    assert (updated.uid, updated.source, updated.target) == (first.uid, first.source, first.target)
    assert stored[second.uid] == second


def test_the_change_is_recorded_as_meta_apart_from_content_changes(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)

    update(str(repo), entry.uid, _wanted(entry, groups=("perception",)), _AUTHOR)

    latest = history(str(repo), entry.uid)[0]
    assert (latest.kind, latest.summary, latest.author) == ("meta", "群組改為 perception", _AUTHOR)
    assert _state(repo)[1] == ""  # 清單檔的改動進了那筆紀錄，工作區乾淨


def test_an_invalid_attribute_writes_nothing(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    before = _state(repo)

    with pytest.raises(AttributeInvalid):
        update(str(repo), entry.uid, _wanted(entry, name="   "), _AUTHOR)

    assert _state(repo) == before and _listed(repo)[0].name == entry.name


def test_a_failed_commit_puts_the_list_file_back(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    before = _state(repo)

    def _commit_boom(*_args, **_kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.attributes.record", _commit_boom)

    with pytest.raises(RuntimeError, match="commit 壞了"):
        update(str(repo), entry.uid, _wanted(entry, name="navigation"), _AUTHOR)

    assert _state(repo) == before and _listed(repo)[0].name == entry.name


def test_a_rollback_that_cannot_finish_fails_loudly_naming_the_leftover(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    seen = []

    def _write_then_fail(*_args, **_kwargs):
        seen.append(1)
        if len(seen) == 1:
            return
        raise OSError("磁碟寫不進去")

    def _commit_boom(*_args, **_kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.attributes.write_config_list", _write_then_fail)
    monkeypatch.setattr("config_manager.io.attributes.record", _commit_boom)

    with pytest.raises(AttributesLeftBehind) as caught:
        update(str(repo), entry.uid, _wanted(entry, name="navigation"), _AUTHOR)

    assert CONFIG_LIST_NAME in str(caught.value) and "commit 壞了" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)
