"""core/errors — 核心層的具名例外。

具名（而非裸 Exception / ValueError），讓呼叫端與測試能精確辨識失敗種類。
訊息一律大聲且具體（不變式 2）。

一層一支 errors 模組，與 `io/errors` 同構——那支已經同時裝了 writer、變更紀錄與
前置檢查三個互不相干的家族。核心層的例外集中在這裡，理由相同：呼叫端只要記得
「這一層的例外從哪裡匯入」一件事，而不是每多一個模組就多一個匯入來源。
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


class UnknownField(ConfigListError):
    """清單檔含無法辨識的欄位。格式錯誤須大聲失敗、指名行號（PDF §329）。"""


class DumpMismatch(ConfigListError):
    """dump 的資料模型與原始清單檔不符（改動或移除既有條目）。目前只支援未改動與新增。"""


class UnknownScope(Exception):
    """搜尋範圍不在允許集合內。

    不繼承 ConfigListError：那一族講的是「清單檔的內容有問題」，這一則講的是
    「呼叫端傳了一個不存在的範圍」，來源與處置都不同。`io/errors` 的
    `ContentUnreadable` 同樣獨立成一則，理由一樣。
    """
