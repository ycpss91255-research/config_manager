"""io/errors — IO 層的具名例外。

具名（而非裸 OSError），讓呼叫端與測試能精確辨識失敗種類。訊息一律含路徑、
原因與下一步——只說「操作失敗」的訊息等於沒說（不變式 2）。
"""


class WriterError(Exception):
    """寫出失敗的基底。"""


class TargetNotWritable(WriterError):
    """目標所在的目錄不可寫。"""


class TargetOutsideRoots(WriterError):
    """目標路徑（或其父目錄）解析後落在允許的根目錄之外。"""


class OwnershipRefused(WriterError):
    """要求的 owner/group 設不上去：名字查不到，或沒有權限。"""


class ChangeError(Exception):
    """變更紀錄失敗的基底。"""


class UnknownKind(ChangeError):
    """變更類型不在允許的集合內。"""
