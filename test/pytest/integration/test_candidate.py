"""T24 — 候選檔案數預覽。io/candidate 的整合規格（#206，設計 §7.9）。

整合層：真的碰檔案系統，符號連結也用真的建（比照 test_source.py）。這一支**不以白名單為
閘門**——那正是它與 browse（T9）／read_source（T22）的差別：新增流程要數的前綴依定義還在
白名單外。逃逸範式比照 io/browse：realpath、字面 .. 先擋、每層 O_NOFOLLOW 不跟隨連結。
"""

import errno
import os

import pytest

from config_manager.io.candidate import CandidateCount, count_candidates
from config_manager.io.errors import (
    CandidateNotADirectory,
    CandidatePrefixEscape,
    CandidateUnreadable,
)

# depth-limit 測試造的巢狀層數；具名以避開對字面量的比較（ruff PLR2004）。
_DEEP_LEVELS = 5
# unreadable-subdir 測試：第 2 次 scandir（進到子目錄那次）才拋 EACCES，頂層照常。
_SUBDIR_SCANDIR_CALL = 2


def test_counts_regular_files_recursively(tmp_path):
    (tmp_path / "a.yaml").write_text("x")
    (tmp_path / "b.conf").write_text("y")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.ini").write_text("z")
    (sub / "deep").mkdir()
    (sub / "deep" / "d.toml").write_text("w")

    assert count_candidates(str(tmp_path)) == CandidateCount(count=4, capped=False)


def test_directories_are_not_counted_only_recursed(tmp_path):
    # 目錄本身不計入——數的是可納管的「檔」。這裡兩個目錄、一個檔 → 1。
    (tmp_path / "only-a-dir").mkdir()
    (tmp_path / "another-dir").mkdir()
    (tmp_path / "f.yaml").write_text("x")

    assert count_candidates(str(tmp_path)).count == 1


def test_an_empty_directory_counts_zero_and_is_not_capped(tmp_path):
    assert count_candidates(str(tmp_path)) == CandidateCount(count=0, capped=False)


def test_a_file_symlink_is_not_counted(tmp_path):
    # 以項目自身型別分類：link.yaml 是符號連結、不是一般檔，不計入。只有 real.yaml 算。
    (tmp_path / "real.yaml").write_text("x")
    os.symlink(tmp_path / "real.yaml", tmp_path / "link.yaml")

    assert count_candidates(str(tmp_path)).count == 1


def test_a_directory_symlink_is_not_followed(tmp_path):
    # 一條指向 prefix 外的目錄連結不被跟進——否則指向 /etc 的連結會把它底下的檔數進來。
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.yaml").write_text("1")
    (outside / "y.yaml").write_text("2")
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "own.yaml").write_text("0")
    os.symlink(outside, prefix / "link-to-outside")

    assert count_candidates(str(prefix)).count == 1  # 只有 own.yaml


def test_content_is_never_read_so_an_unreadable_file_still_counts(tmp_path):
    # 只 stat 型別、不讀內容——一個 0000 的檔仍被數到，證明計數不讀內容。
    secret = tmp_path / "secret.yaml"
    secret.write_text("x")
    secret.chmod(0o000)

    assert count_candidates(str(tmp_path)).count == 1


def test_depth_limit_caps_and_returns_a_partial_count(tmp_path):
    # 5 層深、每層一個檔；max_depth=2 → 只數到淺層，回部分計數 + capped。
    here = tmp_path
    for i in range(_DEEP_LEVELS):
        (here / f"f{i}.yaml").write_text("x")
        here = here / f"level{i}"
        here.mkdir()

    result = count_candidates(str(tmp_path), max_depth=2)

    assert result.capped is True
    assert 0 < result.count < _DEEP_LEVELS


def test_item_limit_caps_and_returns_a_partial_count(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.yaml").write_text("x")
    cap = 3

    result = count_candidates(str(tmp_path), max_items=cap)

    assert result.capped is True
    assert result.count <= cap


def test_a_literal_dotdot_in_the_prefix_is_refused_without_walking(tmp_path):
    # realpath 會把 .. 正規化掉、遮蔽逃逸意圖，故在解析前以字面擋下（比照 T4／T8）。
    with pytest.raises(CandidatePrefixEscape):
        count_candidates(str(tmp_path / "sub" / ".." / "x"))


def test_a_nonexistent_prefix_is_a_named_error_not_zero(tmp_path):
    # 回具名錯誤而非 0：0 會把「路徑打錯」誤報成「這裡沒有可納管檔」。
    with pytest.raises(CandidateNotADirectory):
        count_candidates(str(tmp_path / "does-not-exist"))


def test_a_prefix_that_is_a_file_is_a_named_error(tmp_path):
    f = tmp_path / "f.yaml"
    f.write_text("x")

    with pytest.raises(CandidateNotADirectory):
        count_candidates(str(f))


def test_an_unreadable_prefix_is_named_unreadable_not_not_a_directory(tmp_path, monkeypatch):
    # 前綴是目錄卻列不出來（權限）與「不是目錄」是兩種不同的下一步（§0.4）。以 monkeypatch
    # 強制開檔 EACCES，測試才不必真的降權（root 無視權限，比照 #232 的非 root 測試困境）。
    target = tmp_path / "locked"
    target.mkdir()
    resolved = os.path.realpath(str(target))
    real_open = os.open

    def fake_open(path, flags, *args, **kwargs):
        if path == resolved:
            raise OSError(errno.EACCES, "Permission denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fake_open)

    with pytest.raises(CandidateUnreadable):
        count_candidates(str(target))


def test_an_unreadable_subdirectory_mid_walk_is_skipped_not_fatal(tmp_path, monkeypatch):
    # 深層某目錄列不出來時 best-effort 跳過、不讓整個預覽失敗（頂層讀不出來才是具名錯誤）。
    (tmp_path / "a.yaml").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.yaml").write_text("y")
    real_scandir = os.scandir

    def fake_scandir(target):
        # 只讓子目錄那次 scandir 爆；頂層照常。以 fd 走訪，故以「非頂層 fd」近似——
        # 簡化為：第二次呼叫（子目錄）拋 EACCES。
        fake_scandir.calls += 1
        if fake_scandir.calls >= _SUBDIR_SCANDIR_CALL:
            raise OSError(errno.EACCES, "Permission denied")
        return real_scandir(target)

    fake_scandir.calls = 0
    monkeypatch.setattr(os, "scandir", fake_scandir)

    result = count_candidates(str(tmp_path))

    # 頂層的 a.yaml 數到了；子目錄列不出來被跳過，整體不失敗。
    assert result.count == 1
    assert result.capped is False


def test_a_subdirectory_that_cannot_be_opened_mid_walk_is_skipped(tmp_path, monkeypatch):
    # TOCTOU／權限：一個在 scandir 當下還是目錄、卻在開檔時失敗的子目錄（被抽換成連結、或
    # 無權限）被跳過、不失敗、不跟隨（O_NOFOLLOW 的防線）。以 monkeypatch 讓子目錄那次
    # dir_fd 相對開檔拋 ELOOP，測試才不必真的造競速。
    (tmp_path / "a.yaml").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.yaml").write_text("y")
    real_open = os.open

    def fake_open(path, flags, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and path == "sub":
            raise OSError(errno.ELOOP, "Too many levels of symbolic links")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fake_open)

    result = count_candidates(str(tmp_path))

    # 頂層 a.yaml 數到；sub 開不成被跳過（b.yaml 不計入），整體不失敗。
    assert result.count == 1
    assert result.capped is False
