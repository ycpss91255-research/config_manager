"""T26 — 備份與還原（#52）的設定庫層。測試介面：io/backup 的 bundle / restore。

對真實的臨時 git repo 操作。備份是 `git bundle`，只含**已提交**的東西；還原是從 bundle clone 進一個
**空**目錄。兩邊都以「真的還原得出來」為準，不以「檔案存在」為準：`git bundle verify` 對截斷的
bundle 會說 okay（它只看表頭），只有實際 clone 會抓到——所以備份完成的定義是 clone 進一個暫存目錄
成功且 fsck 乾淨。還原後的內容透過既有介面（`history()`、檔案）核對。
"""

import os
import pathlib
import subprocess
import threading

import pytest

from config_manager.io.backup import bundle, restore
from config_manager.io.errors import BackupUnverifiable, RestoreFailed, RestoreTargetOccupied
from config_manager.io.git import head, history, record, stage

AUTHOR = "劉宇盈 <yy@example.invalid>"
_SEED = ["-c", "user.name=seed", "-c", "user.email=seed@example.invalid"]


def _repo(tmp_path):
    """一個像 config-repo 的臨時 repo：清單檔、白名單、一份來源、一筆變更紀錄。"""
    path = tmp_path / "config-repo"
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "config-list.toml").write_text("list_version = 1\n", encoding="utf-8")
    (path / "allowed-roots.toml").write_text("roots_version = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), *_SEED, "commit", "-q", "-m", "chore: 起點"], check=True
    )
    (path / "files").mkdir()
    (path / "files" / "nav2.yaml").write_text("max_vel: 0.8\n", encoding="utf-8")
    stage(str(path), "files/nav2.yaml")
    record(str(path), "mfz3k9q1", "cfg", "調整 max_vel 至 0.8", AUTHOR)
    return path


def _commit_empty(repo, message):
    subprocess.run(
        ["git", "-C", str(repo), *_SEED, "commit", "-q", "--allow-empty", "-m", message], check=True
    )


def _is_ancestor(repo, sha, descendant):
    return (
        subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, descendant], check=False
        ).returncode
        == 0
    )


# ── 備份 ──────────────────────────────────────────────────────────────────────


def test_a_backup_records_the_head_it_contains_and_the_file_exists(tmp_path):
    repo = _repo(tmp_path)

    made = bundle(str(repo), str(tmp_path / "backups"))

    assert (pathlib.Path(made.path).is_file(), made.head) == (True, head(str(repo)))


def test_a_backup_file_is_named_after_the_moment_and_the_head(tmp_path):
    # 一個目錄裡放很多份備份時，名字就要說得出它是哪一刻、哪一版的——不必打開才知道。
    repo = _repo(tmp_path)

    made = bundle(str(repo), str(tmp_path / "backups"))

    name = pathlib.Path(made.path).name
    assert name.startswith("config-repo-") and name.endswith(f"-{made.head[:7]}.bundle")


def test_a_backup_leaves_out_uncommitted_and_untracked_files(tmp_path):
    # 備份的範圍是**已提交**的東西（#52 定案 1）：改了沒提交的、新加沒追蹤的，都不在裡面。
    repo = _repo(tmp_path)
    (repo / "config-list.toml").write_text("list_version = 1\n# 沒提交的修改\n", encoding="utf-8")
    (repo / "files" / "orphan.yaml").write_text("x: 1\n", encoding="utf-8")

    made = bundle(str(repo), str(tmp_path / "backups"))
    restored = restore(made.path, str(tmp_path / "restored"))

    assert (
        (tmp_path / "restored" / "config-list.toml").read_text(encoding="utf-8"),
        (tmp_path / "restored" / "files" / "orphan.yaml").exists(),
        restored.head,
    ) == ("list_version = 1\n", False, made.head)


