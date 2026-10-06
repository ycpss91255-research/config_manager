"""api/history — 歷史查詢與退版的端點與邏輯（設計 §5.3、§7.6；#23／#24）。

自 `api/routes` 拆出：那個模組已近千行（pylint C0302），而歷史／退版是一組自成一體的動作——讀
`io/git.history`、以 `io/git.show` 取舊版內容、交給進版的 `io/promote.apply` 寫回。分層不變
（api → core → io）：這裡只做請求解讀、例外→HTTP 的映射與形狀轉換。
"""

from __future__ import annotations

from subprocess import CalledProcessError

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from config_manager.api.checks import overrides_in
from config_manager.api.browser import Browsers
from config_manager.api.session import Identity
from config_manager.core.drafts import Promotion, Stage
from config_manager.core.errors import SyntaxParse
from config_manager.core.inference import infer_types
from config_manager.core.models import ConfigList, FileEntry
from config_manager.core.parse import parse, values
from config_manager.io.allowed_roots import root_prefixes
from config_manager.io.errors import PromoteLeftBehind, WriterError
from config_manager.io.git import KINDS, Change, history, show
from config_manager.io.preflight import read_config_list
from config_manager.io.promote import apply as apply_promotions
from config_manager.io.schema import schema_revert


class RevertInput(BaseModel):
    """退版的請求：要回到哪一版（該 config 歷史裡的一筆 sha，#24）。"""

    version: str = Field(min_length=7, max_length=40, pattern=r"^[0-9a-fA-F]+$")


def register_history(
    app: FastAPI, repo: str, browsers: Browsers, stage_box: dict[str, Stage]
) -> None:
    """把歷史與退版的端點（§7.6）掛上 app（#23／#24）。抽出來的理由同 `_register_allowed_roots`
    （C901）。"""

    @app.get("/api/configs/{uid}/history")
    def config_history(uid: str, prefix: str | None = None) -> list[dict[str, object]]:
        """單筆的變更歷史（設計文件 §3.5.3）。`?prefix=cfg,adopt` 依類型過濾；不給就只看
        內容變更。"""
        return _config_history(repo, uid, prefix)

    @app.get("/api/configs/{uid}/history/{sha}")
    def version_detail(uid: str, sha: str) -> dict[str, object]:
        """那一版來源複本的內容（本 repo 對 §3.5.3 的追加，#26）：歷史檢視據此算參數層級差異。"""
        return _version_detail(repo, uid, sha)

    @app.post("/api/configs/{uid}/revert")
    def revert_config(
        uid: str, payload: RevertInput, identity: Identity | None = Depends(browsers.current)
    ) -> dict[str, object]:
        """退版（設計文件 §3.5.3、§5.3）：以反向變更寫回舊版內容並寫出目標，不改寫歷史（#24）。"""
        return _revert_config(repo, identity, stage_box, uid, payload)


# 歷史預設只看內容變更（§7.6.1、圖 7）：cfg 與 adopt 才真的改了內容；revert／meta 會干擾判讀，
# import 是起點、unmanage 是終點——都可用 ?prefix= 明點要看的類型（「全部」就六種都給）。
_CONTENT_KINDS = ("cfg", "adopt")


def _config_history(repo: str, uid: str, prefix: str | None) -> list[dict[str, object]]:
    """單筆 config 的變更歷史，最新在前（#23）。類型由 `prefix`（逗號分隔）決定，不給就內容變更。

    未知的類型→422 並列出允許值（送錯的請求，不是靜默當成沒過濾）；uid 不在清單→404。每筆帶
    sha／kind／summary／author／at／body——介面把 kind 對應成行為描述（§7.6.1），不顯示代號。
    """
    require_entry(read_config_list(repo), uid)
    kinds = _CONTENT_KINDS if not prefix else tuple(part.strip() for part in prefix.split(","))
    unknown = [kind for kind in kinds if kind not in KINDS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"prefix 含不是變更類型的值：{'、'.join(unknown)}。"
            f"下一步：只用 {'／'.join(KINDS)} 之中的，以逗號分隔",
        )
    return [
        {
            "sha": change.sha,
            "kind": change.kind,
            "summary": change.summary,
            "author": change.author,
            "at": change.at,
            "body": change.body,
            # 這一筆略過了哪些規則、理由是什麼（#42）；沒有就是空清單。
            "overrides": overrides_in(change.body),
        }
        for change in history(repo, uid)
        if change.kind in kinds
    ]


