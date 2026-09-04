"""T20 — 內容雜湊。測試介面：io/digest 的 digest。

偏離偵測的另一半。T2 的 decide 刻意不讀檔，雜湊由呼叫者算好傳入——這裡就是
算那個雜湊的地方，所以它在 io 層（ADR-00000011）。

比對的是原始位元組，不是解析後的資料：偵測的目標是「有人繞過介面動過這個檔案」
（設計文件 §5.4），連空白與換行的差異都算偏離。
"""

import pytest

from config_manager.io.digest import digest
from config_manager.io.errors import ContentUnreadable

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


def test_unreadable_path_raises_rather_than_looking_undeployed(tmp_path):
    # 「不存在」與「讀不出來」不可混為一談。都回 None 的話，一個壞掉的目標會被
    # 判成未部署，UI 就提供一鍵寫出——一個修不好真正問題的動作（不變式 2）。
    directory = tmp_path / "not-a-file"
    directory.mkdir()

    with pytest.raises(ContentUnreadable) as exc:
        digest(str(directory))

    assert str(directory) in str(exc.value)
