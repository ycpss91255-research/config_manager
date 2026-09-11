"""io/allowed_roots — 白名單設定檔的讀取與新增（§7.9, #202）。

效果透過既有介面觀察：檔案內容以 read_allowed_roots 讀回（core 的 T23 驗結構），
preflight 的缺失／不可解析屬 T15。新增當下的 realpath 正規化、到不了的目錄拒絕，以及
追加後的 git commit，是這一層的 I/O 守衛，以真實檔案系統與 git 在整合層直接斷言
（比照 io/onboard 對 #172／#173 的處理）。用 tmp_path。
"""

import subprocess

import pytest

from config_manager.core.errors import InvalidPrefix
from config_manager.io import allowed_roots as allowed_roots_module
from config_manager.io.allowed_roots import (
    add_allowed_root,
    read_allowed_roots,
    root_prefixes,
)
from config_manager.io.errors import (
    AllowedRootLeftBehind,
    AllowedRootUnreachable,
    AllowedRootsMissing,
    AllowedRootsUnparsable,
)

AUTHOR = "劉宇盈 <yy@example.invalid>"

_SEED = """\
roots_version = 1

[[roots]]
prefix   = "/opt/robot/config"
added_by = "部署設定 (CM_ALLOWED_ROOTS)"
added_at = "2026-09-11T00:00:00Z"
"""


def _repo(tmp_path):
    """一個有初始 commit、且白名單種子檔已提交的臨時 config-repo。"""
    path = tmp_path / "config-repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "allowed-roots.toml").write_text(_SEED, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path),
         "-c", "user.name=seed", "-c", "user.email=seed@example.invalid",
         "add", "allowed-roots.toml"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path),
         "-c", "user.name=seed", "-c", "user.email=seed@example.invalid",
         "commit", "-q", "-m", "chore: 種子"],
        check=True,
    )
    return path


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def test_reading_a_valid_file_returns_its_roots(tmp_path):
    repo = _repo(tmp_path)

    roots = read_allowed_roots(str(repo)).roots

    assert [root.prefix for root in roots] == ["/opt/robot/config"]


def test_root_prefixes_returns_just_the_prefix_strings(tmp_path):
    # browse／onboard／source 每次請求以這個取白名單，不必自己解析檔案。
    repo = _repo(tmp_path)

    assert root_prefixes(str(repo)) == ("/opt/robot/config",)


def test_a_missing_file_raises_named_exception(tmp_path):
    # 種子後不該發生——真發生就是有人刪了它或掛錯路徑（不變式 2）。
    empty = tmp_path / "empty-repo"
    empty.mkdir()

    with pytest.raises(AllowedRootsMissing):
        read_allowed_roots(str(empty))


def test_an_unparsable_file_raises_named_exception(tmp_path):
    repo = _repo(tmp_path)
    (repo / "allowed-roots.toml").write_text("roots_version = [unclosed\n", encoding="utf-8")

    with pytest.raises(AllowedRootsUnparsable):
        read_allowed_roots(str(repo))


def test_adding_a_root_appends_it_and_records_who_and_when(tmp_path):
    repo = _repo(tmp_path)
    newdir = tmp_path / "etc-robot"
    newdir.mkdir()

    add_allowed_root(str(repo), str(newdir), AUTHOR, "2026-09-12T09:00:00Z")

    roots = read_allowed_roots(str(repo)).roots
    added = next(root for root in roots if root.prefix == str(newdir))
    assert (added.added_by, added.added_at) == (AUTHOR, "2026-09-12T09:00:00Z")


def test_adding_a_root_commits_the_change_leaving_the_tree_clean(tmp_path):
    repo = _repo(tmp_path)
    newdir = tmp_path / "etc-robot"
    newdir.mkdir()

    add_allowed_root(str(repo), str(newdir), AUTHOR, "2026-09-12T09:00:00Z")

    # 追加自己提交了：工作區乾淨，最新一筆 commit 由新增者署名。
    assert _git(repo, "status", "--porcelain") == ""
    assert "劉宇盈" in _git(repo, "log", "-1", "--format=%an <%ae>")


def test_a_failed_commit_rolls_the_file_back_so_the_whitelist_is_not_silently_widened(
    tmp_path, monkeypatch
):
    # 白名單每次請求從檔讀，所以檔案一被寫入該根就生效。commit 沒成時要把設定檔還原到
    # 新增前，否則白名單被靜默擴張卻沒有 git 稽核（比照 onboard #173）。
    repo = _repo(tmp_path)
    newdir = tmp_path / "etc-robot"
    newdir.mkdir()

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(allowed_roots_module, "commit", boom)

    with pytest.raises(subprocess.CalledProcessError):
        add_allowed_root(str(repo), str(newdir), AUTHOR, "2026-09-12T09:00:00Z")

    # 回滾了：該根不在白名單裡，工作區也乾淨（沒有留下未提交的擴張）。
    assert str(newdir) not in [root.prefix for root in read_allowed_roots(str(repo)).roots]
    assert _git(repo, "status", "--porcelain") == ""


def test_when_rollback_itself_fails_it_raises_left_behind_naming_the_file(
    tmp_path, monkeypatch
):
    # 回滾（還原設定檔）也失敗的場景：同時說出原本的失敗與清理的失敗，指名殘留了什麼
    # （比照 onboard 的 OnboardLeftBehind，#173）。
    repo = _repo(tmp_path)
    newdir = tmp_path / "etc-robot"
    newdir.mkdir()

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git"])

    monkeypatch.setattr(allowed_roots_module, "commit", boom)
    monkeypatch.setattr(allowed_roots_module, "unstage", boom)  # 回滾的清理也失敗

    with pytest.raises(AllowedRootLeftBehind) as exc:
        add_allowed_root(str(repo), str(newdir), AUTHOR, "2026-09-12T09:00:00Z")

    assert "allowed-roots.toml" in str(exc.value)


def test_a_relative_prefix_is_refused(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(InvalidPrefix):
        add_allowed_root(str(repo), "etc/robot", AUTHOR, "2026-09-12T09:00:00Z")


def test_a_prefix_with_dotdot_is_refused(tmp_path):
    repo = _repo(tmp_path)

    with pytest.raises(InvalidPrefix):
        add_allowed_root(str(repo), "/opt/robot/../etc", AUTHOR, "2026-09-12T09:00:00Z")


def test_a_prefix_pointing_at_a_directory_that_is_not_there_is_refused(tmp_path):
    # 指向不可見／不存在的目錄大聲失敗、指名（不變式 2）。
    repo = _repo(tmp_path)
    ghost = tmp_path / "does-not-exist"

    with pytest.raises(AllowedRootUnreachable):
        add_allowed_root(str(repo), str(ghost), AUTHOR, "2026-09-12T09:00:00Z")


def test_a_symlinked_prefix_is_stored_as_its_realpath(tmp_path):
    # 正規化：存的是 realpath 解析後的目錄，不是 symlink 路徑。
    repo = _repo(tmp_path)
    real = tmp_path / "real-config"
    real.mkdir()
    link = tmp_path / "link-config"
    link.symlink_to(real)

    add_allowed_root(str(repo), str(link), AUTHOR, "2026-09-12T09:00:00Z")

    prefixes = [root.prefix for root in read_allowed_roots(str(repo)).roots]
    assert str(real) in prefixes
    assert str(link) not in prefixes
