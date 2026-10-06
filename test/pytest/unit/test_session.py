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
    DEFAULT_RENEW_TIMEOUT,
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
    return SessionLock(timedelta(minutes=timeout_minutes), tokens=lambda: next(counter))


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


def test_a_session_whose_renewals_stop_is_swept_even_when_no_timeout_was_configured():
    # 續期逾時兩種模式都有：分頁異常中斷（沒走到釋放）後心跳停了，階段要自動回收，否則之後
    # 進來的人永遠唯讀（§7.2.2「異常中斷則靠續期逾時自動釋放」）。沒設定就用預設值，不是「不逾時」。
    lock = SessionLock()
    lock.acquire(_MING, _T0)

    swept = lock.sweep(_T0 + DEFAULT_RENEW_TIMEOUT + timedelta(seconds=1))

    assert [session.holder for session in swept] == [_MING]
    assert lock.acquire(_LIN, _T0 + DEFAULT_RENEW_TIMEOUT + timedelta(seconds=2)).holder == _LIN


def test_a_session_that_keeps_renewing_is_never_swept_however_long_it_lives():
    # 開發模式不因閒置回收：頁面開著、心跳還在，放三天也不回收（閒置逾時是部署模式的事，#48）。
    lock = SessionLock()
    session = lock.acquire(_MING, _T0)
    now = _T0
    while now < _T0 + timedelta(days=3):
        now += DEFAULT_RENEW_TIMEOUT / 2
        lock.renew(session.token, now)

    assert lock.sweep(now) == []
    with pytest.raises(SessionHeld):
        lock.acquire(_LIN, now)


def test_release_with_the_wrong_token_does_nothing():
    lock = _lock()
    lock.acquire(_MING, _T0)

    assert lock.release("someone-elses-token") is False
    assert lock.current is not None


def test_resuming_swaps_the_token_so_a_late_release_of_the_old_one_is_harmless():
    # 重新整理頁面：舊頁面卸載時送出釋放，新頁面拿存著的識別碼接續。兩個請求誰先到不一定——
    # 接續時換一個新的識別碼，晚到的那個「釋放舊識別碼」就什麼都動不了（人工驗證 U38）。
    lock = _lock(timeout_minutes=10)
    session = lock.acquire(_MING, _T0)

    resumed = lock.resume(session.token, _T0 + timedelta(minutes=1))

    assert resumed.token != session.token
    assert (resumed.holder, resumed.started_at) == (session.holder, session.started_at)
    assert lock.release(session.token) is False  # 晚到的舊釋放
    assert lock.holds(resumed.token)


def test_resuming_a_session_that_is_gone_fails_loudly():
    lock = _lock(timeout_minutes=10)
    session = lock.acquire(_MING, _T0)
    lock.release(session.token)

    with pytest.raises(SessionExpired):
        lock.resume(session.token, _T0 + timedelta(minutes=1))


# ── 閒置逾時（T13，#48）────────────────────────────────────────────────────────
# 部署模式：一個瀏覽器（以票為鍵）閒置達設定時間就退出；開發模式不逾時。時間全由呼叫端給（`now`）。

from config_manager.api.session import IdleSeats  # noqa: E402

_TEN = timedelta(minutes=10)


def _seated(timeout=_TEN):
    seats = IdleSeats(timeout)
    seats.declare("ticket-a", _MING, _T0)
    return seats


def test_a_declared_identity_is_there_until_the_idle_timeout():
    seats = _seated()

    assert seats.identity("ticket-a", _T0 + timedelta(minutes=9, seconds=59)) == _MING


def test_an_identity_idle_for_the_timeout_is_gone():
    seats = _seated()

    assert seats.identity("ticket-a", _T0 + _TEN) is None


def test_activity_restarts_the_idle_clock():
    seats = _seated()
    seats.touch("ticket-a", _T0 + timedelta(minutes=9))

    assert seats.identity("ticket-a", _T0 + timedelta(minutes=18)) == _MING
    assert seats.identity("ticket-a", _T0 + timedelta(minutes=19)) is None


def test_activity_after_the_timeout_does_not_bring_the_identity_back():
    # 逾時就是退出：晚到的一次操作不能把已經退出的人悄悄接回來（下一個坐下的可能是別人）。
    seats = _seated()

    assert seats.touch("ticket-a", _T0 + timedelta(minutes=11)) is False
    assert seats.identity("ticket-a", _T0 + timedelta(minutes=11)) is None


def test_expire_reports_each_idle_ticket_exactly_once():
    seats = _seated()
    seats.declare("ticket-b", _LIN, _T0 + timedelta(minutes=5))

    assert seats.expire(_T0 + timedelta(minutes=10)) == ["ticket-a"]
    assert seats.expire(_T0 + timedelta(minutes=11)) == []  # 回報過的不再回報
    assert seats.identity("ticket-b", _T0 + timedelta(minutes=11)) == _LIN  # 別人不受影響


def test_remaining_counts_down_to_the_timeout():
    seats = _seated()

    assert seats.remaining("ticket-a", _T0 + timedelta(minutes=9)) == timedelta(minutes=1)
    assert seats.remaining("ticket-a", _T0 + timedelta(minutes=12)) == timedelta(0)


def test_a_timed_out_ticket_is_remembered_as_timed_out_until_it_declares_again():
    # 介面據此顯示「逾時退出」而不是一張沒頭沒尾的身分輸入頁；重新輸入身分後就不再是逾時狀態。
    seats = _seated()
    seats.expire(_T0 + _TEN)

    assert seats.timed_out("ticket-a") is True
    seats.declare("ticket-a", _LIN, _T0 + timedelta(minutes=11))
    assert seats.timed_out("ticket-a") is False
    assert seats.identity("ticket-a", _T0 + timedelta(minutes=12)) == _LIN


def test_without_a_timeout_nothing_ever_goes_idle():
    # 開發模式：不因閒置退出。
    seats = _seated(timeout=None)

    assert seats.identity("ticket-a", _T0 + timedelta(days=30)) == _MING
    assert seats.expire(_T0 + timedelta(days=30)) == []
    assert seats.remaining("ticket-a", _T0 + timedelta(days=30)) is None


def test_an_unknown_ticket_has_no_identity_and_is_not_timed_out():
    seats = _seated()

    assert seats.identity("never-seen", _T0) is None
    assert seats.timed_out("never-seen") is False
    assert seats.remaining("never-seen", _T0) is None
