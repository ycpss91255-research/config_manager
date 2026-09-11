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


class DuplicateSource(ConfigListError):
    """兩筆條目指向同一個 repo 內來源檔。複本其實是同一份，動一個會牽到另一個。

    編碼單射（#192）讓納管不會產生這種狀態，但手改的清單檔仍可能有——與 target 唯一
    是同一種危害的兩個面向：target 是「寫去哪」，source 是「複本存哪」，兩者都不該重號。
    """


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


class AllowedRootsError(Exception):
    """白名單設定檔（allowed-roots.toml，§7.9 / #202）完整性錯誤的基底。

    不併進 `ConfigListError`：那一族講的是「清單檔（config-list.toml）的內容有問題」，
    這一族講的是「白名單**設定檔**的內容有問題」——兩者是不同的檔、不同的 schema，
    處置也不同（前者改清單檔，後者改白名單）。與 T1／T23 的介面分工一致。
    """


class DuplicatePrefix(AllowedRootsError):
    """兩筆白名單根共用同一個路徑前綴。重複前綴是靜默的設定錯誤，訊息指出是哪兩筆。"""


class InvalidPrefix(AllowedRootsError):
    """白名單根的前綴不是合法的絕對路徑：不以 / 開頭，或含 .. 路徑段。

    字面比對即擋下（比照 T4／T5）；realpath 正規化與可見性留給 I/O 層（realpath 是 I/O）。
    """


class RootsUnknownField(AllowedRootsError):
    """白名單設定檔含無法辨識的欄位。格式錯誤須大聲失敗、指名行號，防止由設定檔注入。"""


class RootsMalformed(AllowedRootsError):
    """白名單設定檔的 `roots` 不是 `[[roots]]` 表格串列：是純量、inline 陣列，或其他型別。

    `roots = 5`、`roots = "x"`、`roots = [{{prefix="/a"}}]` 都是**合法 TOML**，但不是本檔要的
    形狀。先前 `_check_unknown_fields` 會在 pydantic 驗型別之前就迭代它、丟出 raw
    TypeError／AttributeError，逃過「結構驗證交給 pydantic、讀取層認得 load 的失敗詞彙」
    的契約（讓 bug 冒成 500 或未攔的 traceback）；dump 也只吃 AoT。故在 load 一開始就以
    具名例外擋下這種形狀，訊息指引改用 `[[roots]]` 書寫。"""


class RootsDumpMismatch(AllowedRootsError):
    """dump 拿到的原樣資訊無法以 prefix 對回白名單根：有一筆缺 prefix，或兩筆共用 prefix。

    與 `DumpMismatch` 是同一種形狀的兩個檔：那一個以 uid 定位清單檔條目，這一個以 prefix
    定位白名單根（白名單根沒有 uid，prefix 就是它的識別碼）。對不回去就會刪錯或漏改，
    兩者都是靜默丟資料。`load` 擋得住，但 dump 的原樣資訊是獨立參數，沒有東西保證它
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


class ParseError(Exception):
    """T6 格式解析的錯誤基底。

    自成一族，不併進 `ConfigListError`。那一族講的是「清單檔（config-list.toml）
    的內容有問題」；這一族講的是「**被管理的那份 config 檔**解析不了」——兩者的來源
    與處置都不同（前者改清單檔，後者改被納管的檔案或它宣告的 format）。
    """


class UnsupportedFormat(ParseError):
    """format 不是支援的五種（yaml／json／toml／ini／raw）之一。

    format 明寫於清單檔、不由副檔名推斷（`.yml`、`.param` 等變體不可靠），所以
    「不支援」是一個明確的值錯誤，不是「猜不出來」。
    """


class SyntaxParse(ParseError):
    """被管理的 config 檔語法本身壞掉，解析不下去。

    訊息含行號（不變式 2：壞掉的檔案要大聲失敗、指得出位置），這樣呼叫端與使用者
    都知道去哪裡看，而不是收到一句「解析失敗」。
    """

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line