def test_a_backup_taken_while_changes_are_being_recorded_is_still_a_complete_version(tmp_path):
    # 備份不停服務（#52 定案 2）：進版可能正在寫 commit。bundle 拿到的那個 HEAD 必須是歷史上
    # 真的存在過的一個完整版本——不是半筆 commit，也不是一個對不到任何版本的狀態。
    repo = _repo(tmp_path)
    stop = threading.Event()

    def keep_recording():
        n = 0
        while not stop.is_set():
            _commit_empty(repo, f"chore: 進版中 {n}")
            n += 1

    writer = threading.Thread(target=keep_recording)
    writer.start()
    try:
        made = [bundle(str(repo), str(tmp_path / "backups")) for _ in range(5)]
    finally:
        stop.set()
        writer.join()

    final = head(str(repo))
    assert all(_is_ancestor(repo, m.head, final) for m in made)
    assert all(restore(m.path, str(tmp_path / f"r{i}")).head == m.head for i, m in enumerate(made))


def test_a_backup_that_cannot_be_restored_is_not_reported_as_made(tmp_path, monkeypatch):
    # 「備份完成」的定義是 clone 得出來。讓 clone 失敗，備份就不算做成，而且不留下一個會被
    # 當成好備份的檔案。
    repo = _repo(tmp_path)
    real_run = subprocess.run

    def failing_clone(command, *args, **kwargs):
        if "clone" in command:
            raise subprocess.CalledProcessError(128, command, stderr="fatal: index-pack died")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", failing_clone)

    with pytest.raises(BackupUnverifiable) as caught:
        bundle(str(repo), str(tmp_path / "backups"))

    assert "index-pack died" in str(caught.value)
    assert list((tmp_path / "backups").glob("*")) == []


# ── 還原 ──────────────────────────────────────────────────────────────────────


def test_restoring_into_an_empty_directory_brings_back_the_list_the_sources_and_the_history(
    tmp_path,
):
    repo = _repo(tmp_path)
    made = bundle(str(repo), str(tmp_path / "backups"))
    target = tmp_path / "restored"
    target.mkdir()

    restored = restore(made.path, str(target))

    entries = history(str(target), "mfz3k9q1")
    assert (restored.head, head(str(target))) == (made.head, made.head)
    assert [(e.kind, e.summary, e.author) for e in entries] == [
        ("cfg", "調整 max_vel 至 0.8", AUTHOR)
    ]
    assert (target / "files" / "nav2.yaml").read_text(encoding="utf-8") == "max_vel: 0.8\n"
    assert (target / "allowed-roots.toml").read_text(encoding="utf-8") == "roots_version = 1\n"


