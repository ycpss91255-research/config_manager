"""io/allowed_roots — 白名單設定檔的讀取與新增（§7.9, #202）。

效果透過既有介面觀察：檔案內容以 read_allowed_roots 讀回（core 的 T23 驗結構），
preflight 的缺失／不可解析屬 T15。新增當下的 realpath 正規化、到不了的目錄拒絕，以及
追加後的 git commit，是這一層的 I/O 守衛，以真實檔案系統與 git 在整合層直接斷言
（比照 io/onboard 對 #172／#173 的處理）。用 tmp_path。
"""

import subprocess

import pytest

from config_manager.core.errors import InvalidPrefix, PrefixNotFound
from config_manager.io import allowed_roots as allowed_roots_module
from config_manager.io.allowed_roots import (
    add_allowed_root,
    read_allowed_roots,
    remove_allowed_root,
    root_prefixes,
)
from config_manager.io.errors import (
    AllowedRootLeftBehind,
    AllowedRootUnreachable,
    AllowedRootsMissing,
    AllowedRootsUnparsable,
    TargetNotWritable,
)

AUTHOR = "劉宇盈 <yy@example.invalid>"

# main() 參數個數不符時的回傳碼（比照 preflight.main 的 usage 分支）。具名以避開對字面 2
# 的比較（ruff PLR2004 豁免 0／1，不豁免 2）。
_USAGE_EXIT = 2

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


def test_when_rollback_hits_a_writer_error_it_raises_left_behind(tmp_path, monkeypatch):
    # #213（io/allowed_roots :105）：回滾還原設定檔那一步的 replace_atomically 丟 WriterError
    # （非 OSError 子類，如 repo 根對服務身分不可寫→TargetNotWritable）。except 只接
    # (OSError, CalledProcessError) 會讓它逃出→白名單靜默擴張、無 commit、不發
    # AllowedRootLeftBehind。except 涵蓋 WriterError 後大聲失敗指名該設定檔。
    repo = _repo(tmp_path)
    newdir = tmp_path / "etc-robot"
    newdir.mkdir()

    def commit_boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(allowed_roots_module, "commit", commit_boom)

    seen = []
    real_replace = allowed_roots_module.replace_atomically

    def replace_then_writer_error(*args, **kwargs):
        seen.append(1)
        if len(seen) == 1:
            return real_replace(*args, **kwargs)  # 新增當下寫成功
        raise TargetNotWritable("回滾還原設定檔時 repo 根不可寫")

    monkeypatch.setattr(allowed_roots_module, "replace_atomically", replace_then_writer_error)

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


def test_removing_a_root_deletes_it_and_leaves_the_others(tmp_path):
    # 白名單維護要能減根（#15）。以檔案原樣儲存的 prefix 定位（種子字面值），刪掉那一筆、
    # 其餘保留。
    repo = _repo(tmp_path)
    newdir = tmp_path / "etc-robot"
    newdir.mkdir()
    add_allowed_root(str(repo), str(newdir), AUTHOR, "2026-09-12T09:00:00Z")

    remove_allowed_root(str(repo), "/opt/robot/config", AUTHOR)

    assert [root.prefix for root in read_allowed_roots(str(repo)).roots] == [str(newdir)]


def test_removing_a_root_commits_the_change_leaving_the_tree_clean(tmp_path):
    repo = _repo(tmp_path)

    remove_allowed_root(str(repo), "/opt/robot/config", AUTHOR)

    # 移除自己提交了：工作區乾淨，最新一筆 commit 由移除者署名。
    assert _git(repo, "status", "--porcelain") == ""
    assert "劉宇盈" in _git(repo, "log", "-1", "--format=%an <%ae>")


def test_removing_a_prefix_that_is_not_in_the_file_raises_prefix_not_found(tmp_path):
    # 移除以檔案原樣 prefix 定位；定位不到就大聲失敗、指名（不變式 2），不靜默成 no-op。
    repo = _repo(tmp_path)

    with pytest.raises(PrefixNotFound) as exc:
        remove_allowed_root(str(repo), "/opt/robot/nope", AUTHOR)

    assert "/opt/robot/nope" in str(exc.value)


def test_a_failed_commit_rolls_the_removal_back_so_the_whitelist_is_not_silently_shrunk(
    tmp_path, monkeypatch
):
    # 白名單每次請求從檔讀，檔一改就生效。移除的 commit 沒成時要把設定檔還原到移除前，
    # 否則白名單被靜默縮減卻沒有 git 稽核（與新增的回滾對稱）。
    repo = _repo(tmp_path)

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git", "commit"])

    monkeypatch.setattr(allowed_roots_module, "commit", boom)

    with pytest.raises(subprocess.CalledProcessError):
        remove_allowed_root(str(repo), "/opt/robot/config", AUTHOR)

    # 回滾了：該根仍在白名單裡，工作區也乾淨（沒有留下未提交的縮減）。
    assert "/opt/robot/config" in [root.prefix for root in read_allowed_roots(str(repo)).roots]
    assert _git(repo, "status", "--porcelain") == ""


def test_when_removal_rollback_itself_fails_it_raises_left_behind_naming_the_file(
    tmp_path, monkeypatch
):
    # 回滾也失敗的場景：同時說出原本的失敗與清理的失敗，指名殘留了什麼（比照新增）。
    repo = _repo(tmp_path)

    def boom(*_args, **_kwargs):
        raise subprocess.CalledProcessError(1, ["git"])

    monkeypatch.setattr(allowed_roots_module, "commit", boom)
    monkeypatch.setattr(allowed_roots_module, "unstage", boom)  # 回滾的清理也失敗

    with pytest.raises(AllowedRootLeftBehind) as exc:
        remove_allowed_root(str(repo), "/opt/robot/config", AUTHOR)

    assert "allowed-roots.toml" in str(exc.value)


def test_main_prints_the_files_root_prefixes_one_per_line(tmp_path, capsys):
    # entrypoint 的開機可見性檢查（#146）以這個取生效白名單的根：以檔為準（#202），
    # 不是 CM_ALLOWED_ROOTS——首啟後兩者會漂移（#232 發現5）。一行一個，便於 shell 迭代。
    repo = _repo(tmp_path)

    code = allowed_roots_module.main(["config_manager.io.allowed_roots", str(repo)])

    assert code == 0
    assert capsys.readouterr().out.splitlines() == ["/opt/robot/config"]


def test_main_reports_a_usage_error_when_not_given_exactly_one_repo(capsys):
    code = allowed_roots_module.main(["config_manager.io.allowed_roots"])

    assert code == _USAGE_EXIT
    assert "usage" in capsys.readouterr().err


def test_main_names_a_missing_file_on_stderr_without_a_traceback(tmp_path, capsys):
    # 種子後不該缺檔；真缺了（被刪／掛錯）要具名指路、回非零碼，而不是丟 traceback——
    # entrypoint 據以大聲失敗（比照 preflight.main 只收具名例外）。
    empty = tmp_path / "empty-repo"
    empty.mkdir()

    code = allowed_roots_module.main(["config_manager.io.allowed_roots", str(empty)])

    assert code == 1
    assert "allowed-roots.toml" in capsys.readouterr().err
