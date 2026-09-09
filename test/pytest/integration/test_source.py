"""T22 — 匯入時刻對外界的讀取。io/source 的整合規格。

本檔涵蓋 T22 的**路徑判定**那一半（#174）：來源經 realpath 解析後落在白名單外、
或不是可讀的一般檔案時，拒絕且不讀取內容。讀取失敗的錯誤分類（不存在／讀不到／
上層目錄無 traverse 權限）是 #175 的範圍。

整合層：這裡真的碰檔案系統，符號連結也用真的建（比照 `test_writer.py` 的手法）。
"""

import errno
import grp
import os
import pwd
import socket
import stat

import pytest

from config_manager.io.errors import (
    ContentUnreadable,
    SourceAbsent,
    SourceNotRegularFile,
    SourceOutsideRoots,
    SourcePathUnstable,
    SourceUnreachable,
)
from config_manager.io.source import local_hostname, read_source


def _inside_and_outside(tmp_path):
    """建出「白名單內」與「白名單外」兩個目錄。"""
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    return inside, outside


def test_regular_file_inside_the_whitelist_is_read_as_bytes(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"name: amr01\n")

    assert read_source(str(source), [str(inside)]).content == b"name: amr01\n"


def test_the_resolved_path_is_reported(tmp_path):
    # 呼叫端要知道它實際讀了哪一個檔案，不是它請求了哪一個。
    inside, _ = _inside_and_outside(tmp_path)
    real = inside / "params.yaml"
    real.write_bytes(b"a: 1\n")
    link = inside / "link.yaml"
    link.symlink_to(real)

    assert read_source(str(link), [str(inside)]).resolved_path == os.path.realpath(real)


