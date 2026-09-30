"""api/search — 參數層級搜尋的端點（#32；設計 §7.4.2、§3.3.5；T14）。

`GET /api/search?q=…&scope=…` 是本 repo 對 §3.5.3 的追加（比照 inspect／#122）：五檔範圍
（config 名稱／目標路徑／參數名稱／參數值／全部），回以 config 為單位的命中——介面拿它過濾
左側樹、展開時定位到命中的那一列。

索引**每次請求重建**，不在記憶體裡維護一份：清單檔與來源複本是唯一真實來源（不變式 6），修改後舊值
不再命中、解除納管後索引移除，靠的是「每次都從真實來源重讀」而不是「記得要更新」——後者正是 T14 說
最容易漏的兩處。這個規模（一台機器、幾十份 config）重建的成本可忽略。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from config_manager.core.errors import SyntaxParse, UnknownScope
from config_manager.core.index import ALLOWED_SCOPES, SCOPE_ALL, Entry, index, search_configs
from config_manager.core.parse import parse, values
from config_manager.io.parsers import read_source as read_source_copy
from config_manager.io.preflight import read_config_list


def register_search(app: FastAPI, repo: str) -> None:
    """把搜尋端點掛上 app。抽出來的理由同 `register_history`（C901）。"""

    @app.get("/api/search")
    def search_configs_endpoint(
        q: str = Query(min_length=1), scope: str = SCOPE_ALL
    ) -> dict[str, object]:
        """搜尋（設計 §7.4.2）：`scope` 是五檔之一，預設「全部」（四種範圍的聯集）。"""
        return _search(repo, q, scope)


def _search(repo: str, query: str, scope: str) -> dict[str, object]:
    config_list = read_config_list(repo)
    entries: list[Entry] = []
    unindexed: list[dict[str, str]] = []
    for entry in config_list.files:
        if entry.format == "raw":
            continue  # raw 不解析、沒有參數可索引；config 名稱與目標路徑仍搜得到
        try:
            data = values(parse(read_source_copy(repo, entry.source), entry.format))
        except (OSError, UnicodeDecodeError, SyntaxParse, RecursionError) as error:
            # 一份來源複本壞掉不弄垮整次搜尋：那份的參數不進索引，但說出來（不變式 2）。
            unindexed.append({"uid": entry.uid, "reason": str(error)})
            continue
        if isinstance(data, dict):
            entries += index(entry.uid, data)
    configs = [(entry.uid, entry.name, entry.target) for entry in config_list.files]
    try:
        hits = search_configs(configs, entries, query, scope)
    except UnknownScope as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    by_uid = {entry.uid: entry for entry in config_list.files}
    return {
        "query": query,
        "scope": scope,
        "scopes": list(ALLOWED_SCOPES),
        "hits": [
            {
                "uid": hit.uid,
                "name": by_uid[hit.uid].name,
                "target": by_uid[hit.uid].target,
                "matched": list(hit.matched),
                "params": [{"path": path, "value": value} for path, value in hit.params],
            }
            for hit in hits
        ],
        "unindexed": unindexed,
    }
