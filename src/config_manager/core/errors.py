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
    """dump 拿到的原樣資訊無法以 uid 對回條目：有一筆沒有 uid，或兩筆共用 uid。

    三種變更（新增／改動／移除）都由 dump 支援，所以這一則講的不是「還不支援」，
    而是**定位不到**：dump 靠 uid 把模型的每一筆對回原文的那一筆（uid 納管後
    永不變更，ADR-00000012）。對不回去就會刪錯或漏改，兩者都是靜默丟資料。
    `load` 擋得住這兩種清單檔，但 dump 的原樣資訊是獨立參數，沒有東西保證它
    經過 `load`。
    """


class UnknownScope(Exception):
    """搜尋範圍不在允許集合內。

    不繼承 ConfigListError：那一族講的是「清單檔的內容有問題」，這一則講的是
    「呼叫端傳了一個不存在的範圍」，來源與處置都不同。`io/errors` 的
    `ContentUnreadable` 同樣獨立成一則，理由一樣。
    """


class NameUnderivable(Exception):
    """目標路徑推導不出名稱：不是絕對路徑，或最後二層裡有一層是空的。

    **放這裡而不是 `core/identity.py` 自己一支 errors。** 本檔開頭那段講的是
    「一層一支 errors 模組」，而那個決定是為了讓呼叫端只要記得一件事：這一層的
    例外從哪裡匯入。`core/` 現在有 config_list、identity、index、state 四個模組，
    照模組分開會得到四個匯入來源，換來的只是把已經寫在每一則 docstring 裡的歸屬
    再寫進檔名一次。被否決的另一個選項是塞進 `ConfigListError` 那一族——不行，
    那一族講的是「清單檔的內容有問題」，這一則的來源是呼叫端傳進來的一個路徑
    字串，與清單檔無關，處置也不同（改呼叫端，不是改清單檔）。與 `UnknownScope`
    同一個形狀，所以同樣直接繼承 `Exception`。

    **為什麼不是回一個湊合的名字。** 完整參照形式是 `<name>@<hostname>-<uid>`
    （CONTEXT 身分欄位）。`/opt/robot/` 湊得出 `robot-`、`params.yaml` 湊得出
    `params`——前者接進參照形式之後分不出哪一段是 name，後者悄悄把一個相對路徑
    當成合法的目標位置。兩者都是不變式 2 禁止的靜默處理：消掉的是訊號，不是麻煩。
    """
