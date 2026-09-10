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


class TemporaryLeftBehind(WriterError):
    """寫出失敗，而沒搬成的暫存檔也刪不掉。

    訊息裡同時說出原本的失敗與清理的失敗，`__cause__` 指向前者：清理失敗不該
    蓋掉原本的錯誤，但留在目標目錄裡的 .config_manager-*.tmp 也不該沒有人知道。
    """


class OnboardLeftBehind(Exception):
    """納管中途失敗後，回滾自己也失敗了，repo 沒能回到納管前的狀態。

    與 `TemporaryLeftBehind` 是同一種形狀的兩個場景：那一個是原子寫出的暫存檔清不掉，
    這一個是納管三個寫入動作中途失敗、回滾清不乾淨。兩者都**同時說出**原本的失敗與
    清理的失敗，`__cause__` 指向原本的失敗——清理失敗不該蓋掉真正的錯誤，但留在 repo
    裡的孤兒來源檔也不該沒有人知道（#173）。訊息指名殘留了什麼。
    """


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


class SourceError(Exception):
    """匯入時刻讀取來源失敗的基底（T22）。

    自成一族，不併進 `WriterError`：那一族講的是「寫出到目標」，這一族講的是
    「從外界讀進來」。兩者的處置不同——前者改目標或權限，後者改要納管的那條路徑。
    """


class SourceOutsideRoots(SourceError):
    """來源經 realpath 解析後落在白名單之外。

    與 `TargetOutsideRoots` 是同一種危害的兩個時刻：那一個管寫出，這一個管讀取。
    納管不寫出目標（見 T22），所以寫出端那道檢查在這條路上不會被走到——**讀取
    本身就是危害**，內容一旦被複製進 config repo 就永久留在版控裡。
    """


class SourceNotRegularFile(SourceError):
    """來源不是可讀的一般檔案：目錄、裝置、socket，或斷掉的符號連結。

    設計 §5.1 的路徑檢查要求三件事——落在白名單內、不含 `..` 逃逸、**且為可讀的
    一般檔案**。這一則是第三項。
    """


class SourcePathUnstable(SourceError):
    """解析或開啟期間來源路徑被改動，這一次匯入不成立。

    非 strict 的 `os.path.realpath` **不保證不丟例外**——它只保證不因「路徑不存在」
    而丟。CPython 3.11 的 `_joinrealpath` 在 `os.lstat` 說「這是連結」之後才裸呼叫
    `os.readlink`（`posixpath.py:480`），兩者之間連結被移除，`FileNotFoundError`
    就會往外拋。開檔時 `O_NOFOLLOW` 收到 `ELOOP` 也是同一件事的另一個時刻：解析之後
    最後一段又變成了符號連結，那就是競速本身。

    失敗方向是拒絕，不是讀取——寧可這次匯入不成立，也不讀一份來歷不明的內容。
    """


class SourceAbsent(SourceError):
    """要納管的來源路徑不存在。

    與 `SourceNotRegularFile` 分開：後者是「看到了那個 inode、但它不是一般檔案」，
    這一則是「根本沒有那個 inode」。把「不存在」說成「不是一般檔案」是講不出根據的
    話——我們沒看到它。與 `PreflightError` 家族的 `SourceMissing` 也分開：那一則講的
    是清單檔某條目引用的來源不在 repo 裡（啟動時），這一則是匯入當下要讀的那份檔案。
    """


class BrowseError(Exception):
    """檔案系統瀏覽失敗的基底（GET /api/browse，#185）。

    自成一族：`SourceError` 講的是「讀一份要納管的檔案」，這一族講的是「在白名單內
    列目錄、給使用者挑檔案」。同一道白名單邊界的兩個用途，處置不同。
    """


class BrowseOutsideRoots(BrowseError):
    """要瀏覽的路徑經 realpath 解析後落在白名單之外。與 `SourceOutsideRoots` 是同一道
    邊界的兩個時刻：那一個管納管的讀取，這一個管瀏覽的列舉。"""


class BrowseNotADirectory(BrowseError):
    """要瀏覽的路徑不是目錄。browse 列的是目錄內容；指向檔案時要挑它、不是瀏覽它。"""


class BrowseUnreadable(BrowseError):
    """路徑在白名單內、也是目錄，但列不出來（權限等）。與「不是目錄」分開：後者是
    形狀不對，這一則是碰得到卻讀不動。"""


class SourceUnreachable(SourceError):
    """來源的某一層上層目錄沒有 traverse（`+x`）權限，去不到那個檔案。

    父目錄少了 `+x` 時 `os.path.lexists` 回 False——檔案明明在，卻被判成不存在。
    那正是 `io/digest` 的 docstring 警告過的形狀：把「你到不了它」說成「它不存在」，
    UI 會把一個權限問題呈現成「未部署」，操作者去修錯的東西。訊息指名是**哪一層**
    目錄擋住去路。
    """
