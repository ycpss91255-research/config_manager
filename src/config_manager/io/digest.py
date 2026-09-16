"""io/digest — 檔案內容雜湊（測試介面 T20；設計文件 §5.4）。

偏離偵測的另一半。core/state 的 decide 刻意不讀檔，雜湊由呼叫者算好傳入
（ADR-00000011），所以算雜湊的地方在 io 層。呼叫端把兩者接起來：

    target_hash = digest(target)
    decide(target_hash is not None, target_hash, digest(source))

雜湊的是原始位元組，不是解析後的資料。偵測的目標是「有人繞過介面直接修改了
目標」——比對解析後的資料會讓「語意相同、格式不同」看起來一致，而那正是有人
手改過的痕跡。所以連空白與換行的差異都算偏離。

**開檔加固**（#214）：先前用 `os.path.lexists` 早退＋跟隨式 `open(path, "rb")`，有兩個
安全洞——目標被換成 FIFO 時 `open` 永久卡住（`digest` 對 target 逐筆呼叫，一個就讓整支
掃描不回）；祖先目錄缺 traverse 權限時 `lexists` 回 False → 被誤判為「未部署」而提供
一鍵寫出（不變式 2）。改成 `os.open(O_RDONLY|O_NOFOLLOW|O_NONBLOCK)`＋`fstat` 的
`S_ISREG`：`O_NONBLOCK` 讓 FIFO 不卡、`S_ISREG` 擋裝置／目錄／FIFO／socket、`O_NOFOLLOW`
擋 symlink，再依 `open` 的 errno 把失敗分成「不存在（→None）／不可 traverse／不是一般
檔案／讀不出」四種具名結果（比照 `io/source._classify_open_failure`；此處各自持有以免這次
安全修動到已穩定的 read_source）。
"""

import errno
import hashlib
import os
import stat

from config_manager.io.errors import (
    ContentUnreadable,
    NotARegularFile,
    PathUnreachable,
)
from config_manager.io.paths import blocking_parent

# 一次讀進記憶體的分塊大小。內容串流進 hashlib、不留存，所以記憶體是 O(1)。
_CHUNK = 65536


def digest(path: str) -> str | None:
    """回傳 path 的內容 sha256；路徑不存在則回 None，其餘讀不成的情形丟具名例外。

    「不存在」回 None、「存在但讀不成」丟例外——兩者不可混為一談：都回 None 的話，一個
    權限壞掉或被換成 FIFO 的目標會被判成「未部署」，UI 於是提供一鍵寫出，而那個動作修不好
    真正的問題，操作者也不會知道為什麼（不變式 2）。
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        classified = _classify_open_failure(path, error)
        if classified is None:
            return None
        raise classified from error

    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise NotARegularFile(
                f"目標不是一般檔案（{_kind(info.st_mode)}）：{path}。"
                f"下一步：確認該路徑指向一份 config 檔案，不是裝置、目錄或具名管道"
            )
        return _hash(descriptor, path)
    finally:
        os.close(descriptor)


def _hash(descriptor: int, path: str) -> str:
    """把 fd 的內容分塊餵進 sha256。內容不留存，記憶體 O(1)。"""
    hasher = hashlib.sha256()
    try:
        while True:
            block = os.read(descriptor, _CHUNK)
            if not block:
                return hasher.hexdigest()
            hasher.update(block)
    except OSError as error:
        raise ContentUnreadable(
            f"內容讀不出來：{path}（{error.strerror}）。"
            f"下一步：確認它是一般檔案、且執行身分有讀取權限"
        ) from error


def _classify_open_failure(path: str, error: OSError) -> Exception | None:
    """把 `os.open` 的失敗分成四種；「不存在」回 None（未部署，合法狀態），其餘回例外。

    比照 `io/source._classify_open_failure`：把「不存在」「去不到」「不是一般檔案」混成
    同一種等於把訊號消掉，使用者會去改錯的東西（不變式 2）。`digest` 不預先 realpath，所以
    沒有 read_source 的 ELOOP 競速窗口——目標本身是 symlink 時 `O_NOFOLLOW` 直接以 ELOOP
    拒絕，那是「不是一般檔案」。
    """
    if error.errno == errno.ELOOP:
        return NotARegularFile(
            f"目標是一條符號連結，不是一般檔案：{path}。"
            f"下一步：確認該路徑指向 config 檔案本身，不是指向它的連結"
        )

    if error.errno == errno.ENOENT:
        return None

    if error.errno in (errno.EACCES, errno.EPERM):
        blocker = blocking_parent(path)
        if blocker is not None:
            return PathUnreachable(
                f"上層目錄擋住去路（「{blocker}」沒有 traverse 權限）：{path}。"
                f"下一步：給那個目錄加上執行（+x）權限，或改由讀得到它的身分執行"
            )
        return ContentUnreadable(
            f"內容讀不出來：{path}（{error.strerror}）。"
            f"下一步：確認執行身分對它有讀取權限"
        )

    return NotARegularFile(
        f"目標開不起來（{error.strerror}）：{path}。"
        f"下一步：確認它是一份一般檔案（不是裝置、socket 或具名管道）"
    )


def _kind(mode: int) -> str:
    """把 `st_mode` 說成人看得懂的檔案種類，給錯誤訊息用。"""
    if stat.S_ISDIR(mode):
        return "目錄"
    if stat.S_ISFIFO(mode):
        return "具名管道 FIFO"
    if stat.S_ISBLK(mode) or stat.S_ISCHR(mode):
        return "裝置"
    if stat.S_ISSOCK(mode):
        return "socket"
    return "非一般檔案"
