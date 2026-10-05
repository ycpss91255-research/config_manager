"""T17 — 角色權限。測試介面：core/roles 的 permits。

驗證的是**權限對照表本身正確**（TEST-PLAN T17），不是「角色無法偽造」——v0.10.0 前沒有那個
保證。期望值來自 CONTEXT.md「角色與環境」：型別指定、白名單、屬性編輯限開發者，其餘兩種角色
都能做。
"""

import pytest

from config_manager.core import roles
from config_manager.core.errors import UnknownAction

_EVERYONE = (roles.EDIT_VALUES, roles.REVERT, roles.HANDLE_DRIFT, roles.ONBOARD)
_DEVELOPER_ONLY = (roles.SPECIFY_TYPES, roles.MAINTAIN_ROOTS, roles.EDIT_ATTRIBUTES)


@pytest.mark.parametrize("action", _EVERYONE)
def test_a_user_may_edit_values_revert_handle_drift_and_onboard_within_roots(action):
    assert roles.permits(roles.USER, action) is True


@pytest.mark.parametrize("action", _DEVELOPER_ONLY)
def test_a_user_may_not_change_types_maintain_roots_or_edit_attributes(action):
    assert roles.permits(roles.USER, action) is False


@pytest.mark.parametrize("action", _EVERYONE + _DEVELOPER_ONLY)
def test_a_developer_may_do_everything(action):
    assert roles.permits(roles.DEVELOPER, action) is True


def test_the_table_covers_every_action_exactly_once():
    # 七個動作、兩邊各自不重複：少列一個動作的話，端點就會對著一個表上沒有的代號問權限。
    assert sorted(roles.ACTIONS) == sorted(_EVERYONE + _DEVELOPER_ONLY)


def test_an_unknown_action_is_a_programming_error_not_a_denial():
    with pytest.raises(UnknownAction, match="動作「刪除整個 repo」"):
        roles.permits(roles.DEVELOPER, "刪除整個 repo")


def test_an_unknown_role_is_denied_even_for_shared_actions():
    # 角色是使用者宣告的輸入；不認得的值不當成任何一種，連一般使用者能做的也不給（不變式 4）。
    assert roles.permits("admin", roles.EDIT_VALUES) is False

