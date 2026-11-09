"""io/promote 的回滾（T9／T8；F29）：還原不只是位元組，owner／group／mode 也要回來；回滾未竟的例外
要指名殘留並把原本的失敗掛在 `__cause__`。系統層（test_api）驗經 HTTP 的樣子，這裡直接呼叫 `apply`。

以服務身分（非 root）跑：owner／group 只能驗「沒被改成別的」，mode 用 0640 和暫存檔的 0600 區分。
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import stat
import subprocess
import threading

import pytest

from config_manager.core.drafts import Promotion
from config_manager.core.models import Permissions
from config_manager.io import promote as promote_io
from config_manager.io.errors import NotARegularFile, PromoteLeftBehind, TargetNotWritable
from config_manager.io.git import head
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.promote import apply

_AUTHOR = "陳小明 <ming@example.com>"
_SECOND = 2  # 第 2 份：第 1 份已寫出、已記錄，回滾才有東西可還原
_MODE_BEFORE = 0o640  # 和暫存檔的 0600 不同，還原有沒有回復 mode 才看得出來
_SEED = """\
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
"""


def _repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "files").mkdir(parents=True)
    (repo / CONFIG_LIST_NAME).write_text(_SEED, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=seed", "-c", "user.email=s@e.x",
         "commit", "-q", "-m", "chore: 種下清單檔"],
        check=True,
    )
    return repo


def _plan(tmp_path, repo, name, text, before):
    """一份要進版的 config：來源複本在 repo/files、目標在 tmp_path/targets，
    目標先放進版前的內容。"""
    targets = tmp_path / "targets"
    targets.mkdir(exist_ok=True)
    target = targets / f"{name}.yaml"
    target.write_text(before, encoding="utf-8")
    (repo / "files" / f"{name}.yaml").write_text(before, encoding="utf-8")
    permissions = Permissions(owner=str(os.getuid()), group=str(os.getgid()), mode="0644")
    return Promotion(
        uid=f"mfz3k9q{name}", source=f"files/{name}.yaml", target=str(target), text=text,
        permissions=permissions, summary=f"修改參數（{name}@amr01）", kind="cfg",
    )


def _second_write_fails(monkeypatch):
    real_write = promote_io.write
    seen = []

    def _write(target, content, permissions, roots):
        seen.append(target)
        if len(seen) == _SECOND:
            raise TargetNotWritable(f"注入：{target} 寫不進去。下一步：無")
        real_write(target, content, permissions, roots)

    monkeypatch.setattr(promote_io, "write", _write)


def test_a_rollback_restores_the_mode_the_target_had_before(tmp_path, monkeypatch):
    # F29：進版前 0640 → 第 2 份失敗回滾 → 第 1 份不但內容回來，mode 也是 0640
    #（不是暫存檔的 0600）。
    repo = _repo(tmp_path)
    first = _plan(tmp_path, repo, "a", "max_vel: 1.2\n", "max_vel: 0.8\n")
    second = _plan(tmp_path, repo, "b", "speed: 2\n", "speed: 1\n")
    os.chmod(first.target, _MODE_BEFORE)
    before_head = head(str(repo))
    _second_write_fails(monkeypatch)

    with pytest.raises(TargetNotWritable):
        apply(str(repo), [first, second], _AUTHOR, [str(tmp_path / "targets")])

    info = os.stat(first.target)
    assert pathlib.Path(first.target).read_bytes() == b"max_vel: 0.8\n"
    assert stat.S_IMODE(info.st_mode) == _MODE_BEFORE
    assert (info.st_uid, info.st_gid) == (os.getuid(), os.getgid())
    assert head(str(repo)) == before_head


def test_a_rollback_that_cannot_finish_names_the_leftover_and_keeps_the_cause(
    tmp_path, monkeypatch
):
    # U24 第 2 輪：還原第 1 份時原子替換失敗 → PromoteLeftBehind 指名那個目標與原因、帶原本的失敗，
    # `__cause__` 是原本的失敗；第 1 份此刻是新內容（真的殘留）、第 2 份沒動、HEAD 回到進版前。
    repo = _repo(tmp_path)
    first = _plan(tmp_path, repo, "a", "max_vel: 1.2\n", "max_vel: 0.8\n")
    second = _plan(tmp_path, repo, "b", "speed: 2\n", "speed: 1\n")
    before_head = head(str(repo))
    _second_write_fails(monkeypatch)
    real_replace = promote_io.replace_atomically

    def _restoring_first_fails(target, content, **kwargs):
        if target == first.target:
            raise TargetNotWritable(f"注入：{target} 還原時寫不進去。下一步：無")
        real_replace(target, content, **kwargs)

    monkeypatch.setattr(promote_io, "replace_atomically", _restoring_first_fails)

    with pytest.raises(PromoteLeftBehind) as caught:
        apply(str(repo), [first, second], _AUTHOR, [str(tmp_path / "targets")])

    message = str(caught.value)
    assert first.target in message and "還原時寫不進去" in message
    assert "原本的失敗" in message and "下一步" in message
    assert isinstance(caught.value.__cause__, TargetNotWritable)
    assert pathlib.Path(first.target).read_bytes() == b"max_vel: 1.2\n"
    assert pathlib.Path(second.target).read_bytes() == b"speed: 1\n"
    assert head(str(repo)) == before_head


# ── F14：進版前拍現場的那一次讀取也要守門（具名管道不卡住、不是一般檔案就拒絕）────────────────


def _run_with_fifo_release(action, fifo, seconds=10):
    """在執行緒跑 `action`；超過 `seconds` 還沒回來就開 FIFO 的寫端放掉它（修正前的卡住），
    記下 hung。回 (hung, raised)。"""
    outcome = {"raised": None}

    def runner():
        try:
            action()
        except (NotARegularFile, OSError) as error:
            outcome["raised"] = error

    worker = threading.Thread(target=runner, daemon=True)
    worker.start()
    worker.join(seconds)
    hung = worker.is_alive()
    if hung:
        with contextlib.suppress(OSError):
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(5)
    return hung, outcome["raised"]


def test_a_fifo_target_makes_promote_refuse_by_name_instead_of_hanging(tmp_path):
    # F14-5：目標被換成具名管道 → 進版前拍現場不可以卡住；具名拒絕、沒有寫出、目標仍是那條管道。
    repo = _repo(tmp_path)
    plan = _plan(tmp_path, repo, "p", "max_vel: 1.2\n", "max_vel: 0.8\n")
    target = pathlib.Path(plan.target)
    target.unlink()
    os.mkfifo(target)

    hung, raised = _run_with_fifo_release(
        lambda: apply(str(repo), [plan], _AUTHOR, [str(tmp_path / "targets")]), str(target)
    )

    assert (hung, isinstance(raised, NotARegularFile), stat.S_ISFIFO(os.lstat(target).st_mode)) == (
        False, True, True
    )


def test_a_fifo_target_on_the_second_plan_leaves_no_half_batch(tmp_path):
    # 定案 1：多份進版，第 2 份目標是具名管道 → 整批停下，第 1 份還原、HEAD 不變、管道還是管道。
    repo = _repo(tmp_path)
    first = _plan(tmp_path, repo, "a", "max_vel: 1.2\n", "max_vel: 0.8\n")
    second = _plan(tmp_path, repo, "b", "speed: 2\n", "speed: 1\n")
    fifo = pathlib.Path(second.target)
    fifo.unlink()
    os.mkfifo(fifo)
    before_head = head(str(repo))

    with pytest.raises(NotARegularFile):
        apply(str(repo), [first, second], _AUTHOR, [str(tmp_path / "targets")])

    assert (
        pathlib.Path(first.target).read_bytes(),
        head(str(repo)),
        stat.S_ISFIFO(os.lstat(fifo).st_mode),
    ) == (b"max_vel: 0.8\n", before_head, True)


def test_a_symlinked_target_is_refused_at_the_snapshot_before_anything_is_written(tmp_path):
    # 定案 2（B1，對 F20 進版行為的政策調整）：目標是連結 → 進版在拍現場那一步拒絕；
    # 連結與真檔都不變。
    repo = _repo(tmp_path)
    plan = _plan(tmp_path, repo, "l", "max_vel: 1.2\n", "max_vel: 0.8\n")
    link = pathlib.Path(plan.target)
    real = link.with_name("real.yaml")
    real.write_text("max_vel: 0.8\n", encoding="utf-8")
    link.unlink()
    link.symlink_to(real)
    before_head = head(str(repo))

    with pytest.raises(NotARegularFile):
        apply(str(repo), [plan], _AUTHOR, [str(tmp_path / "targets")])

    assert (link.is_symlink(), real.read_text(), head(str(repo))) == (
        True, "max_vel: 0.8\n", before_head
    )


# ── F37（11/9 U24 第 2 輪）：回滾未竟時設定庫那一行依實際回復結果四擇一，恢復步驟跟著分支 ──
# 期望字句來自 11/9 定稿的恢復指引（cm-verify fix-plan-F36-F38-20261109 第三、十節）。
# 只有「已完整退回」才提供以來源覆蓋／納入來源；其他三種先由維護者確認設定庫、
# 先不要進版覆蓋或納入。

_RESTORED = "設定庫：已退回進版前"
_HEAD_ONLY = "設定庫：HEAD 已回到操作前的 commit；工作樹、索引與附帶檔案是否完整還原，仍需核對。"
_NOT_RESTORED = "設定庫：沒能完整退回進版前"
_UNKNOWN = "設定庫：無法確認是否已退回進版前"
_HOLD_OFF = (
    "先由維護者確認設定庫內容與歷史，確認可採用的來源版本後，再決定恢復方式；"
    "目前先不要進版、覆蓋或納入。"
)
_OVERWRITE = "按「以來源覆蓋目標」"


def _restore_chown_refused(monkeypatch):
    """第 1 份寫出時設擁有者照常；回滾還原第 1 份時設擁有者被拒（錯誤帶暫存檔名）——
    第 1 份留在新內容。"""
    real_chown = os.chown
    calls = []

    def _chown(path, uid, gid, **kwargs):
        calls.append(path)
        if len(calls) >= _SECOND:
            raise PermissionError(1, "Operation not permitted", path)
        real_chown(path, uid, gid, **kwargs)

    monkeypatch.setattr(os, "chown", _chown)


def _status_check_fails(monkeypatch):
    """`git status` 跑不起來（其他 git 指令照常）：工作樹與索引沒辦法核對。"""
    real_run = subprocess.run

    def _run(args, *rest, **kwargs):
        if "status" in args:
            raise subprocess.CalledProcessError(128, args, stderr="注入：status 跑不起來")
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run)


def _left_behind(tmp_path, monkeypatch, companions=None):
    """兩份、第 2 份寫出失敗、第 1 份還原時設擁有者被拒（有 companions 時不注入這一項）。

    回 (訊息, repo, 進版前 HEAD)。"""
    repo = _repo(tmp_path)
    first = _plan(tmp_path, repo, "a", "max_vel: 1.2\n", "max_vel: 0.8\n")
    second = _plan(tmp_path, repo, "b", "speed: 2\n", "speed: 1\n")
    before_head = head(str(repo))
    _second_write_fails(monkeypatch)
    if companions is None:
        _restore_chown_refused(monkeypatch)
    with pytest.raises(PromoteLeftBehind) as caught:
        apply(str(repo), [first, second], _AUTHOR, [str(tmp_path / "targets")],
              companions={first.uid: companions} if companions else None)
    return str(caught.value), repo, before_head


def test_when_only_the_head_can_be_checked_the_message_says_so_and_holds_off(tmp_path, monkeypatch):
    _status_check_fails(monkeypatch)
    message, _, _ = _left_behind(tmp_path, monkeypatch)

    assert (_HEAD_ONLY in message, _RESTORED in message, _HOLD_OFF in message,
            _OVERWRITE in message) == (True, False, True, False)


def test_when_the_reset_fails_the_message_says_the_repo_is_not_restored_and_holds_off(
    tmp_path, monkeypatch
):
    def _reset_refused(repo, sha):
        raise subprocess.CalledProcessError(128, ["git", "reset", "--hard", sha], stderr="注入")

    monkeypatch.setattr(promote_io, "reset_hard", _reset_refused)
    message, _, _ = _left_behind(tmp_path, monkeypatch)

    assert (_NOT_RESTORED in message, _RESTORED in message, _HOLD_OFF in message,
            _OVERWRITE in message) == (True, False, True, False)


def test_a_companion_that_was_not_restored_is_not_fully_restored_even_when_the_head_matches(
    tmp_path, monkeypatch
):
    # HEAD 相符不等於設定庫完整還原：一起寫回設定庫的附帶檔案（這次才新建的 schema）拿不掉時，
    # 就算 reset 成功、HEAD 回到進版前，也只能說沒能完整退回，並註明那是設定庫裡的檔案。
    companion = "schemas/mfz3k9qa.schema.json"
    real_remove = os.remove

    def _remove(path, *args, **kwargs):
        if path.endswith(companion):
            raise PermissionError(13, "Permission denied", path)
        return real_remove(path, *args, **kwargs)

    monkeypatch.setattr(os, "remove", _remove)
    message, repo, before_head = _left_behind(
        tmp_path, monkeypatch, companions=((companion, '{"type": "object"}\n'),)
    )

    assert (head(str(repo)) == before_head, _NOT_RESTORED in message, _RESTORED in message,
            "（設定庫裡的檔案）" in message, _HOLD_OFF in message, _OVERWRITE in message) == (
        True, True, False, True, True, False,
    )


def test_when_the_head_cannot_be_read_after_the_reset_the_message_cannot_confirm(
    tmp_path, monkeypatch
):
    real_head = promote_io.head
    reads = []

    def _head_after_reset_unreadable(repo):
        reads.append(repo)
        if len(reads) >= _SECOND:
            raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"], stderr="注入")
        return real_head(repo)

    monkeypatch.setattr(promote_io, "head", _head_after_reset_unreadable)
    message, _, _ = _left_behind(tmp_path, monkeypatch)

    assert (_UNKNOWN in message, _RESTORED in message, _HOLD_OFF in message,
            _OVERWRITE in message) == (True, False, True, False)


def test_targets_all_restored_but_an_unverifiable_repo_is_still_reported_as_left_behind(
    tmp_path, monkeypatch
):
    # 目標都還原了、設定庫卻核對不了：不能讓它看起來像整批乾淨回滾——照回滾未竟回報，
    # 設定庫一行寫只核對到 HEAD。
    repo = _repo(tmp_path)
    first = _plan(tmp_path, repo, "a", "max_vel: 1.2\n", "max_vel: 0.8\n")
    second = _plan(tmp_path, repo, "b", "speed: 2\n", "speed: 1\n")
    _second_write_fails(monkeypatch)
    _status_check_fails(monkeypatch)

    with pytest.raises(PromoteLeftBehind) as caught:
        apply(str(repo), [first, second], _AUTHOR, [str(tmp_path / "targets")])

    message = str(caught.value)
    assert (pathlib.Path(first.target).read_bytes(), _HEAD_ONLY in message) == (
        b"max_vel: 0.8\n", True,
    )
