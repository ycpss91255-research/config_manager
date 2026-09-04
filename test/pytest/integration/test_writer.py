"""T8 — 原子寫出。測試介面：io/writer 的 write。

以真實檔案系統測試（IO 層 adapter）。用語依 CONTEXT.md。

寫出順序為：暫存檔 → fsync → owner/group/mode → rename。rename 在同一個
filesystem 內是原子操作，所以目標要嘛是舊內容、要嘛是新內容，不會是半殘檔案。

owner/group 用「目前這個行程自己的 id」：本服務跑在容器裡，而容器的使用者常常
沒有 /etc/passwd 項目（實測 uid 501 就查不到名字），把自己 chown 給自己是非 root
唯一能成功的所有權設定。設定成別人的所有權需要提權，那條路屬於 #19。
"""

import os
import stat

import pytest

from config_manager.core.models import Permissions
from config_manager.io.errors import (
    OwnershipRefused,
    TargetNotWritable,
    TargetOutsideRoots,
)
from config_manager.io.writer import write


# 測試用的非預設 mode，字串與位元兩種形式各一份，避免比較裡出現魔術數字。
_MODE = "0600"
_MODE_BITS = 0o600


def _own_permissions(mode: str = "0644") -> Permissions:
    return Permissions(owner=str(os.getuid()), group=str(os.getgid()), mode=mode)


def test_write_puts_the_given_content_at_the_target(tmp_path):
    target = tmp_path / "params.yaml"

    write(str(target), "max_vel: 0.8\n", _own_permissions(), [str(tmp_path)])

    assert target.read_text() == "max_vel: 0.8\n"


def test_write_applies_the_requested_mode(tmp_path):
    target = tmp_path / "params.yaml"

    write(str(target), "x\n", _own_permissions(_MODE), [str(tmp_path)])

    assert stat.S_IMODE(target.stat().st_mode) == _MODE_BITS


def test_write_overwrites_an_existing_target(tmp_path):
    target = tmp_path / "params.yaml"
    target.write_text("old\n")

    write(str(target), "new\n", _own_permissions(), [str(tmp_path)])

    assert target.read_text() == "new\n"


def test_an_interrupted_write_leaves_the_target_and_no_debris(tmp_path, monkeypatch):
    # 「中斷」以在改名前注入失敗來模擬：目標必須維持原內容，且目錄裡不留暫存檔。
    target = tmp_path / "params.yaml"
    target.write_text("old\n")

    def refuse(*_args, **_kwargs):
        raise OSError("injected failure before rename")

    monkeypatch.setattr(os, "replace", refuse)

    with pytest.raises(OSError):
        write(str(target), "new\n", _own_permissions(), [str(tmp_path)])

    assert target.read_text() == "old\n"
    assert [path.name for path in tmp_path.iterdir()] == ["params.yaml"]


def test_an_unwritable_target_directory_fails_by_name(tmp_path):
    # 訊息要能讓人知道下一步，不能只說「操作失敗」。
    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked / "params.yaml"
    locked.chmod(0o500)
    try:
        with pytest.raises(TargetNotWritable) as exc:
            write(str(target), "x\n", _own_permissions(), [str(tmp_path)])
        message = str(exc.value)
        assert str(locked) in message
        assert "下一步" in message
    finally:
        locked.chmod(0o700)


def test_a_target_that_escapes_the_allowed_roots_is_refused(tmp_path):
    # 目標是一條指向白名單外的符號連結。ADR-00000003 警告過：rename 會把連結
    # 換成一般檔案而靜默失效，所以檢查必須在任何寫入之前發生。
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.yaml"
    secret.write_text("secret\n")

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "params.yaml"
    link.symlink_to(secret)

    with pytest.raises(TargetOutsideRoots):
        write(str(link), "new\n", _own_permissions(), [str(allowed)])

    assert secret.read_text() == "secret\n"
    assert link.is_symlink()


def test_write_applies_the_requested_ownership(tmp_path):
    target = tmp_path / "params.yaml"

    write(str(target), "x\n", _own_permissions(), [str(tmp_path)])

    info = target.stat()
    assert (info.st_uid, info.st_gid) == (os.getuid(), os.getgid())


def test_ownership_it_cannot_set_fails_loudly_without_writing(tmp_path):
    # 服務不以 root 執行（驗收第 8 條），所以要求 root 所有必然失敗。重點是它得
    # 大聲失敗，而不是靜默跳過 chown 就當成寫成功——那正是不變式 2 要擋的形態。
    target = tmp_path / "params.yaml"
    root_owned = Permissions(owner="0", group="0", mode="0644")

    with pytest.raises(OwnershipRefused):
        write(str(target), "x\n", root_owned, [str(tmp_path)])

    assert not target.exists()
