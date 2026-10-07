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
    TargetExists,
    OwnershipRefused,
    TargetNotWritable,
    TargetOutsideRoots,
    TemporaryLeftBehind,
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


def _write_that_fails_before_rename(monkeypatch):
    """把 rename 打掉，讓 write 走進「暫存檔要被清掉」的那條路。

    既有的中斷規格用同一種注入，但它驗的是清理成功的樣子（目錄裡只剩目標檔）。
    下面兩則驗的是清理**失敗**時會發生什麼——那條路徑先前完全沒有規格。
    """

    def refuse(*_args, **_kwargs):
        raise OSError("injected failure before rename")

    monkeypatch.setattr(os, "replace", refuse)


def _unlink_that_fails(monkeypatch):
    """sticky bit、SELinux、NFS 的 stale handle 都會讓「建得出、刪不掉」成立。"""

    def refuse(*_args, **_kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "unlink", refuse)


def test_a_temporary_that_cannot_be_removed_keeps_the_original_failure_visible(
    tmp_path, monkeypatch
):
    # 清理失敗不得蓋掉原本的失敗原因。使用者拿到「暫存檔刪不掉」而真正的原因
    # 不見了的話，他會去修一個次要問題。
    target = tmp_path / "params.yaml"
    target.write_text("old\n")
    _write_that_fails_before_rename(monkeypatch)
    _unlink_that_fails(monkeypatch)

    with pytest.raises(TemporaryLeftBehind) as exc:
        write(str(target), "new\n", _own_permissions(), [str(tmp_path)])

    assert "injected failure before rename" in str(exc.value)


def test_a_temporary_that_cannot_be_removed_names_the_file_and_the_next_step(
    tmp_path, monkeypatch
):
    # 清理失敗也不得消失（§0.4：不得捕捉後僅 pass）。目標目錄會逐次累積
    # .config_manager-*.tmp，而這個系統的賣點正是「目標位置的內容由我們負責」。
    target = tmp_path / "params.yaml"
    target.write_text("old\n")
    _write_that_fails_before_rename(monkeypatch)
    _unlink_that_fails(monkeypatch)

    with pytest.raises(TemporaryLeftBehind) as exc:
        write(str(target), "new\n", _own_permissions(), [str(tmp_path)])

    message = str(exc.value)
    assert ".config_manager-" in message and "下一步" in message


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


# ── 只補回不存在的檔案（寫出修復；#29 的修正）─────────────────────────────────
# 寫出修復是「目標不見了，把來源放回去」——不留變更紀錄。所以它絕不能覆蓋任何既有的東西：
# 覆蓋既有內容要走以來源覆蓋目標（先確認、留紀錄）。`only_if_missing` 讓這件事由檔案系統保證。


def test_filling_in_a_missing_target_writes_the_content_and_mode(tmp_path):
    target = tmp_path / "params.yaml"

    write(str(target), "max_vel: 0.8\n", _own_permissions(_MODE), [str(tmp_path)],
          only_if_missing=True)

    assert target.read_text() == "max_vel: 0.8\n"
    assert stat.S_IMODE(target.stat().st_mode) == _MODE_BITS
    assert os.listdir(tmp_path) == ["params.yaml"]  # 沒有留下暫存檔


def test_filling_in_refuses_an_existing_target_and_leaves_it_untouched(tmp_path):
    target = tmp_path / "params.yaml"
    target.write_text("changed on site\n")
    before = target.stat()

    with pytest.raises(TargetExists) as caught:
        write(str(target), "from the repo\n", _own_permissions(), [str(tmp_path)],
              only_if_missing=True)

    assert target.read_text() == "changed on site\n"
    after = target.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)  # 連碰都沒碰
    assert os.listdir(tmp_path) == ["params.yaml"]
    assert str(target) in str(caught.value) and "下一步：" in str(caught.value)


def test_filling_in_refuses_a_target_identical_to_what_it_would_write(tmp_path):
    # 內容一樣也不寫：「目標在不在」是唯一的條件，不去比內容——比了再寫，中間就有空窗。
    target = tmp_path / "params.yaml"
    target.write_text("same\n")
    before = target.stat().st_ino

    with pytest.raises(TargetExists):
        write(str(target), "same\n", _own_permissions(), [str(tmp_path)], only_if_missing=True)

    assert target.stat().st_ino == before


def test_filling_in_refuses_a_dangling_symlink_at_the_target(tmp_path):
    # 懸空的符號連結：`exists()` 說不在，但那個名字有東西。照寫會把連結換成一般檔案
    # （ADR-00000003 的靜默失效）。
    target = tmp_path / "params.yaml"
    target.symlink_to(tmp_path / "elsewhere.yaml")

    with pytest.raises(TargetExists):
        write(str(target), "x\n", _own_permissions(), [str(tmp_path)], only_if_missing=True)

    assert target.is_symlink() and os.readlink(target) == str(tmp_path / "elsewhere.yaml")
    assert not (tmp_path / "elsewhere.yaml").exists()


def test_filling_in_refuses_a_directory_at_the_target(tmp_path):
    target = tmp_path / "params.yaml"
    target.mkdir()

    with pytest.raises(TargetExists):
        write(str(target), "x\n", _own_permissions(), [str(tmp_path)], only_if_missing=True)

    assert target.is_dir()


def test_a_target_that_appears_at_the_last_moment_is_not_overwritten(tmp_path, monkeypatch):
    # 「先看有沒有、再寫」擋不住這個：檢查之後、寫上去之前，有人把檔案放了回去。這裡在內容都
    # 準備好、就差放上去的那一刻（套權限時）讓目標出現——仍然不能被蓋掉。
    target = tmp_path / "params.yaml"
    real_chmod = os.chmod

    def appear_then_chmod(path, mode, **kwargs):
        target.write_text("put back by someone else\n")
        real_chmod(path, mode, **kwargs)

    monkeypatch.setattr(os, "chmod", appear_then_chmod)

    with pytest.raises(TargetExists):
        write(str(target), "from the repo\n", _own_permissions(), [str(tmp_path)],
              only_if_missing=True)

    assert target.read_text() == "put back by someone else\n"
    assert os.listdir(tmp_path) == ["params.yaml"]


def test_the_ordinary_write_still_overwrites(tmp_path):
    # 進版、以來源覆蓋、退版都靠覆蓋：`only_if_missing` 是另外開的門，預設不變。
    target = tmp_path / "params.yaml"
    target.write_text("old\n")

    write(str(target), "new\n", _own_permissions(), [str(tmp_path)])

    assert target.read_text() == "new\n"

