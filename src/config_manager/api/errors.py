"""api/errors — 介面層的具名例外。

具名（而非裸 ValueError），讓端點能把它對應到正確的 HTTP 狀態、測試能精確辨識
失敗種類。訊息一律含欄位、原因與下一步——只說「輸入無效」的訊息等於沒說
（不變式 2）。
"""


class SessionError(Exception):
    """編輯階段相關失敗的基底。"""


class InvalidAuthor(SessionError):
    """身分輸入不合法：欄位為空，或含會破壞 git 作者字串的字元。"""


class ConfigRepoMissing(Exception):
    """起服務時沒有 config-repo 可服務：CM_CONFIG_REPO 未設定或為空。

    不歸在 SessionError 底下——它與編輯階段無關，是啟動階段的接線問題。
    """
