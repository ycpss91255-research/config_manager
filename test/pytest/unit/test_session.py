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


def test_record_separator_in_name_raises():
    # \x1f／\x1e 是 io/git.history() 切變更紀錄欄位用的分隔符；混進 author 會讓整庫帳本
    # 讀不出來（ValueError）。在身分輸入這一關就擋下，不等到 commit 才失敗。#212。
    with pytest.raises(InvalidAuthor):
        author("陳小\x1f明", "ming@example.com", USER)


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


def test_a_name_with_a_nul_byte_is_rejected():
    # #257：NUL 放行的話會被存進身分，納管時帶進 git commit → subprocess 以 ValueError 失敗
    # （非 OSError，端點 except 接不到）而成裸 500。在輸入這關具名拒絕，比照 `<`。
    with pytest.raises(InvalidAuthor):
        author("a\x00b", "ming@example.com", USER)


# ── 編輯階段（T13 後半，#33）────────────────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

from config_manager.api.session import (  # noqa: E402
    Identity,
    SessionExpired,
    SessionHeld,
    SessionLock,
)

_T0 = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)
_MING = Identity("陳小明", "ming@example.com", "developer")
_LIN = Identity("林巡檢", "lin@example.com", "user")


def _lock(timeout_minutes=10):
    counter = iter(f"token-{n}" for n in range(1, 100))
    timeout = None if timeout_minutes is None else timedelta(minutes=timeout_minutes)
    return SessionLock(timeout, tokens=lambda: next(counter))


def test_the_session_can_be_acquired_when_nobody_holds_it():
    session = _lock().acquire(_MING, _T0)

    assert (session.holder, session.started_at, session.token) == (_MING, _T0, "token-1")


def test_a_second_acquire_is_refused_naming_the_holder_and_the_start_time():
    lock = _lock()
    lock.acquire(_MING, _T0)

    with pytest.raises(SessionHeld) as caught:
        lock.acquire(_LIN, _T0 + timedelta(minutes=1))

    assert (caught.value.holder, caught.value.started_at) == (_MING, _T0)


def test_after_release_someone_else_can_acquire_immediately():
    lock = _lock()
    session = lock.acquire(_MING, _T0)

    assert lock.release(session.token) is True
    assert lock.acquire(_LIN, _T0).holder == _LIN


def test_an_idle_session_is_swept_after_the_timeout_and_others_can_acquire():
    # 部署模式：閒置達設定時間後回收；被回收的那份回給呼叫端（API 據此清草稿並回報）。
    lock = _lock(timeout_minutes=10)
    lock.acquire(_MING, _T0)

    swept = lock.sweep(_T0 + timedelta(minutes=10, seconds=1))

    assert [s.holder for s in swept] == [_MING]
    assert lock.acquire(_LIN, _T0 + timedelta(minutes=11)).holder == _LIN


def test_renewing_keeps_the_session_alive_past_the_original_timeout():
    lock = _lock(timeout_minutes=10)
    session = lock.acquire(_MING, _T0)

    lock.renew(session.token, _T0 + timedelta(minutes=9))

    assert lock.sweep(_T0 + timedelta(minutes=15)) == []


def test_renewing_an_expired_session_fails_loudly_instead_of_reacquiring():
    lock = _lock(timeout_minutes=10)
    session = lock.acquire(_MING, _T0)

    with pytest.raises(SessionExpired):
        lock.renew(session.token, _T0 + timedelta(minutes=30))

    assert lock.current is None  # 沒有替它悄悄重新取得


def test_development_mode_never_sweeps_an_idle_session():
    lock = _lock(timeout_minutes=None)
    lock.acquire(_MING, _T0)

    assert lock.sweep(_T0 + timedelta(days=3)) == []
    with pytest.raises(SessionHeld):
        lock.acquire(_LIN, _T0 + timedelta(days=3))


def test_release_with_the_wrong_token_does_nothing():
    lock = _lock()
    lock.acquire(_MING, _T0)

    assert lock.release("someone-elses-token") is False
    assert lock.current is not None
