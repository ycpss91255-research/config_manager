"""io/schema — 替一份已納管的 config 產生 schema 骨架並存進 config-repo（#38）。

編排層，同 `io/unmanage` 的處理：效果全透過既有介面觀察——清單檔條目的 `schema` 用
`core/config_list.load`（T1）、紀錄用 `io/git.history`（T7）、骨架本身看 `.schemas/` 底下的檔案
（內容由 T12 的 `draft_schema` 決定，這裡只擋「存下來的那份驗得過來源內容」）。這裡擋的是接線：
產生後檔案在、清單檔指得到它、有一筆 meta 紀錄、工作區乾淨；產生不了的說得出原因；寫入中途
失敗整批回滾。

整合層：真的碰檔案系統與 git。
"""

import json
import subprocess

import jsonschema
import pytest

from config_manager.core.config_list import load
from config_manager.io.errors import (
    SchemaExists,
    SchemaLeftBehind,
    SchemaNotFound,
    SchemaUnavailable,
    SchemaUnreadable,
)
from config_manager.io.git import history
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.schema import draft_skeleton, read_schema
from config_manager.io.unmanage import unmanage

_AUTHOR = "陳小明 <ming@example.com>"
_SEED = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""
_YAML = b"max_vel: 0.8\nretries: 3\nframes: [base, odom]\n"


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


def _git_state(repo):
    def _git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
        ).stdout
    return _git("rev-parse", "HEAD").strip(), _git("status", "--porcelain")


def _entry(repo, uid):
    listed = load((repo / CONFIG_LIST_NAME).read_text(encoding="utf-8")).files
    return next(item for item in listed if item.uid == uid)


def test_the_skeleton_is_stored_under_schemas_and_the_entry_points_at_it(tmp_path):
    # 骨架存在 config-repo 的 `.schemas/`，檔名用 uid（改名不會讓它對不上）；清單檔記下路徑。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)

    updated = draft_skeleton(str(repo), entry.uid, _AUTHOR)

    assert updated.schema_path == f".schemas/{entry.uid}.json"
    assert _entry(repo, entry.uid).schema_path == f".schemas/{entry.uid}.json"
    stored = json.loads((repo / ".schemas" / f"{entry.uid}.json").read_text(encoding="utf-8"))
    assert stored["properties"]["max_vel"] == {"type": "number"}
    jsonschema.validate({"max_vel": 0.8, "retries": 3, "frames": ["base", "odom"]}, stored)


def test_drafting_records_a_meta_change_and_leaves_the_tree_clean(tmp_path):
    # 隨 config 一同版控：骨架與清單檔的改動進同一筆紀錄，類型是 meta（內容沒動，§2.3）。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)

    draft_skeleton(str(repo), entry.uid, _AUTHOR)

    changes = history(str(repo), entry.uid)
    assert [change.kind for change in changes] == ["meta", "import"]
    assert changes[0].author == _AUTHOR
    assert _git_state(repo)[1] == ""


@pytest.mark.parametrize(
    ("name", "content", "fmt"),
    [
        ("a.yaml", b"robot:\n  max_vel: 0.8\n  retries: 3\n", "yaml"),
        ("a.toml", b"[robot]\nmax_vel = 0.8\nretries = 3\n", "toml"),
        ("a.json", b'{"robot": {"max_vel": 0.8, "retries": 3}}\n', "json"),
    ],
)
def test_equivalent_content_in_any_format_drafts_the_same_skeleton(tmp_path, name, content, fmt):
    # `.schemas/` 沿用 JSON Schema 的稱呼，不代表只適用 JSON 檔：描述的是解析後的資料結構。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo, name, content, fmt)

    draft_skeleton(str(repo), entry.uid, _AUTHOR)

    stored = json.loads((repo / ".schemas" / f"{entry.uid}.json").read_text(encoding="utf-8"))
    assert stored["properties"] == {
        "robot": {
            "type": "object",
            "properties": {"max_vel": {"type": "number"}, "retries": {"type": "integer"}},
        }
    }


def test_a_raw_entry_has_no_structure_to_draft_from(tmp_path):
    # raw 是刻意宣告「不解析、只做版控」——沒有解析後的結構，產生不了，也不假裝產生了。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo, "firmware.bin", b"\x00\x01\x02", "raw")
    before = _git_state(repo)

    with pytest.raises(SchemaUnavailable, match="raw"):
        draft_skeleton(str(repo), entry.uid, _AUTHOR)

    assert _git_state(repo) == before


def test_content_whose_top_level_is_not_an_object_cannot_be_drafted(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo, "list.yaml", b"- a\n- b\n", "yaml")
    before = _git_state(repo)

    with pytest.raises(SchemaUnavailable, match="頂層"):
        draft_skeleton(str(repo), entry.uid, _AUTHOR)

    assert _git_state(repo) == before


def test_an_existing_schema_is_not_overwritten(tmp_path):
    # 已有的 schema 可能被開發者收緊過；再產生一次不能悄悄蓋掉那些修改。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    draft_skeleton(str(repo), entry.uid, _AUTHOR)
    schema_file = repo / ".schemas" / f"{entry.uid}.json"
    schema_file.write_text('{"type": "object", "required": ["max_vel"]}\n', encoding="utf-8")

    with pytest.raises(SchemaExists, match=f".schemas/{entry.uid}.json"):
        draft_skeleton(str(repo), entry.uid, _AUTHOR)

    assert "required" in schema_file.read_text(encoding="utf-8")


