"""io/scan — 差異掃描（設計文件 §5.4、圖 8；測試介面 T21）。

把三個各自獨立的東西接起來：讀清單（T15）、算雜湊（T20）、判定狀態（T2）。

**它有自己的測試介面，是因為「接起來」本身會出錯**——順序接反、把來源當目標、
把 None 當成一致——而那三個介面各自全綠都擋不住這類錯誤。

放在 io 而不是 api：它讀檔案系統，且 CLI 與 HTTP 端點都要用它（ADR-00000009：
兩者走同一組端點，端點走同一支掃描）。
"""

import os
from dataclasses import dataclass

from config_manager.core.models import FileEntry
from config_manager.core.state import State, decide
from config_manager.io.digest import digest
from config_manager.io.errors import (
    ContentTooLarge,
    ContentUnreadable,
    NotARegularFile,
    PathUnreachable,
)
from config_manager.io.preflight import read_config_list


@dataclass(frozen=True)
class ScanFailure:
    """某一筆的狀態判不出來：目標存在但不是可讀的一般檔案（FIFO／裝置／目錄／symlink），
    或去不到（上層目錄無 traverse），或讀不出來。

    **不是第五種 State**（設計刻意只留四種）——是「這一筆比不了」的旁路結果，讓一個病態
    目標不弄垮整份掃描（#214，Q3=ii）：該筆回這個、其餘照常判定。`message` 是 `digest`
    丟出的可行動訊息（含路徑與下一步）。
    """

    message: str


def scan(repo: str) -> list[tuple[FileEntry, State | ScanFailure]]:
    """逐筆比對目標與來源，回傳每筆的狀態（或該筆的 `ScanFailure`）。順序與清單檔一致。

    順序不由這裡決定：畫面要怎麼排是畫面的事，掃描保留清單檔的順序，
    這樣「清單檔第三筆」與「畫面第三列」永遠指同一件事。
    """
    return [(entry, _state_of(repo, entry)) for entry in read_config_list(repo).files]


def _state_of(repo: str, entry: FileEntry) -> State | ScanFailure:
    source_hash = digest(os.path.join(repo, entry.source))
    if source_hash is None:
        # 清單檔解析成功、只有這一筆的來源不見（有人動了 repo）。折進「未部署」會讓壞掉的
        # repo 看起來只是還沒 apply——UI 於是提供一鍵寫出，而那個動作沒有東西可寫（不變式 2）。
        # 這一筆逐筆降級成 ScanFailure（#209 Q2）：與 target 病態同構，標記該列、其餘照常，
        # 一筆壞不弄垮整表；也不會在請求路徑冒成裸 500（#209 的整支失敗只留給整份讀不了）。
        return ScanFailure(
            f"來源內容不見了：{entry.ref} 的來源「{entry.source}」不在 repo 裡。"
            f"下一步：還原該檔，或從清單檔移除這筆條目"
        )

    try:
        target_hash = digest(entry.target)
    except (NotARegularFile, PathUnreachable, ContentTooLarge, ContentUnreadable) as error:
        # 一個病態目標（FIFO／裝置／symlink／不可 traverse）不弄垮整份掃描（#214，Q3=ii）：
        # 這一筆回結構化錯誤、其餘照常。digest 的加固讓這裡不會掛住也不誤判未部署。
        return ScanFailure(str(error))
    return decide(target_hash is not None, target_hash, source_hash)
