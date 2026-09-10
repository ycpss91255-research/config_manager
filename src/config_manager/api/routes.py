"""api/routes — HTTP 端點（設計文件 §3.5.3；測試介面 T9）。

端點集合由設計文件 §3.5.3 的表決定，不在這裡發明。目前只實作
`GET /api/configs` 的最小形式，其餘隨各自的 issue 逐條加入。

app 由 create_app(repo) 產生而非模組層的全域物件：config-repo 的位置是啟動
參數，做成全域會讓它變成 import 的副作用，測試也就沒辦法在同一個行程裡指向
不同的 repo（ADR-00000011 的同一個理由：輸入從參數進來）。
"""

from collections.abc import Iterable

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config_manager.api.errors import InvalidAuthor
from config_manager.api.session import USER, Identity, author
from config_manager.core.errors import ConfigListError
from config_manager.core.models import FileEntry
from config_manager.core.state import State
from config_manager.io.browse import Entry, Listing, browse
from config_manager.io.errors import BrowseError, ContentUnreadable, SourceError
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.scan import scan


class SessionInput(BaseModel):
    """身分輸入的請求主體。角色預設為一般使用者（預設值落向安全，不變式 4）。"""

    name: str
    email: str
    role: str = USER


class ConfigInput(BaseModel):
    """納管請求的主體：來源路徑、確認過的 format、歧義確認結果（#185）。

    `format` 與 `ambiguity_note` 是確認畫面（#14／#195 的偵測端點）的產物——後端不重做
    偵測，只記下確認過的值（#12 的 D1／D4）。`ambiguity_note` 沒有歧義時是空字串。
    """

    source_path: str
    format: str
    ambiguity_note: str = ""

# 前端是另一個容器、另一個 port（設計文件 §3.1：瀏覽器分別連 frontend 與
# backend），所以頁面對 API 的請求是跨來源的。
#
# 預設值不是 "*"。這個服務改得動機器上的 config，允許任意來源等於讓任何一個
# 被瀏覽的網頁都能對它下指令。預設只放行 compose 起的那個前端；別的部署形態
# 自己用 CM_ALLOWED_ORIGINS 指定（不變式 4：預設值落向安全）。
# api/cli 的 serve_plan 也要它：「沒指定就用預設」這個決定要在計畫上看得見，
# 不能藏在一個只有 create_app 讀得到的私有預設參數裡（#97）。
DEFAULT_ORIGINS = ("http://127.0.0.1:8081", "http://localhost:8081")


def create_app(
    repo: str,
    allowed_origins: Iterable[str] = DEFAULT_ORIGINS,
    allowed_roots: Iterable[str] = (),
) -> FastAPI:
    """建立服務於 repo 這份 config-repo 的 app。

    `allowed_roots` 是納管與檔案瀏覽的白名單——系統可以碰主機上的哪些目錄（§7.9 的安全
    邊界）。預設是空的（什麼都不放行，落向安全，不變式 4）；部署以 `CM_ALLOWED_ROOTS`
    指定（見 `api/cli.serve_plan`）。持久化、可從介面維護的白名單見 #15。
    """
    roots = tuple(allowed_roots)
    app = FastAPI(title="config_manager", docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["content-type"],
    )

    # 目前的身分。一次只有一個編輯階段（ADR-00000014），所以放在 app 上而不是
    # 一個模組層的全域——後者會讓同一個行程裡起兩個 app 互相看見對方的身分。
    held: dict[str, Identity] = {}

    @app.post("/api/session")
    def set_session(payload: SessionInput) -> dict[str, str]:
        """設定使用者身分（設計文件 §3.5.3）。

        **這不是登入。** 沒有密碼、不驗證、角色是自我宣告（ADR-00000020）。
        """
        try:
            identity = author(payload.name, payload.email, payload.role)
        except InvalidAuthor as error:
            # 422 而非 400：輸入的形狀對，值不合法。訊息原樣傳給使用者，因為它
            # 已經寫成可行動的樣子（欄位＋原因＋下一步）。
            raise HTTPException(status_code=422, detail=str(error)) from error

        held["identity"] = identity
        return _as_session(identity)

    @app.get("/api/session")
    def get_session() -> dict[str, str] | None:
        """目前的身分，尚未輸入則回 null。"""
        identity = held.get("identity")
        return _as_session(identity) if identity else None

    @app.get("/api/configs")
    def list_configs() -> list[dict[str, object]]:
        """列出所有納管項目，含狀態（設計文件 §3.5.3）。

        每次請求重新掃描：清單檔與目標檔案是唯一真實來源（ADR-00000002），
        快取一份會讓「畫面上的狀態」與「磁碟上的狀態」有機會不一致——而這個
        系統存在的理由就是要讓那兩者對得上。設計文件 §5.4 也是這樣定的：
        開啟主畫面時掃一次，按下「檢查差異」時再掃一次。
        """
        return [_as_row(entry, state) for entry, state in scan(repo)]

    @app.post("/api/configs")
    def onboard_config(payload: ConfigInput) -> dict[str, object]:
        """納管新檔案（設計文件 §3.5.3）。"""
        return _onboard_config(repo, roots, held, payload)

    @app.get("/api/browse")
    def browse_filesystem(path: str) -> dict[str, object]:
        """檔案系統瀏覽，受白名單限制（設計文件 §3.5.3）。供納管畫面挑檔案用。"""
        return _browse_filesystem(roots, path)

    return app