def _version_detail(repo: str, uid: str, sha: str) -> dict[str, object]:
    """`sha` 那一版的 `{sha, types, values, schema_effect}`，前三者形狀同單筆內容端點（#26）。

    sha 限這份 config 歷史裡的（前綴可）——別份的 sha 也 show 得出內容，但那不是這份的版本→422。
    `raw` 不解析：types 空、values 為 null（同單筆內容端點）。
    """
    entry = require_entry(read_config_list(repo), uid)
    change = _find_version(repo, entry, sha)
    detail: dict[str, object] = {
        "sha": change.sha,
        "types": {},
        "values": None,
        # 退回這一版時 schema 會怎麼變（#39）：restore＝回到當時的 schema、remove＝當時還沒有
        # schema 所以拿掉、null＝不變。介面在確認框先說，不讓它悄悄發生。
        "schema_effect": schema_revert(repo, uid, change.sha)[0],
    }
    if entry.format == "raw":
        return detail
    try:
        data = values(parse(show(repo, change.sha, entry.source), entry.format))
    except (CalledProcessError, UnicodeDecodeError, SyntaxParse, RecursionError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"「{entry.source}」在版本 {change.sha[:7]} 的內容讀不到或解析不了：{error}。"
            "下一步：檢查 config-repo 的這筆紀錄是否完整",
        ) from error
    detail["types"] = infer_types(data)
    detail["values"] = data
    return detail


def _find_version(repo: str, entry: FileEntry, sha: str) -> Change:
    wanted = sha.lower()
    change = next((c for c in history(repo, entry.uid) if c.sha.startswith(wanted)), None)
    if change is None:
        raise HTTPException(
            status_code=422,
            detail=f"版本「{sha}」不在「{entry.name}@{entry.hostname}」的歷史裡。"
            "下一步：從 GET /api/configs/{uid}/history 挑一筆 sha",
        )
    return change


def _revert_config(
    repo: str,
    identity: Identity | None,
    stage_box: dict[str, Stage],
    uid: str,
    payload: RevertInput,
) -> dict[str, object]:
    """退版的邏輯（#24、ADR-00000005）：拿 `version` 那一版的來源內容，當成一筆 kind `revert` 的
    Promotion 交給進版的 `apply`——寫來源複本→記錄（`revert(<uid>): rollback to <sha>`）→寫出目標，
    失敗整批回滾。不 reset、不改寫歷史：退版本身也留在歷史裡、也可以再被退。

    schema 連同內容一起回到那一版（那一版還沒有 schema 就拿掉）：內容與 schema 是一對，拿今天的
    schema 擋舊內容，退版就成了路障（#39）。退版本身**不驗證**——那一版在當時是合格的。

    版本必須是**這份 config** 歷史裡的一筆（拿別份的 sha 會把別人的內容寫進來→422）；這份有未進版
    的草稿時不退（草稿是以退版前的來源為底做的，退了就對不起來——先進版或捨棄，409，不變式 4）。
    """
    if identity is None:
        raise HTTPException(
            status_code=409,
            detail="尚未設定身分，無法退版——變更紀錄需要作者。"
            "下一步：先 POST /api/session 設定姓名與 email",
        )
    config_list = read_config_list(repo)
    entry = require_entry(config_list, uid)
    if uid in stage_box["stage"].drafts:
        raise HTTPException(
            status_code=409,
            detail=f"「{entry.name}@{entry.hostname}」有未進版的草稿，退版會讓草稿對不上來源。"
            "下一步：先進版或捨棄這份草稿，再退版",
        )
    target_change = _find_version(repo, entry, payload.version)
    plan = Promotion(
        uid=uid,
        source=entry.source,
        target=entry.target,
        text=show(repo, target_change.sha, entry.source),
        permissions=entry.permissions or config_list.defaults.permissions,
        summary=f"rollback to {target_change.sha[:7]}",
        kind="revert",
    )
    # schema 跟著回到那一版（#39 的定案，ADR-00000021）：與內容進同一筆紀錄、同進同退。
    schema_effect, companions = schema_revert(repo, uid, target_change.sha)
    try:
        apply_promotions(
            repo, [plan], identity.git_author, root_prefixes(repo), {uid: companions}
        )
    except (PromoteLeftBehind, WriterError, CalledProcessError, OSError) as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    latest = history(repo, uid)[0]
    return {
        "uid": uid,
        "reverted_to": target_change.sha,
        "schema_effect": schema_effect,
        "record": {
            "sha": latest.sha, "kind": latest.kind, "summary": latest.summary,
            "author": latest.author, "at": latest.at,
        },
    }


def require_entry(config_list: ConfigList, uid: str) -> FileEntry:
    """清單檔裡 uid 的條目；沒有→404（介面該重新整理清單）。routes 的單筆內容／草稿也用這一支。"""
    entry = next((item for item in config_list.files if item.uid == uid), None)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"清單檔裡沒有 uid「{uid}」的條目。"
            "下一步：重新整理清單，確認該 config 仍在納管中",
        )
    return entry


def source_unreadable(entry: FileEntry, error: Exception) -> HTTPException:
    """來源複本讀不到或解析不了：伺服器端資料的問題，非請求端能修——帶檔名與下一步的 500。
    單筆內容與儲存草稿都會遇到，共用同一個說法。"""
    return HTTPException(
        status_code=500,
        detail=f"「{entry.source}」的來源複本讀不到或解析不了：{error}。"
        "下一步：檢查 config-repo 裡這份檔案是否被改壞或移走",
    )