def test_drafting_for_an_unknown_uid_is_a_named_error_and_touches_nothing(tmp_path):
    repo = _repo(tmp_path)
    _managed(tmp_path, repo)
    before = _git_state(repo)

    with pytest.raises(SchemaNotFound):
        draft_skeleton(str(repo), "zzzzzzz9", _AUTHOR)

    assert _git_state(repo) == before


def test_a_failed_commit_rolls_the_repo_back(tmp_path, monkeypatch):
    # 骨架寫了、清單檔改了、也 stage 了，commit 失敗 → 全部還原：骨架檔不留、清單檔沒有 schema。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    before = _git_state(repo)

    def _commit_boom(*_args, **_kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.schema.record", _commit_boom)

    with pytest.raises(RuntimeError, match="commit 壞了"):
        draft_skeleton(str(repo), entry.uid, _AUTHOR)

    assert _git_state(repo) == before
    assert not (repo / ".schemas" / f"{entry.uid}.json").exists()
    assert _entry(repo, entry.uid).schema_path is None


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

    monkeypatch.setattr("config_manager.io.schema.write_config_list", _write_then_fail)
    monkeypatch.setattr("config_manager.io.schema.record", _commit_boom)

    with pytest.raises(SchemaLeftBehind) as caught:
        draft_skeleton(str(repo), entry.uid, _AUTHOR)

    assert CONFIG_LIST_NAME in str(caught.value) and "commit 壞了" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_unmanaging_takes_the_schema_file_with_it(tmp_path):
    # 解除納管後不再追蹤這份 config，它的 schema 也不留在工作區當孤兒（歷史裡仍翻得到）。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    draft_skeleton(str(repo), entry.uid, _AUTHOR)

    unmanage(str(repo), entry.uid, _AUTHOR)

    assert not (repo / ".schemas" / f"{entry.uid}.json").exists()
    assert _git_state(repo)[1] == ""


def test_a_failed_unmanage_puts_the_schema_file_back(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    draft_skeleton(str(repo), entry.uid, _AUTHOR)
    schema_file = repo / ".schemas" / f"{entry.uid}.json"
    original = schema_file.read_bytes()
    before = _git_state(repo)

    def _commit_boom(*_args, **_kwargs):
        raise RuntimeError("commit 壞了")

    monkeypatch.setattr("config_manager.io.unmanage.record", _commit_boom)

    with pytest.raises(RuntimeError, match="commit 壞了"):
        unmanage(str(repo), entry.uid, _AUTHOR)

    assert _git_state(repo) == before
    assert schema_file.read_bytes() == original


# ── 讀 schema（第 2 層驗證要用的那一份，#39）──────────────────────────────────
# 讀不到或不合法一律大聲失敗：把壞掉的 schema 當成「沒有 schema」會讓把關悄悄消失（不變式 2／4）。


def test_an_entry_without_a_schema_reads_as_none(tmp_path):
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)

    assert read_schema(str(repo), entry) is None


def test_the_drafted_skeleton_reads_back_as_the_schema(tmp_path):
    repo = _repo(tmp_path)
    entry = draft_skeleton(str(repo), _managed(tmp_path, repo).uid, _AUTHOR)

    schema = read_schema(str(repo), entry)

    assert schema["properties"]["retries"] == {"type": "integer"}


def test_a_schema_file_that_went_missing_fails_loudly_naming_the_file(tmp_path):
    repo = _repo(tmp_path)
    entry = draft_skeleton(str(repo), _managed(tmp_path, repo).uid, _AUTHOR)
    (repo / entry.schema_path).unlink()

    with pytest.raises(SchemaUnreadable, match="不存在") as caught:
        read_schema(str(repo), entry)

    assert caught.value.file == str(repo / entry.schema_path)


def test_a_schema_file_that_is_not_json_fails_loudly(tmp_path):
    repo = _repo(tmp_path)
    entry = draft_skeleton(str(repo), _managed(tmp_path, repo).uid, _AUTHOR)
    (repo / entry.schema_path).write_text("{ not json", encoding="utf-8")

    with pytest.raises(SchemaUnreadable, match="JSON"):
        read_schema(str(repo), entry)


@pytest.mark.parametrize("content", ['{"type": "nonsense"}', "[1, 2]", "true"])
def test_a_schema_file_that_is_not_a_usable_schema_fails_loudly(tmp_path, content):
    # 語法是 JSON、但不是一份可用的 schema（未知的型別名、頂層不是物件）。
    repo = _repo(tmp_path)
    entry = draft_skeleton(str(repo), _managed(tmp_path, repo).uid, _AUTHOR)
    (repo / entry.schema_path).write_text(content, encoding="utf-8")

    with pytest.raises(SchemaUnreadable):
        read_schema(str(repo), entry)


def test_a_schema_path_outside_the_schemas_directory_is_refused(tmp_path):
    # `schema` 是清單檔的欄位、可以手改；指到 `.schemas/` 以外的檔案不讀（比照 source 的逃逸檢查）。
    repo = _repo(tmp_path)
    entry = _managed(tmp_path, repo)
    (tmp_path / "outside.json").write_text('{"type": "object"}', encoding="utf-8")
    escaped = entry.model_copy(update={"schema_path": "../outside.json"})

    with pytest.raises(SchemaUnreadable, match=".schemas"):
        read_schema(str(repo), escaped)
