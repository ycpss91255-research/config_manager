"""core/errors — config 清單檔完整性檢查的具名例外。

具名（而非裸 Exception / ValueError），讓呼叫端與測試能精確辨識失敗種類。
訊息一律大聲且具體（不變式 2）。
"""


class ConfigListError(Exception):
    """config 清單檔完整性錯誤的基底。"""


class DuplicateUid(ConfigListError):
    """兩筆條目共用同一個 uid。uid 是唯一的真實識別碼，重號是靜默 bug 的來源。"""


class DuplicateTarget(ConfigListError):
    """兩筆條目寫到同一個目標位置。寫出順序決定最終結果，是靜默 bug。"""


class TargetEscape(ConfigListError):
    """目標路徑含 .. 路徑段，可逃逸到預期目錄外。"""


class InvalidFormat(ConfigListError):
    """format 非允許值。format 明寫、不由副檔名推斷。"""
