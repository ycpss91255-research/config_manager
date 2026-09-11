"""T23 — 白名單設定檔載入與寫回。測試介面：core/allowed_roots 的 load / dump。

用語依 CONTEXT.md。測試寫在公開介面上（load / dump），不驗內部實作。
allowed-roots.toml 是 §7.9 持久化、可從介面維護的白名單（#202）：entrypoint 首次啟動
從 CM_ALLOWED_ROOTS 種下，之後以檔為準、可增可減。
"""

import pytest

from config_manager.core.allowed_roots import load
from config_manager.core.errors import (
    DuplicatePrefix,
    InvalidPrefix,
    RootsUnknownField,
)


def test_valid_allowed_roots_file_loads_with_correct_field_values():
    # 一份合法的白名單設定檔。期望值以獨立字面量寫下，不以與實作相同的方式重算
    # （撰寫規則：期望值來自獨立來源）。
    text = """\
roots_version = 1

[[roots]]
prefix   = "/opt/robot/config"
added_by = "Alice"
added_at = "2026-09-11T08:00:00Z"
"""

    allowed = load(text)

    root = allowed.roots[0]
    assert (root.prefix, root.added_by, root.added_at) == (
        "/opt/robot/config",
        "Alice",
        "2026-09-11T08:00:00Z",
    )


def test_duplicate_prefix_raises_named_exception_identifying_both():
    # 兩筆共用同一個前綴。白名單有重複前綴是靜默的設定錯誤（不變式 2）。
    text = """\
roots_version = 1

[[roots]]
prefix = "/opt/robot/config"

[[roots]]
prefix = "/opt/robot/config"
"""

    with pytest.raises(DuplicatePrefix) as caught:
        load(text)

    # 訊息要指得出是哪兩筆（第 1 筆與第 2 筆），不只是「有重複」。
    message = str(caught.value)
    assert "/opt/robot/config" in message
    assert "第 1 筆" in message and "第 2 筆" in message


def test_a_relative_prefix_is_rejected():
    # 相對路徑：白名單根必須是絕對路徑（比照 T4／T5 的字面檢查）。
    text = """\
roots_version = 1

[[roots]]
prefix = "opt/robot/config"
"""

    with pytest.raises(InvalidPrefix):
        load(text)


def test_a_prefix_that_escapes_with_dotdot_is_rejected():
    # 含 .. 的前綴可逃逸到預期目錄外——字面比對即擋下，realpath 留給 io。
    text = """\
roots_version = 1

[[roots]]
prefix = "/opt/robot/../etc"
"""

    with pytest.raises(InvalidPrefix):
        load(text)


def test_an_unrecognised_field_is_rejected_with_its_line_number():
    # 未知欄位（含 warnings 等內部欄位）大聲失敗、指名行號，防止由設定檔注入（比照 T1）。
    text = """\
roots_version = 1

[[roots]]
prefix = "/opt/robot/config"
warnings = "injected"
"""

    with pytest.raises(RootsUnknownField) as caught:
        load(text)

    message = str(caught.value)
    assert "warnings" in message
    assert "第 5 行" in message
