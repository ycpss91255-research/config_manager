"""core/roles — 角色與環境（CONTEXT.md「角色與環境」；測試介面 T17；ADR-00000020）。

**角色與環境是兩件正交的事。** 角色（一般使用者／開發者）決定**能做什麼**；環境（開發／部署）
決定**階段行為**（記住裝置、閒置逾時）。兩張表各管一邊、互不查對方：部署環境下仍可能需要
開發者調整白名單，所以「部署模式」不能順手把開發者的權限收走（#47）。

純邏輯：權限對照表就在這裡，是「誰能做什麼」的唯一定義。端點的門檻、介面要不要顯示 🔧
元素，都該問這一張表，不各自寫一份 `role == "developer"`——那種對照散開之後，遲早有一處
的答案跟別處不一樣。

角色是自我宣告，不是認證（ADR-00000020）：這張表說的是「宣告了這個角色的人被允許做什麼」，
不保證「角色無法偽造」。
"""

from config_manager.core.errors import UnknownAction

USER = "user"
DEVELOPER = "developer"
ROLES = (USER, DEVELOPER)

DEVELOPMENT = "development"
DEPLOYMENT = "deployment"
MODES = (DEVELOPMENT, DEPLOYMENT)

# 動作的代號就是介面上說的那句話（CONTEXT.md 的用語），門檻的訊息直接拿來用：
# 「只有開發者能維護白名單」。代號與文案是同一個字串，兩者就不可能分歧。
EDIT_VALUES = "修改參數值"
REVERT = "退版"
HANDLE_DRIFT = "處置偏離"
ONBOARD = "在白名單內納管"
SPECIFY_TYPES = "修改參數型別"
MAINTAIN_ROOTS = "維護白名單"
EDIT_ATTRIBUTES = "編輯 config 屬性"

# 每個人都能做的，與僅開發者能做的（CONTEXT.md「角色與環境」：型別指定、白名單、屬性編輯
# 限開發者）。兩個集合窮舉了所有動作；不在任何一邊的動作是程式寫錯了代號，見 `permits`。
_EVERYONE = frozenset({EDIT_VALUES, REVERT, HANDLE_DRIFT, ONBOARD})
_DEVELOPER_ONLY = frozenset({SPECIFY_TYPES, MAINTAIN_ROOTS, EDIT_ATTRIBUTES})
ACTIONS = tuple(sorted(_EVERYONE | _DEVELOPER_ONLY))


def permits(role: str, action: str) -> bool:
    """`role` 這個角色能不能做 `action`。

    不認得的動作丟 `UnknownAction`——那不是「不允許」，是呼叫端把代號打錯了；回 False 會讓
    一個打錯的門檻看起來只是「這個人權限不夠」（不變式 2）。不認得的角色一律拒絕：角色是
    使用者宣告的輸入，守住預設落向安全（不變式 4）。
    """
    if action not in _EVERYONE and action not in _DEVELOPER_ONLY:
        raise UnknownAction(
            f"動作「{action}」不在權限對照表裡，允許值為 {'／'.join(ACTIONS)}。"
            "下一步：改用 core/roles 定義的動作代號"
        )
    if role == DEVELOPER:
        return True
    if role == USER:
        return action in _EVERYONE
    return False
