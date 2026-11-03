"""io/candidate — 候選檔案數預覽（#206，設計 §7.9）.

新增流程要在送出前顯示「此路徑下有幾個可納管檔」。要數的前綴**此刻尚不在白名單內**
（那正是新增流程），故這一支**不以白名單為閘門**、只在前綴底下遞迴數一般檔（不讀內容）
——系統唯一主動走訪白名單外目錄的讀取路徑，比照 #174／#185／#13 做過對抗式審查。

逃逸範式比照 `io/browse`：前綴先過與加入同一條字面檢查（絕對路徑、無 `..`、無 NUL）、再以
`stable_realpath` 解析（第 6 批 F30／F4）、每層以 `O_NOFOLLOW | O_DIRECTORY`
在**父目錄的 fd 上相對重開**再 `scandir`，符號連結以項目自身型別分類、目錄連結不遞迴
（不被騙去數 `/etc`、也切斷連結迴圈）。深度與總項目數兩個上限防打 `/` 或家目錄拖垮服務；
觸上限回**部分計數 + `capped` 旗標**（明確結果，非靜默截斷、非報錯）。

上限走模組常數 + 函式預設參數，`CM_MAX_*` 環境變數覆寫**延後**——對齊 `io/source` 的
`MAX_SOURCE_BYTES` 現況（那個 env 本身也還沒接）。
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass

from config_manager.core.allowed_roots import check_prefix
from config_manager.io.errors import (
    CandidateNotADirectory,
    CandidatePrefixUnstable,
    CandidateUnreadable,
)
from config_manager.io.paths import stable_realpath

_MAX_DEPTH = 64
_MAX_ITEMS = 100_000


@dataclass(frozen=True)
class CandidateCount:
    """一個前綴底下的可納管檔（一般檔）數。

    `capped=True` 表示走訪觸及了深度或總項目數上限，`count` 因此是**下界**而非確切值——
    確認畫面可據以顯示「N+ 個檔」。
    """

    count: int
    capped: bool


def count_candidates(
    prefix: str, *, max_depth: int = _MAX_DEPTH, max_items: int = _MAX_ITEMS
) -> CandidateCount:
    """遞迴數 `prefix` 底下的一般檔案數（不讀內容）。不以白名單為閘門（#206）。

    前綴的檢查與加入白名單**同一判準**（第 6 批 F30／F4）：先過 `core.allowed_roots.check_prefix`
    （NUL、不是絕對路徑、字面 `..` → `InvalidPrefix`，在任何解析之前就擋，相對路徑不會被接上服務的
    工作目錄再說「不存在」），再以 `stable_realpath` 解析——解析不出唯一的真正位置就具名拒絕，
    訊息只引用輸入的前綴，不印沒解析完的假路徑。
    """
    check_prefix(prefix)
    resolved = stable_realpath(prefix)
    if resolved is None:
        raise CandidatePrefixUnstable(
            f"候選數的前綴解析不出唯一的真正位置：{prefix}（路上有連結迴圈，或解析後仍經過連結）。"
            "下一步：直接填那個目錄真正的路徑，不要經過迴圈或多層連結"
        )
    state = _WalkState(max_depth=max_depth, max_items=max_items)
    top = _open_directory(prefix, resolved)
    try:
        _walk(state, top, depth=1)
    finally:
        os.close(top)
    return CandidateCount(count=state.count, capped=state.capped)


def _where(prefix: str, resolved: str) -> str:
    """訊息裡的位置：以輸入的前綴為主詞；解析穩定且與輸入不同時才附真正的位置（Q2）。"""
    return prefix if resolved == prefix else f"{prefix} → {resolved}"


def _open_directory(prefix: str, resolved: str) -> int:
    """以 `O_NOFOLLOW | O_DIRECTORY` 開出前綴目錄的 fd；失敗分類成具名例外（比照 io/browse）。"""
    try:
        return os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM):
            raise CandidateUnreadable(
                f"候選數的前綴列不出來：{_where(prefix, resolved)}（{error.strerror}）。"
                "下一步：確認執行服務的身分對這個目錄有讀取與 traverse（+x）權限"
            ) from error
        # ENOTDIR（不是目錄）、ENOENT（不存在）、ELOOP（最後一段是符號連結）都歸這裡。
        raise CandidateNotADirectory(
            f"候選數的前綴不是可數的目錄：{_where(prefix, resolved)}（{error.strerror}）。"
            "下一步：指向一個真正存在的目錄（不回 0——那會把打錯路徑誤報成沒有可納管檔）"
        ) from error


@dataclass
class _WalkState:
    """遞迴走訪的可變狀態：上限、目前的一般檔計數、走訪過的項目數、是否已觸及上限。

    做成資料物件、走訪做成模組函式（而非一個帶方法的類）——狀態與行為分開，也讓
    `_walk`／`_descend` 各自維持在 max-nested-blocks 之內。
    """

    max_depth: int
    max_items: int
    count: int = 0
    visited: int = 0
    capped: bool = False


def _walk(state: _WalkState, dir_fd: int, depth: int) -> None:
    """在已開啟的目錄 fd 上遞迴數一般檔；觸及深度／項目上限時設 `capped` 並停。"""
    if depth > state.max_depth:
        state.capped = True
        return
    try:
        with os.scandir(dir_fd) as scan:
            for entry in scan:
                state.visited += 1
                if state.visited > state.max_items:
                    state.capped = True
                    return
                if entry.is_file(follow_symlinks=False):
                    state.count += 1
                elif entry.is_dir(follow_symlinks=False):
                    _descend(state, dir_fd, entry.name, depth)
                    if state.capped:
                        return
    except OSError:
        # 走訪途中某層列不出來（權限、競速）：跳過它，不讓整個預覽失敗。前綴本身列不出來
        # 已在 _open_directory 以 CandidateUnreadable 具名處理；這裡是更深層的 best-effort。
        return


def _descend(state: _WalkState, parent_fd: int, name: str, depth: int) -> None:
    """進到子目錄：在**父目錄的 fd 上**相對開檔（`dir_fd=`）並帶 `O_NOFOLLOW`——解析後子目錄
    被抽換成符號連結時開檔失敗，而不是被騙去走它指向的地方。開不成（連結／無權限）就跳過。"""
    try:
        child = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=parent_fd
        )
    except OSError:
        return
    try:
        _walk(state, child, depth + 1)
    finally:
        os.close(child)
