"""io/atomic — 原子替換的共用核心（ADR-00000006、ADR-00000003）。

外部互動層：這裡真的碰檔案系統。核心層不碰（ADR-00000011）。

**「暫存檔 → fsync → 換上去」只該有一份實作。** `io/writer` 在它之上加白名單逃逸
檢查與 owner／group／mode；`io/repo` 直接用它把來源位元組放進 config repo。兩份各自
的原子寫出遲早會在一邊被修好、另一邊沒修——而它守的是「要嘛完整寫入、要嘛完全不動」，
那不是可以有兩個版本的東西。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable

from config_manager.io.errors import TargetNotWritable, TemporaryLeftBehind

_PREFIX = ".config_manager-"
_SUFFIX = ".tmp"


def replace_atomically(
    target: str,
    content: bytes,
    *,
    finalize: Callable[[str], None] | None = None,
) -> None:
    """把 `target` 原子替換成 `content`。要嘛完整寫入，要嘛完全不動。

    順序是暫存檔 → `fsync` → `finalize` → `rename`。rename 在同一個 filesystem 內是
    原子操作，所以任何時刻去看 target，看到的要嘛是舊內容、要嘛是新內容，不會是寫到
    一半的檔案。暫存檔開在 target 同一個目錄裡，才保證跟 target 同一個 filesystem。

    `finalize(暫存檔路徑)` 在 rename 前對暫存檔做最後處理（`io/writer` 用它設 owner／
    group／mode）；不需要就不傳。

    失敗時暫存檔會被清掉；**清不掉時丟 `TemporaryLeftBehind`**，訊息裡同時帶著原本的
    失敗（見 `_discard_temporary`）。
    """
    directory = os.path.dirname(target) or "."
    try:
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=_PREFIX, suffix=_SUFFIX)
    except OSError as error:
        raise TargetNotWritable(
            f"目標目錄無法寫入：{directory}（{error.strerror}）。"
            f"下一步：確認該目錄的權限與擁有者，或以有權限的身分執行。"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            # 內容要先真的落到硬碟，否則 rename 之後斷電會留下一個名字正確、內容
            # 是空的檔案——那正是原子性想避免的半殘狀態。
            os.fsync(handle.fileno())
        if finalize is not None:
            finalize(temporary)
        os.replace(temporary, target)
    except BaseException as failure:
        # 走到這裡就代表 rename 沒成功，暫存檔不留在目標目錄裡。清理完把原本的失敗
        # 原封不動往上拋——它才是使用者要處理的那件事。
        #
        # 用 except 而不是 finally，是為了把「原本的失敗」拿在手上：清理也失敗時，
        # 訊息要同時說出兩件事，而 finally 裡只能靠 sys.exception() 去撈。
        _discard_temporary(temporary, failure)
        raise


def _discard_temporary(temporary: str, failure: BaseException) -> None:
    """刪掉沒有搬成的暫存檔。刪不掉時大聲說，但不蓋掉原本的失敗。

    先前這是 `contextlib.suppress(OSError)`——那就是 `except OSError: pass` 換個拼法，
    而設計 §0.4 明列「不得捕捉後僅 pass」，沒有例外條款。ruff 的 BLE001／E722 都看不到
    suppress，所以它一直是綠的（#121）。

    刪不掉不是「掃地失敗不重要」。成因是 sticky bit、SELinux、NFS 的 stale handle
    這一類——目標目錄裡於是留下一個 `.config_manager-*.tmp`，下一次寫出再留一個，累積
    在一個由本系統管理的目錄裡，而這個系統的賣點正是「這個目錄的內容由我們負責」。

    清理失敗若取代了原本的例外，使用者拿到「暫存檔刪不掉」，真正的失敗原因就不見了。
    **兩件事都說**——訊息裡含原本的失敗，`__cause__` 指向它。
    """
    try:
        os.unlink(temporary)
    except OSError as cleanup_error:
        raise TemporaryLeftBehind(
            f"寫出失敗後，暫存檔 {temporary} 清不掉（{cleanup_error.strerror}）。"
            f"原本的失敗：{failure}。"
            f"下一步：先照原本的失敗處理，另外手動移除該暫存檔——"
            f"留著不管的話，這個目錄會逐次累積 .config_manager-*.tmp。"
        ) from failure
