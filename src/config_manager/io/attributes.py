"""io/attributes — 把屬性的修改寫回清單檔並記一筆 `meta`（#286，設計 §7.4.4）。

規則在 `core/attributes`（純邏輯）；這裡做 I/O 的那一半：讀清單檔、交給核心換上新的屬性、以
`core.dump` 寫回（其餘條目的註解、順序、引號樣式逐位元組保留）、stage、記一筆 `meta`——屬性
變更與內容變更分開記，歷史檢視預設的過濾才不會被屬性調整淹沒（§2.3）。

只動清單檔：來源複本在 repo 裡的位置與目標路徑不跟著名稱或主機變（它們記在條目的 `source`／
`target`，改名不搬檔）。寫入步驟任一步失敗就回滾到修改前，回滾也失敗才丟 `AttributesLeftBehind`。
"""

from __future__ import annotations

from config_manager.core.attributes import Attributes, describe_change, update_attributes
from config_manager.core.config_list import dump
from config_manager.core.models import FileEntry
from config_manager.io.errors import AttributesLeftBehind
from config_manager.io.git import record, stage, unstage
from config_manager.io.preflight import CONFIG_LIST_NAME
from config_manager.io.repo import load_list, undo, write_config_list


def update(repo: str, uid: str, wanted: Attributes, author: str) -> FileEntry:
    """把 `uid` 那份 config 的屬性改成 `wanted`，回更新後的條目。"""
    original, current = load_list(repo)
    # 先算好新文字（核心的檢查與 dump 的完整性檢查都在此），此刻 repo 一個位元組都還沒動。
    changed = update_attributes(current, uid, wanted)
    new_text = dump(changed, original)
    before = next(item for item in current.files if item.uid == uid)
    after = next(item for item in changed.files if item.uid == uid)
    try:
        write_config_list(repo, new_text)
        stage(repo, CONFIG_LIST_NAME)
        record(repo, uid, "meta", describe_change(before, after), author)
    except BaseException as failure:
        leftover = undo([
            ("索引", lambda: unstage(repo, CONFIG_LIST_NAME)),
            (CONFIG_LIST_NAME, lambda: write_config_list(repo, original)),
        ])
        if leftover:
            raise AttributesLeftBehind(
                f"修改屬性中途失敗後回滾未竟，殘留：{'；'.join(leftover)}。原本的失敗：{failure}。"
                "下一步：先照原本的失敗處理，再手動核對清單檔是否回到修改前"
            ) from failure
        raise
    return after
