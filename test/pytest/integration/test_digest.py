"""T20 — 內容雜湊。測試介面：io/digest 的 digest。

偏離偵測的另一半。T2 的 decide 刻意不讀檔，雜湊由呼叫者算好傳入——這裡就是
算那個雜湊的地方，所以它在 io 層（ADR-00000011）。

比對的是原始位元組，不是解析後的資料：偵測的目標是「有人繞過介面動過這個檔案」
（設計文件 §5.4），連空白與換行的差異都算偏離。
"""

import os

import pytest

from config_manager.io import digest as digest_module
from config_manager.io.digest import digest
from config_manager.io.errors import ContentTooLarge, NotARegularFile, PathUnreachable

# 以 coreutils 的 sha256sum 取得，不是用 hashlib 再算一次——
# 用實作自己的算法產生期望值的測試恆真（TEST-PLAN 撰寫規則）：
#   $ printf 'hello\n' | sha256sum
_HELLO_SHA256 = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"


def test_digest_of_existing_file_is_the_sha256_of_its_bytes(tmp_path):
    path = tmp_path / "hello.txt"
    path.write_text("hello\n", encoding="utf-8")

    assert digest(str(path)) == _HELLO_SHA256


def test_missing_file_returns_none_rather_than_raising(tmp_path):
    # 目標不存在是「未部署」——四種狀態之一，是合法狀態不是錯誤。
    assert digest(str(tmp_path / "nowhere.txt")) is None


def test_identical_content_in_different_files_gives_the_same_digest(tmp_path):
    # 一致的判定就靠這個：來源與目標是兩個不同的檔案。
    (tmp_path / "source.yaml").write_text("max_vel_x: 0.8\n", encoding="utf-8")
    (tmp_path / "target.yaml").write_text("max_vel_x: 0.8\n", encoding="utf-8")

    assert digest(str(tmp_path / "source.yaml")) == digest(str(tmp_path / "target.yaml"))


def test_a_single_trailing_newline_changes_the_digest(tmp_path):
    # 比對原始位元組而非解析後的資料：這兩份 YAML 語意相同，但其中一份被人動過。
    (tmp_path / "a.yaml").write_text("max_vel_x: 0.8\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("max_vel_x: 0.8\n\n", encoding="utf-8")

    assert digest(str(tmp_path / "a.yaml")) != digest(str(tmp_path / "b.yaml"))


def test_a_directory_target_raises_not_a_regular_file_rather_than_looking_undeployed(tmp_path):
    # 「不存在」與「不是一般檔案」不可混為一談。都回 None 的話，一個壞掉的目標會被判成
    # 未部署，UI 就提供一鍵寫出——一個修不好真正問題的動作（不變式 2）。
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    with pytest.raises(NotARegularFile) as exc:
        digest(str(directory))

    assert str(directory) in str(exc.value)


def test_a_fifo_target_is_refused_instead_of_hanging_the_scan(tmp_path):
    # #214：目標被換成 FIFO，跟隨式 open 會永久卡住（沒有寫入端就一直等）→ scan 逐筆呼叫
    # digest，一個這種目標讓整支掃描不回。O_NONBLOCK 開得成之後 fstat 認出它不是一般檔案並
    # 具名拒絕，在有限時間內丟 NotARegularFile 而非掛住。
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)

    with pytest.raises(NotARegularFile):
        digest(str(fifo))


def test_a_symlink_target_is_refused_not_followed(tmp_path):
    # #214：目標是符號連結時，跟隨式 open 會去讀連結指到的任意檔案。O_NOFOLLOW 以 ELOOP
    # 拒絕，判為「不是一般檔案」，不讀連結目標。
    real = tmp_path / "real.yaml"
    real.write_text("a: 1\n", encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)

    with pytest.raises(NotARegularFile):
        digest(str(link))


def test_a_target_larger_than_the_cap_is_refused_before_being_hashed(tmp_path):
    # #214：一般檔案 target 也可能異常巨大（S_ISREG 過關但 GB 級），讓每次掃描白花時間。
    # fstat 的 st_size 超過上限就在讀之前具名拒絕（max_bytes 可覆寫，測試給小值）。
    target = tmp_path / "big.yaml"
    target.write_bytes(b"x" * 4096)

    with pytest.raises(ContentTooLarge):
        digest(str(target), max_bytes=1024)


def test_a_parent_without_traverse_permission_is_not_reported_as_missing(tmp_path):
    # #214：父目錄少了 +x，檔案明明在但 lexists 回 False → 先前判「未部署」→ 一鍵寫出。
    # 要看 open 的 errno（EACCES）而非 lexists 才分得出「看不到」與「不存在」。
    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked / "params.yaml"
    target.write_bytes(b"a: 1\n")
    os.chmod(locked, 0o000)

    try:
        with pytest.raises(PathUnreachable):
            digest(str(target))
    finally:
        os.chmod(locked, 0o755)  # 讓 tmp_path 清理得掉


# ── F14：同一套守門也提供位元組讀取 read_target ──────────────────────────────────────────


def _read_target():
    found = getattr(digest_module, "read_target", None)
    assert found is not None, "io.digest 還沒有 read_target（守門的位元組讀取）"
    return found


def test_read_target_returns_the_bytes_of_a_regular_file(tmp_path):
    target = tmp_path / "t.yaml"
    target.write_bytes(b"a: 1\n")

    assert _read_target()(str(target)) == b"a: 1\n"


def test_read_target_returns_none_for_a_missing_file(tmp_path):
    assert _read_target()(str(tmp_path / "nope.yaml")) is None


def test_read_target_refuses_a_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "t.pipe"
    os.mkfifo(fifo)

    with pytest.raises(NotARegularFile):
        _read_target()(str(fifo))


def test_read_target_refuses_a_file_over_the_cap_without_reading_it_in(tmp_path):
    big = tmp_path / "big.yaml"
    big.write_bytes(b"a" * 64)

    with pytest.raises(ContentTooLarge):
        _read_target()(str(big), 32)


def test_read_target_reads_a_file_exactly_at_the_cap(tmp_path):
    # 定案 3：正好上限可讀，上限＋1 拒絕（那一則在上面）。
    exact = tmp_path / "exact.yaml"
    exact.write_bytes(b"a" * 32)

    assert _read_target()(str(exact), 32) == b"a" * 32


def test_read_target_refuses_a_character_device(tmp_path):
    # 修正後才補（修正前讀 /dev/zero 會吃光記憶體；/dev/null 讀立刻 EOF、安全）：
    # S_ISREG 擋得住裝置。
    with pytest.raises(NotARegularFile):
        _read_target()("/dev/null")
