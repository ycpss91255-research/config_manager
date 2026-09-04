"""T13 — 編輯階段生命週期（身分部分）。測試介面：api/session 的 author。

身分（誰）與階段（誰正在編輯）是兩件事：使用者可以只是看清單，那不需要取得階段。
這一批測的是身分。

身分輸入不是認證（ADR-00000020、CONTEXT.md「避免使用的說法」）：沒有密碼、
不驗證、角色是自我宣告。它唯一的用途是成為變更紀錄的作者，使變更可追溯到人。

純邏輯，不需要檔案系統也不需要時鐘。
"""

import pytest

from config_manager.api.errors import InvalidAuthor
from config_manager.api.session import DEVELOPER, USER, author


def test_valid_identity_becomes_a_git_author_string():
    # io/git.record 收的作者格式是 `姓名 <email>`——身分存在的理由就是餵給它。
    identity = author("陳小明", "ming@example.com", USER)

    assert identity.git_author == "陳小明 <ming@example.com>"


def test_declared_role_is_recorded_as_declared():
    # 角色是自我宣告，不驗證（ADR-00000020）：宣告開發者就是開發者。這裡驗的是
    # 「記錄的就是宣告的值」，不是「無法偽造」——v0.10.0 前沒有那個保證，也不假裝有。
    assert author("陳小明", "ming@example.com", DEVELOPER).role == DEVELOPER


def test_empty_name_raises_named_exception():
    with pytest.raises(InvalidAuthor) as exc:
        author("", "ming@example.com", USER)

    assert "姓名" in str(exc.value)


def test_empty_email_raises_named_exception():
    with pytest.raises(InvalidAuthor) as exc:
        author("陳小明", "   ", USER)

    assert "email" in str(exc.value).lower()


def test_angle_bracket_in_name_raises_rather_than_being_stripped():
    # `<` 會破壞 git 的作者字串：塞進去之後產生的 commit 作者是另一個人。
    # 悄悄清洗會讓變更紀錄上的名字與使用者輸入的不同，而紀錄的用途正是追溯到人
    # ——紀錄與事實不符，比拒絕輸入嚴重得多（不變式 2）。
    with pytest.raises(InvalidAuthor):
        author("陳小明 <admin@example.com>", "ming@example.com", USER)


def test_newline_in_email_raises():
    # 換行會讓後面的內容變成 commit 訊息的另一行。
    with pytest.raises(InvalidAuthor):
        author("陳小明", "ming@example.com\nSigned-off-by: 別人 <x@y>", USER)


def test_unknown_role_raises_named_exception_listing_the_allowed_values():
    with pytest.raises(InvalidAuthor) as exc:
        author("陳小明", "ming@example.com", "admin")

    message = str(exc.value)
    assert USER in message
    assert DEVELOPER in message


def test_surrounding_whitespace_is_trimmed_not_rejected():
    # 貼上來的字串常帶空白。修剪不會改變身分，與清洗掉 `<` 不同——那會改變它。
    identity = author("  陳小明  ", "  ming@example.com  ", USER)

    assert identity.git_author == "陳小明 <ming@example.com>"
