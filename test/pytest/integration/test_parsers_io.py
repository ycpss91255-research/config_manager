"""io/parsers — 改一個值後原子寫回 repo 的來源複本（T6 的 io 半邊，#17）。

整合層：這裡真的碰檔案。核心的原樣保證（註解、引號、順序）由 core/parse 的單元規格釘住；
這裡只驗**接線**：讀對檔、只改那個值、寫回同一路徑、其餘位元組不動、路徑錯時不動檔案。
"""

import pytest

from config_manager.core.errors import UnknownPath
from config_manager.io.parsers import edit_source

_ORIGINAL = "# top\nspeed: 1  # fast\nname: 'x'\n"


def _repo_with_source(tmp_path):
    repo = tmp_path / "repo"
    (repo / "files").mkdir(parents=True)
    (repo / "files" / "a.yaml").write_text(_ORIGINAL, encoding="utf-8")
    return repo, "files/a.yaml"


def test_edit_source_changes_only_that_value_and_keeps_the_rest_byte_identical(tmp_path):
    repo, source = _repo_with_source(tmp_path)

    edit_source(str(repo), source, "yaml", {"speed": 2.0})

    # 被改那一行的行內註解前空白由 ruamel 重排成一個（T6 第二列記的唯一正規化）；其餘逐位元組不動。
    assert (repo / source).read_text(encoding="utf-8") == "# top\nspeed: 2.0 # fast\nname: 'x'\n"


def test_edit_source_returns_the_text_it_wrote(tmp_path):
    repo, source = _repo_with_source(tmp_path)

    written = edit_source(str(repo), source, "yaml", {"speed": 2.0})

    assert written == (repo / source).read_text(encoding="utf-8")


def test_an_unknown_path_leaves_the_file_untouched(tmp_path):
    # 路徑找不到要在寫回之前就擋下（不變式 2：大聲失敗，且不留下半套改動）。
    repo, source = _repo_with_source(tmp_path)

    with pytest.raises(UnknownPath):
        edit_source(str(repo), source, "yaml", {"speed": 2.0, "nope": 1})

    assert (repo / source).read_text(encoding="utf-8") == _ORIGINAL
