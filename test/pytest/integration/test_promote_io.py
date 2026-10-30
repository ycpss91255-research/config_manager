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
