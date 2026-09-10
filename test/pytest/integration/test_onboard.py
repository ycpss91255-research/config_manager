"""io/onboard — 把一份磁碟上的 config 納入管理（#12）.

編排：把 read_source（T22）、identity（T5）、config_list（T1）、repo（#186）、
git（T7）串起來。它不算出新的值，效果全都透過既有介面觀察——逐位元組相同用
`io/digest`（T20）、清單檔條目用 `core/config_list.load`（T1）、commit 用
`io/git.history`（T7）——所以不是新測試介面，跟 `io/repo` 同一個處理方式。

各介面各自全綠擋不住的是**接線**（來源目標接反、name 取錯路徑、沒 commit）。這裡的
每一條就是在擋那個。

整合層：這裡真的碰檔案系統與 git。
"""

import os
import re
import subprocess

from config_manager.core.config_list import load
from config_manager.io.digest import digest
from config_manager.io.errors import SourceOutsideRoots
from config_manager.io.git import history
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import CONFIG_LIST_NAME

import pytest

_AUTHOR = "陳小明 <ming@example.com>"

_SEED = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def _repo(tmp_path):
    """一個像 entrypoint 種出來的 config repo：只有清單檔，沒有 files/。"""
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


def _source(tmp_path, name="nav2_params.yaml", content=b"max_vel: 0.8\n"):
    """一份要被納管的來源檔，放在白名單內。"""
    root = tmp_path / "managed"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(content)
    return root, path


def _request(root, path, fmt="yaml", note=""):
    return OnboardRequest(
        source_path=str(path), fmt=fmt, allowed_roots=(str(root),), ambiguity_note=note
    )


def test_onboarded_source_is_byte_identical_in_the_repo(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path, content=b"max_vel: 0.8\n\xff not utf-8\n")

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert digest(str(repo / entry.source)) == digest(str(path))


def test_the_entry_records_the_target_as_the_original_path(tmp_path):
    # 接反的守門：target 是磁碟上原本的位置，source 是 repo 內的複本，兩者不能對調。
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert entry.target == str(path)
    assert entry.source == f"files/{entry.hostname}/{str(path).lstrip('/').replace('/', '__')}"


def test_the_new_entry_is_in_the_config_list(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    listed = load((repo / CONFIG_LIST_NAME).read_text(encoding="utf-8"))
    assert [e.uid for e in listed.files] == [entry.uid]


def test_name_is_derived_from_the_target_path(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path, name="nav2_params.yaml")

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    # 最後兩層、去副檔名、底線轉連字號（T5）：managed/nav2_params.yaml → managed-nav2-params
    assert entry.name == "managed-nav2-params"


def test_uid_is_an_eight_char_base36(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert re.fullmatch(r"[0-9a-z]{8}", entry.uid)


def test_permissions_record_the_original_mode(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    os.chmod(path, 0o640)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert entry.permissions.mode == "0640"


def test_a_commit_is_recorded_with_the_import_kind(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    changes = history(str(repo), entry.uid)
    assert len(changes) == 1
    assert changes[0].kind == "import"
    assert changes[0].summary == f"{entry.name}@{entry.hostname}"


def test_the_ambiguity_note_goes_into_the_commit_body(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)
    note = "歧義確認：\n- 第 1 行「no」判定為字串"

    entry = onboard(str(repo), _request(root, path, note=note), _AUTHOR)

    assert history(str(repo), entry.uid)[0].body == note


def test_no_ambiguity_note_means_an_empty_commit_body(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path)

    entry = onboard(str(repo), _request(root, path), _AUTHOR)

    assert history(str(repo), entry.uid)[0].body == ""


def test_the_original_source_file_is_left_untouched(tmp_path):
    repo = _repo(tmp_path)
    root, path = _source(tmp_path, content=b"max_vel: 0.8\n")

    onboard(str(repo), _request(root, path), _AUTHOR)

    assert path.read_bytes() == b"max_vel: 0.8\n"


def test_a_source_outside_the_whitelist_is_refused(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.yaml"
    secret.write_bytes(b"stolen\n")
    # 白名單只涵蓋 managed/，來源在 outside/。
    root = tmp_path / "managed"
    root.mkdir()

    request = OnboardRequest(source_path=str(secret), fmt="yaml", allowed_roots=(str(root),))
    with pytest.raises(SourceOutsideRoots):
        onboard(str(repo), request, _AUTHOR)


def test_refusal_leaves_the_repo_untouched(tmp_path):
    repo = _repo(tmp_path)
    before = (repo / CONFIG_LIST_NAME).read_text(encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.yaml"
    secret.write_bytes(b"stolen\n")
    root = tmp_path / "managed"
    root.mkdir()

    request = OnboardRequest(source_path=str(secret), fmt="yaml", allowed_roots=(str(root),))
    with pytest.raises(SourceOutsideRoots):
        onboard(str(repo), request, _AUTHOR)

    assert (repo / CONFIG_LIST_NAME).read_text(encoding="utf-8") == before
    assert not (repo / "files").exists()
