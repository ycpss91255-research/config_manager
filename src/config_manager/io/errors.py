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


class PreflightError(Exception):
    """啟動前置檢查失敗的基底。"""


class ConfigListMissing(PreflightError):
    """config-repo 裡沒有清單檔。首次啟動時 entrypoint 已種下，故此處必為異常。"""


class ConfigListUnparsable(PreflightError):
    """清單檔存在但讀不出來：TOML 語法錯誤，或內容不符清單檔規格。"""


class SourceMissing(PreflightError):
    """清單檔某條目引用的來源內容不在 repo 裡。只查來源側——目標未部署是合法狀態。"""


class ContentUnreadable(Exception):
    """路徑存在但內容讀不出來。與「不存在」分開：後者是未部署，是合法狀態。"""
