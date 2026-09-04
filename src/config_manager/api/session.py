"""api/session — 身分與編輯階段（設計文件 §7.8；測試介面 T13）。

**這不是認證機制。** 沒有密碼、沒有驗證、角色是自我宣告（ADR-00000020）。唯一的
用途是成為變更紀錄的作者，使變更可追溯到人。介面文案用「身分輸入」而非「登入」
——說「登入」會讓使用者以為系統有存取控制（CONTEXT.md「避免使用的說法」）。

身分（誰）與階段（誰正在編輯）是兩件事：使用者可以只是看清單，那不需要取得階段。
這一版只有身分；階段的 acquire／renew／release／sweep 隨 #33 加入。

純邏輯，不做 I/O，也不讀時鐘——身分不涉及時間。
"""

from typing import NamedTuple

from config_manager.api.errors import InvalidAuthor

USER = "user"
DEVELOPER = "developer"
ROLES = (USER, DEVELOPER)

# git 的作者字串是 `姓名 <email>`。這三個字元會拆散它：角括號讓作者變成另一個人，
# 換行讓後面的內容變成 commit 訊息的另一行。
_FORBIDDEN = ("<", ">", "\n", "\r")


class Identity(NamedTuple):
    """一個人的身分。角色是他自己宣告的，不是系統判定的。"""

    name: str
    email: str
    role: str

    @property
    def git_author(self) -> str:
        """io/git.record 收的作者格式。"""
        return f"{self.name} <{self.email}>"


def author(name: str, email: str, role: str) -> Identity:
    """由輸入的姓名、email 與角色建立身分。不合法則丟具名例外。"""
    clean_name = _checked("姓名", name)
    clean_email = _checked("email", email)

    if role not in ROLES:
        raise InvalidAuthor(
            f"角色「{role}」不在允許的值裡：{USER}、{DEVELOPER}。"
            f"下一步：在身分輸入頁選擇其中一個"
        )

    return Identity(clean_name, clean_email, role)


def _checked(field: str, value: str) -> str:
    """修剪前後空白並拒絕會破壞 git 作者字串的字元。

    修剪是安全的：貼上來的字串常帶空白，去掉它不改變身分。清洗掉 `<` 就不同了
    ——那會改變身分，而變更紀錄上的名字若與使用者輸入的不同，紀錄與事實就不符。
    紀錄的用途正是追溯到人（ADR-00000020），所以這裡拒絕，不清洗（不變式 2）。
    """
    trimmed = (value or "").strip()
    if not trimmed:
        raise InvalidAuthor(f"{field}是必填的。下一步：在身分輸入頁填寫它")

    for character in _FORBIDDEN:
        if character in trimmed:
            shown = character.encode("unicode_escape").decode("ascii")
            raise InvalidAuthor(
                f"{field}不能含「{shown}」——那會破壞變更紀錄的作者欄位，"
                f"使紀錄上的人與實際輸入的人不同。下一步：移除該字元"
            )
    return trimmed
