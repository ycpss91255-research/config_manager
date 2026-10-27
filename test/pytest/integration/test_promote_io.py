"""io/promote 的回滾（T9／T8；F29）：還原不只是位元組，owner／group／mode 也要回來；回滾未竟的例外
要指名殘留並把原本的失敗掛在 `__cause__`。系統層（test_api）驗經 HTTP 的樣子，這裡直接呼叫 `apply`。

以服務身分（非 root）跑：owner／group 只能驗「沒被改成別的」，mode 用 0640 和暫存檔的 0600 區分。
"""

from __future__ import annotations

import os
import stat
import subprocess

import pytest

from config_manager.core.drafts import Promotion
from config_manager.core.models import Permissions
from config_manager.io import promote as promote_io
from config_manager.io.errors import PromoteLeftBehind, TargetNotWritable
from config_manager.io.git import head
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.promote import apply

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
    """一份要進版的 config：來源複本在 repo/files、目標在 tmp_path/targets，目標先放進版前的內容。"""
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
        if len(seen) == 2:
            raise TargetNotWritable(f"注入：{target} 寫不進去。下一步：無")
        real_write(target, content, permissions, roots)

    monkeypatch.setattr(promote_io, "write", _write)


def test_a_rollback_restores_the_mode_the_target_had_before(tmp_path, monkeypatch):
    # F29：進版前 0640 → 第 2 份失敗回滾 → 第 1 份不但內容回來，mode 也是 0640（不是暫存檔的 0600）。
    repo = _repo(tmp_path)
    first = _plan(tmp_path, repo, "a", "max_vel: 1.2\n", "max_vel: 0.8\n")
    second = _plan(tmp_path, repo, "b", "speed: 2\n", "speed: 1\n")
    os.chmod(first.target, 0o640)
    before_head = head(str(repo))
    _second_write_fails(monkeypatch)

    with pytest.raises(TargetNotWritable):
        apply(str(repo), [first, second], _AUTHOR, [str(tmp_path / "targets")])

    info = os.stat(first.target)
    assert open(first.target, "rb").read() == b"max_vel: 0.8\n"
    assert stat.S_IMODE(info.st_mode) == 0o640
    assert (info.st_uid, info.st_gid) == (os.getuid(), os.getgid())
    assert head(str(repo)) == before_head


def test_a_rollback_that_cannot_finish_names_the_leftover_and_keeps_the_cause(tmp_path, monkeypatch):
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
    assert open(first.target, "rb").read() == b"max_vel: 1.2\n"
    assert open(second.target, "rb").read() == b"speed: 1\n"
    assert head(str(repo)) == before_head