def _onboard_config(
    repo: str, roots: tuple[str, ...], held: dict[str, Identity], payload: ConfigInput
) -> dict[str, object]:
    """納管新檔案的邏輯。收確認畫面已確認過的請求（來源路徑、format、歧義確認），呼叫
    `io/onboard`。偵測（format／型別／歧義）不在這裡——那在確認畫面就做完了（#12 的 D4、
    #195）。抽成模組層函式，端點的 closure 只負責接線，`create_app` 才不會愈長愈複雜。"""
    identity = held.get("identity")
    if identity is None:
        # 納管會產生一筆變更紀錄，需要作者。沒有身分就無從署名——先設身分。
        raise HTTPException(
            status_code=409,
            detail="尚未設定身分，無法納管。下一步：先 POST /api/session 設定姓名與 email",
        )

    request = OnboardRequest(
        source_path=payload.source_path,
        fmt=payload.format,
        allowed_roots=roots,
        ambiguity_note=payload.ambiguity_note,
    )
    try:
        entry = onboard(repo, request, identity.git_author)
    except (SourceError, ContentUnreadable) as error:
        # 來源路徑的問題：白名單外、不是一般檔案、不存在、上層無 traverse（SourceError
        # 家族），或檔案在、也到得了、但內容讀不出來（ContentUnreadable 不屬該家族，
        # 早先漏接成 500，#175）。都是輸入形狀對、值不合法 → 422。
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConfigListError as error:
        # 與既有條目衝突（target／uid／source 重複）：與目前狀態相牴觸。
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _as_entry(entry)


def _browse_filesystem(roots: tuple[str, ...], path: str) -> dict[str, object]:
    """檔案系統瀏覽的邏輯（同 `_onboard_config`：抽出來讓 `create_app` 保持簡單）。"""
    try:
        listing = browse(path, roots)
    except BrowseError as error:
        # 白名單外、不是目錄、讀不出來：輸入的路徑值不合法。
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _as_listing(listing)


def _as_session(identity: Identity) -> dict[str, str]:
    """身分在畫面上需要的欄位。git_author 一併回傳，讓「紀錄上會是誰」看得見。"""
    return {
        "name": identity.name,
        "email": identity.email,
        "role": identity.role,
        "git_author": identity.git_author,
    }


def _as_row(entry: FileEntry, state: State) -> dict[str, object]:
    """條目在清單畫面上需要的欄位。"""
    return {
        "uid": entry.uid,
        "name": entry.name,
        "hostname": entry.hostname,
        "ref": entry.ref,
        "target": entry.target,
        "format": entry.format,
        "groups": entry.groups,
        "state": state.value,
    }


def _as_entry(entry: FileEntry) -> dict[str, object]:
    """剛納管的條目回給畫面的欄位。不含 state：狀態要掃描才知道，畫面隨後重新
    `GET /api/configs` 就會看到它連同狀態；這裡只回「建立了什麼」。"""
    return {
        "uid": entry.uid,
        "name": entry.name,
        "hostname": entry.hostname,
        "ref": entry.ref,
        "target": entry.target,
        "source": entry.source,
        "format": entry.format,
    }


def _as_listing(listing: Listing) -> dict[str, object]:
    """一次瀏覽在畫面上需要的欄位：解析後的目錄，與底下每一項的名字／種類／路徑。"""
    return {
        "path": listing.path,
        "entries": [_as_browse_entry(entry) for entry in listing.entries],
    }


def _as_browse_entry(entry: Entry) -> dict[str, str]:
    return {"name": entry.name, "kind": entry.kind, "path": entry.path}
