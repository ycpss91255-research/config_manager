"""io/scan — 差異掃描（設計文件 §5.4、圖 8；測試介面 T21）。

把三個各自獨立的東西接起來：讀清單（T15）、算雜湊（T20）、判定狀態（T2）。

**它有自己的測試介面，是因為「接起來」本身會出錯**——順序接反、把來源當目標、
把 None 當成一致——而那三個介面各自全綠都擋不住這類錯誤。

放在 io 而不是 api：它讀檔案系統，且 CLI 與 HTTP 端點都要用它（ADR-00000009：
兩者走同一組端點，端點走同一支掃描）。
"""

import os

from config_manager.core.models import FileEntry
from config_manager.core.state import State, decide
from config_manager.io.digest import digest
from config_manager.io.errors import SourceMissing
from config_manager.io.preflight import read_config_list


def scan(repo: str) -> list[tuple[FileEntry, State]]:
    """逐筆比對目標與來源，回傳每筆的狀態。順序與清單檔一致。

    順序不由這裡決定：畫面要怎麼排是畫面的事，掃描保留清單檔的順序，
    這樣「清單檔第三筆」與「畫面第三列」永遠指同一件事。
    """
    return [(entry, _state_of(repo, entry)) for entry in read_config_list(repo).files]


def _state_of(repo: str, entry: FileEntry) -> State:
    source_hash = digest(os.path.join(repo, entry.source))
    if source_hash is None:
        # 啟動時 T15 驗過來源都在，所以此刻不在代表有人動了 repo。折進「未部署」
        # 會讓一個壞掉的 repo 看起來只是還沒 apply——UI 於是提供一鍵寫出，而那個
        # 動作沒有東西可寫（不變式 2）。
        raise SourceMissing(
            f"掃描時來源內容不見了：{entry.ref} 的來源「{entry.source}」不在 {repo} 裡。"
            f"下一步：還原該檔，或從清單檔移除這筆條目"
        )

    target_hash = digest(entry.target)
    return decide(target_hash is not None, target_hash, source_hash)
