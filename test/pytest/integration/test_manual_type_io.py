"""io/manual_type — 把人工指定的型別寫進 config-repo 的 schema（#285、ADR-00000021）。

編排層，同 `io/schema` 的處理：效果全透過既有介面觀察——schema 的內容用 `io/schema.read_schema`
與 T12 的 `manual_types`、清單檔條目用 `core/config_list.load`（T1）、紀錄用 `io/git.history`
（T7）。指定與清除的規則本身在 T12（`core/manual_types`）；這裡擋的是接線：寫進去了、記了一筆
meta、與現值不相容的擋下、清到什麼都不剩時 schema 檔與條目的指向一起拿掉。

整合層：真的碰檔案系統與 git。
"""

import subprocess

import pytest

from config_manager.core.config_list import load
from config_manager.core.errors import PathNotSpecifiable, TypeIncompatible, TypeNotSpecified
from config_manager.core.manual_types import manual_types
from config_manager.io.errors import SchemaUnavailable
from config_manager.io.git import history
from config_manager.io.manual_type import specify_type
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.schema import draft_skeleton, read_schema

_AUTHOR = "陳小明 <ming@example.com>"
_SEED = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""
_YAML = b"timeout: 5\nmax_vel: 0.8\nname: amr01\nframes: [base, odom]\n"


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


def _managed(tmp_path, repo, name="nav2.yaml", content=_YAML, fmt="yaml"):
    root = tmp_path / "managed"
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(content)
    request = OnboardRequest(source_path=str(path), fmt=fmt, allowed_roots=(str(root),))
    return onboard(str(repo), request, _AUTHOR)


def _entry(repo, uid):
    listed = load((repo / CONFIG_LIST_NAME).read_text(encoding="utf-8")).files
    return next(item for item in listed if item.uid == uid)


def _status(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True,
        check=True,
    ).stdout


def test_specifying_on_a_config_without_a_schema_creates_one_and_records_it(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)

    updated = specify_type(str(repo), entry.uid, "timeout", "float", _AUTHOR)

    assert updated.schema_path == f".schemas/{entry.uid}.json"
    assert manual_types(read_schema(str(repo), _entry(repo, entry.uid))) == {"timeout": "float"}
    latest = history(str(repo), entry.uid)[0]
    assert (latest.kind, latest.summary, latest.author) == (
        "meta", "指定「timeout」的型別為 double", _AUTHOR,
    )
    assert _status(repo) == ""


def test_specifying_on_an_existing_skeleton_changes_only_that_field(tmp_path):
    repo = _repo(tmp_path)
    entry = draft_skeleton(str(repo), _managed(tmp_path, repo).uid, _AUTHOR)

    specify_type(str(repo), entry.uid, "timeout", "float", _AUTHOR)

    schema = read_schema(str(repo), _entry(repo, entry.uid))
    assert schema["properties"]["timeout"]["type"] == "number"
    assert schema["properties"]["max_vel"] == {"type": "number"}  # 其餘欄位原樣
    assert manual_types(schema) == {"timeout": "float"}


def test_a_type_the_current_value_does_not_fit_is_refused_and_nothing_is_written(tmp_path):
    # 0.8 不是整數、amr01 不是布林：指定下去這份 config 當場就不合格，所以擋下、說出原因。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    head = history(str(repo), entry.uid)[0].sha

    with pytest.raises(TypeIncompatible, match="max_vel"):
        specify_type(str(repo), entry.uid, "max_vel", "int", _AUTHOR)
    with pytest.raises(TypeIncompatible, match="name"):
        specify_type(str(repo), entry.uid, "name", "bool", _AUTHOR)

    assert _entry(repo, entry.uid).schema_path is None
    assert history(str(repo), entry.uid)[0].sha == head and _status(repo) == ""


@pytest.mark.parametrize("path", ["nope", "frames", "frames[0]"])
def test_only_an_existing_single_parameter_can_be_specified(tmp_path, path):
    # 這份 config 沒有的參數、整個清單、清單的某個元素——都不能指定型別。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)

    with pytest.raises(PathNotSpecifiable):
        specify_type(str(repo), entry.uid, path, "string", _AUTHOR)

    assert _status(repo) == ""


def test_clearing_one_of_two_specifications_keeps_the_other_and_records_it(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    specify_type(str(repo), entry.uid, "timeout", "float", _AUTHOR)
    specify_type(str(repo), entry.uid, "max_vel", "float", _AUTHOR)

    specify_type(str(repo), entry.uid, "timeout", None, _AUTHOR)

    assert manual_types(read_schema(str(repo), _entry(repo, entry.uid))) == {"max_vel": "float"}
    latest = history(str(repo), entry.uid)[0]
    assert (latest.kind, latest.summary) == ("meta", "清除「timeout」的型別指定")


def test_clearing_the_last_specification_removes_the_schema_it_created(tmp_path):
    # 那份 schema 是為了指定才建立的；清完不留空殼——檔案拿掉、條目不再指到它，之後可以產生骨架。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    specify_type(str(repo), entry.uid, "timeout", "float", _AUTHOR)

    updated = specify_type(str(repo), entry.uid, "timeout", None, _AUTHOR)

    assert updated.schema_path is None and _entry(repo, entry.uid).schema_path is None
    assert not (repo / ".schemas" / f"{entry.uid}.json").exists()
    assert _status(repo) == ""
    draft_skeleton(str(repo), entry.uid, _AUTHOR)  # 不被空殼擋住


def test_clearing_what_was_never_specified_is_a_named_error(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    skeleton = draft_skeleton(str(repo), _managed(tmp_path, repo, "b.yaml").uid, _AUTHOR)

    with pytest.raises(TypeNotSpecified):
        specify_type(str(repo), entry.uid, "timeout", None, _AUTHOR)  # 連 schema 都沒有
    with pytest.raises(TypeNotSpecified):
        specify_type(str(repo), skeleton.uid, "timeout", None, _AUTHOR)  # 有骨架、沒指定過


def test_a_raw_config_has_no_parameters_to_specify(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo, "firmware.bin", b"\x00\x01", "raw")

    with pytest.raises(SchemaUnavailable, match="raw"):
        specify_type(str(repo), entry.uid, "anything", "int", _AUTHOR)


def test_a_failed_commit_rolls_the_specification_back(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)

    def _commit_boom(*_args, **_kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.schema.record", _commit_boom)

    with pytest.raises(RuntimeError, match="commit 壞了"):
        specify_type(str(repo), entry.uid, "timeout", "float", _AUTHOR)

    assert _status(repo) == ""
    assert not (repo / ".schemas" / f"{entry.uid}.json").exists()
    assert _entry(repo, entry.uid).schema_path is None
