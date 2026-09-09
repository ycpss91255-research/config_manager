"""T22 — 匯入時刻對外界的讀取。io/source 的整合規格。

本檔涵蓋 T22 的**路徑判定**那一半（#174）：來源經 realpath 解析後落在白名單外、
或不是可讀的一般檔案時，拒絕且不讀取內容。讀取失敗的錯誤分類（不存在／讀不到／
上層目錄無 traverse 權限）是 #175 的範圍。

整合層：這裡真的碰檔案系統，符號連結也用真的建（比照 `test_writer.py` 的手法）。
"""

import os
import stat

import pytest

from config_manager.io.errors import SourceNotRegularFile, SourceOutsideRoots
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