def test_a_restored_repo_is_clean_and_carries_no_remote_pointing_at_the_backup_file(tmp_path):
    # 還原出來的要是一份能直接服務的 config-repo：工作區乾淨、沒有一個指向備份檔的 origin
    # ——原本的 repo 沒有遠端，還原後也不該憑空多一個。
    repo = _repo(tmp_path)
    made = bundle(str(repo), str(tmp_path / "backups"))

    restore(made.path, str(tmp_path / "restored"))

    status = subprocess.run(
        ["git", "-C", str(tmp_path / "restored"), "status", "--porcelain"],
        capture_output=True, text=True, check=True,
    ).stdout
    remotes = subprocess.run(
        ["git", "-C", str(tmp_path / "restored"), "remote"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert (status, remotes) == ("", "")


def test_restoring_into_a_directory_with_anything_in_it_is_refused_and_touches_nothing(tmp_path):
    # 只還原到空的設定庫（#52 定案 3）：有東西就拒絕、不清空——打錯指令不該蓋掉現有資料。
    repo = _repo(tmp_path)
    made = bundle(str(repo), str(tmp_path / "backups"))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / ".keep").write_text("既有資料\n", encoding="utf-8")

    with pytest.raises(RestoreTargetOccupied) as caught:
        restore(made.path, str(occupied))

    assert str(occupied) in str(caught.value)
    assert sorted(p.name for p in occupied.iterdir()) == [".keep"]
    assert (occupied / ".keep").read_text(encoding="utf-8") == "既有資料\n"


def test_a_corrupt_backup_is_refused_before_the_destination_is_touched(tmp_path):
    # 先驗證備份有效、再開始還原（#52 定案 3）。截斷的 bundle 連 `git bundle verify` 都說 okay，
    # 所以驗證＝真的 clone 一次；驗不過，目的地連建都不建。
    repo = _repo(tmp_path)
    made = bundle(str(repo), str(tmp_path / "backups"))
    corrupt = tmp_path / "corrupt.bundle"
    corrupt.write_bytes(pathlib.Path(made.path).read_bytes()[:-40])

    with pytest.raises(BackupUnverifiable) as caught:
        restore(str(corrupt), str(tmp_path / "restored"))

    assert str(corrupt) in str(caught.value)
    assert not (tmp_path / "restored").exists()


def test_a_backup_of_something_that_is_not_a_config_repo_is_refused(tmp_path):
    # bundle 是 git 的通用格式；一份別的 repo 的 bundle 還原進來，服務會在啟動檢查才爆。
    # 這裡就認：沒有清單檔的不是 config-repo 的備份。
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    (other / "README.md").write_text("不是 config-repo\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(other), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(other), *_SEED, "commit", "-q", "-m", "init"], check=True)
    foreign = tmp_path / "foreign.bundle"
    subprocess.run(["git", "-C", str(other), "bundle", "create", str(foreign), "--all"],
                   check=True, capture_output=True)

    with pytest.raises(BackupUnverifiable) as caught:
        restore(str(foreign), str(tmp_path / "restored"))

    assert "config-list.toml" in str(caught.value)
    assert not (tmp_path / "restored").exists()


def test_a_restore_that_fails_midway_leaves_the_destination_as_it_was(tmp_path, monkeypatch):
    # 失敗時不得留下被誤認為成功的設定庫（#52 定案 3）：驗證過了、往目的地 clone 時才失敗，
    # 目的地要回到還原前的樣子——原本是空目錄就還是空目錄，原本不存在就還是不存在。
    repo = _repo(tmp_path)
    made = bundle(str(repo), str(tmp_path / "backups"))
    existed_empty = tmp_path / "existed"
    existed_empty.mkdir()
    real_run = subprocess.run

    def clone_into_destination_fails(command, *args, **kwargs):
        if "clone" in command and command[-1] in (str(existed_empty), str(tmp_path / "absent")):
            # 先寫一點東西進去，再死——模擬 clone 中途斷掉留下半個 repo。
            pathlib.Path(command[-1]).mkdir(exist_ok=True)
            pathlib.Path(command[-1], ".git").mkdir()
            raise subprocess.CalledProcessError(128, command, stderr="fatal: 模擬中斷")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", clone_into_destination_fails)

    with pytest.raises(RestoreFailed) as first:
        restore(made.path, str(existed_empty))
    with pytest.raises(RestoreFailed) as second:
        restore(made.path, str(tmp_path / "absent"))

    assert "模擬中斷" in str(first.value) and "模擬中斷" in str(second.value)
    assert (list(existed_empty.iterdir()), (tmp_path / "absent").exists()) == ([], False)


def test_restoring_over_a_file_is_refused(tmp_path):
    repo = _repo(tmp_path)
    made = bundle(str(repo), str(tmp_path / "backups"))
    not_a_dir = tmp_path / "a-file"
    not_a_dir.write_text("x", encoding="utf-8")

    with pytest.raises(RestoreTargetOccupied):
        restore(made.path, str(not_a_dir))

    assert not_a_dir.read_text(encoding="utf-8") == "x"


def test_a_missing_backup_file_is_named_in_the_refusal(tmp_path):
    with pytest.raises(BackupUnverifiable) as caught:
        restore(str(tmp_path / "nowhere.bundle"), str(tmp_path / "restored"))

    assert "nowhere.bundle" in str(caught.value) and not (tmp_path / "restored").exists()


def test_the_backup_directory_is_created_when_it_does_not_exist_yet(tmp_path):
    repo = _repo(tmp_path)

    made = bundle(str(repo), str(tmp_path / "deep" / "backups"))

    assert pathlib.Path(made.path).parent == tmp_path / "deep" / "backups"
    assert os.access(made.path, os.R_OK)