def test_permissions_are_read_from_the_source(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")
    os.chmod(source, 0o640)

    assert read_source(str(source), [str(inside)]).permissions.mode == "0640"


def test_symlink_pointing_outside_the_whitelist_is_refused(tmp_path):
    # 連結本身在白名單內，指向的東西在外面——只有 realpath 解析後看得出來。
    inside, outside = _inside_and_outside(tmp_path)
    secret = outside / "secret.yaml"
    secret.write_bytes(b"stolen: 1\n")
    link = inside / "link.yaml"
    link.symlink_to(secret)

    with pytest.raises(SourceOutsideRoots):
        read_source(str(link), [str(inside)])


def test_refusal_names_where_the_link_actually_points(tmp_path):
    # 只說「被拒絕」不夠——要說出它實際指向哪裡，否則使用者無從判斷自己錯在哪。
    inside, outside = _inside_and_outside(tmp_path)
    secret = outside / "secret.yaml"
    secret.write_bytes(b"stolen: 1\n")
    link = inside / "link.yaml"
    link.symlink_to(secret)

    with pytest.raises(SourceOutsideRoots) as caught:
        read_source(str(link), [str(inside)])
    assert os.path.realpath(secret) in str(caught.value)


def test_content_is_not_read_when_the_source_is_refused(tmp_path):
    # 「不讀取內容」要真的驗：連結指向白名單外一個**不存在**的路徑。
    # 先判定就丟 SourceOutsideRoots；先讀取則會丟讀取類的例外。
    inside, outside = _inside_and_outside(tmp_path)
    link = inside / "link.yaml"
    link.symlink_to(outside / "does-not-exist.yaml")

    with pytest.raises(SourceOutsideRoots):
        read_source(str(link), [str(inside)])


def test_plain_path_outside_the_whitelist_is_refused(tmp_path):
    inside, outside = _inside_and_outside(tmp_path)
    source = outside / "params.yaml"
    source.write_bytes(b"a: 1\n")

    with pytest.raises(SourceOutsideRoots):
        read_source(str(source), [str(inside)])


def test_directory_is_refused(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    folder = inside / "a-directory"
    folder.mkdir()

    with pytest.raises(SourceNotRegularFile):
        read_source(str(folder), [str(inside)])


def test_broken_symlink_inside_the_whitelist_is_refused(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    link = inside / "link.yaml"
    link.symlink_to(inside / "gone.yaml")

    with pytest.raises(SourceNotRegularFile):
        read_source(str(link), [str(inside)])


def test_fifo_is_refused(tmp_path):
    # 裝置與 socket 同一類：讀它會阻塞或回無意義的內容，不是可納管的 config。
    inside, _ = _inside_and_outside(tmp_path)
    fifo = inside / "a-fifo"
    os.mkfifo(fifo)

    with pytest.raises(SourceNotRegularFile):
        read_source(str(fifo), [str(inside)])


def test_refusal_of_a_non_regular_file_names_what_it_is(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    folder = inside / "a-directory"
    folder.mkdir()

    with pytest.raises(SourceNotRegularFile) as caught:
        read_source(str(folder), [str(inside)])
    assert "目錄" in str(caught.value)


def test_local_hostname_is_not_empty():
    # T22 只保證「讀到的是本機 hostname」；它在不同部署形態下穩不穩定屬部署決策（#178）。
    assert local_hostname()


def test_mode_is_reported_as_four_digit_octal(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")
    os.chmod(source, 0o600)

    expected = f"{stat.S_IMODE(os.stat(source).st_mode):04o}"
    assert read_source(str(source), [str(inside)]).permissions.mode == expected


# ── 對抗性驗證補上的規格（見 PR 描述的突變檢查一節）─────────────────────────


def test_socket_is_refused(tmp_path):
    # 突變檢查抓到的缺口：原本只有目錄與具名管道有規格，socket 與裝置沒有。
    inside, _ = _inside_and_outside(tmp_path)
    endpoint = inside / "a-socket"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(endpoint))
    try:
        with pytest.raises(SourceNotRegularFile):
            read_source(str(endpoint), [str(inside)])
    finally:
        server.close()


def test_character_device_is_refused():
    # /dev/null 是字元裝置。把 /dev 放進白名單，才驗得到這條分支。
    with pytest.raises(SourceNotRegularFile):
        read_source("/dev/null", ["/dev"])


def test_owner_identifies_the_uid_that_owns_the_file(tmp_path):
    # 突變檢查抓到的缺口：把 owner／group 寫死成 "root"，原本 13 則全過。
    # 名字查得到就回名字、查不到就回數字——兩種形式都必須指回同一個 uid。
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")

    owner = read_source(str(source), [str(inside)]).permissions.owner
    uid = int(owner) if owner.isdigit() else pwd.getpwnam(owner).pw_uid
    assert uid == os.stat(source).st_uid


def test_group_identifies_the_gid_that_owns_the_file(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")

    group = read_source(str(source), [str(inside)]).permissions.group
    gid = int(group) if group.isdigit() else grp.getgrnam(group).gr_gid
    assert gid == os.stat(source).st_gid


def test_permissions_do_not_come_from_a_second_lookup_by_path(tmp_path, monkeypatch):
    # 「只讀一次」的可觀察形式：權限必須來自已開啟的那個 fd，不是對路徑再查一次
    # ——第二次查詢可能查到另一個 inode，那樣回報的權限描述的就不是被讀到的內容。
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")
    resolved = os.path.realpath(source)

    looked_up = []
    real_stat = os.stat

    def spy(target, *args, **kwargs):
        looked_up.append(str(target))
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "stat", spy)
    read_source(str(source), [str(inside)])

    assert resolved not in looked_up


def test_a_symlinked_whitelist_root_still_covers_what_is_inside_it(tmp_path):
    # 白名單的「根」自己也要解析。io/writer 的逃逸檢查對它的 roots 就是這樣做的
    # ——同一個問題的兩道檢查不該給出不同答案。
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root)
    source = real_root / "params.yaml"
    source.write_bytes(b"a: 1\n")

    assert read_source(str(source), [str(linked_root)]).content == b"a: 1\n"


def test_path_changing_during_resolution_is_a_named_exception(tmp_path, monkeypatch):
    # 非 strict 的 realpath 只保證不因「路徑不存在」而丟，不保證完全不丟：CPython
    # 3.11 的 _joinrealpath 在 lstat 說「這是連結」之後才裸呼叫 readlink，兩者之間
    # 連結被移除就會拋出。呼叫端不該收到未分類的原生例外。
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")

    def unstable(_target, *_args, **_kwargs):
        raise OSError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(os.path, "realpath", unstable)
    with pytest.raises(SourcePathUnstable):
        read_source(str(source), [str(inside)])


def test_final_component_becoming_a_symlink_at_open_time_is_refused(tmp_path, monkeypatch):
    # O_NOFOLLOW 收到 ELOOP：解析之後最後一段又變成了符號連結，那就是競速本身。
    # 失敗方向是拒絕——寧可這次匯入不成立，也不讀一份來歷不明的內容。
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")
    resolved = os.path.realpath(source)

    real_open = os.open

    def racing_open(target, *args, **kwargs):
        if str(target) == resolved:
            raise OSError(errno.ELOOP, "Too many levels of symbolic links")
        return real_open(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)
    with pytest.raises(SourcePathUnstable):
        read_source(str(source), [str(inside)])


# ── 讀取失敗的三種結果各自分開（#182）───────────────────────────────────────
#
# 先前 ENOENT（不存在）被歸進 SourceNotRegularFile——那句話講不出根據，我們根本
# 沒看到那個 inode。io/digest 的 docstring 早寫過這個分界為什麼重要：把「你沒權限
# 讀它」說成「那不是一個檔案」，使用者會去改錯的東西（不變式 2）。


def test_missing_source_is_reported_as_missing_not_as_a_non_file(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)

    with pytest.raises(SourceAbsent):
        read_source(str(inside / "never-existed.yaml"), [str(inside)])


def test_missing_source_message_says_it_does_not_exist(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)

    with pytest.raises(SourceAbsent) as caught:
        read_source(str(inside / "never-existed.yaml"), [str(inside)])
    assert "不存在" in str(caught.value)


def test_unreadable_content_is_reported_as_a_permission_problem(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    source = inside / "params.yaml"
    source.write_bytes(b"a: 1\n")
    os.chmod(source, 0o000)

    with pytest.raises(ContentUnreadable):
        read_source(str(source), [str(inside)])


def test_parent_without_traverse_permission_is_not_reported_as_missing(tmp_path):
    # 父目錄少了 +x：檔案明明在，lexists 卻回 False。要看 lstat 的 errno 才分得出
    # 「不存在」與「上層目錄擋住去路」——後者是 EACCES，不是 ENOENT。
    inside, _ = _inside_and_outside(tmp_path)
    locked = inside / "locked"
    locked.mkdir()
    source = locked / "params.yaml"
    source.write_bytes(b"a: 1\n")
    os.chmod(locked, 0o000)

    try:
        with pytest.raises(SourceUnreachable):
            read_source(str(source), [str(inside)])
    finally:
        os.chmod(locked, 0o755)  # 讓 tmp_path 清理得掉


def test_traverse_failure_names_the_directory_in_the_way(tmp_path):
    inside, _ = _inside_and_outside(tmp_path)
    locked = inside / "locked"
    locked.mkdir()
    source = locked / "params.yaml"
    source.write_bytes(b"a: 1\n")
    os.chmod(locked, 0o000)

    try:
        with pytest.raises(SourceUnreachable) as caught:
            read_source(str(source), [str(inside)])
        assert str(locked) in str(caught.value)
    finally:
        os.chmod(locked, 0o755)


def test_broken_symlink_is_still_a_non_regular_file(tmp_path):
    # T22 的行為表明列斷掉的連結屬「不是一般檔案」——本 issue 不改變這一分類。
    inside, _ = _inside_and_outside(tmp_path)
    link = inside / "link.yaml"
    link.symlink_to(inside / "gone.yaml")

    with pytest.raises(SourceNotRegularFile):
        read_source(str(link), [str(inside)])
