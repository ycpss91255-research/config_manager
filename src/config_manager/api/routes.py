"""api/routes — HTTP 端點（設計文件 §3.5.3；測試介面 T9）。

端點集合由設計文件 §3.5.3 的表決定，不在這裡發明。目前只實作
`GET /api/configs` 的最小形式，其餘隨各自的 issue 逐條加入。

app 由 create_app(repo) 產生而非模組層的全域物件：config-repo 的位置是啟動
參數，做成全域會讓它變成 import 的副作用，測試也就沒辦法在同一個行程裡指向
不同的 repo（ADR-00000011 的同一個理由：輸入從參數進來）。
"""

import os
from collections.abc import Iterable
from datetime import datetime, timezone
from subprocess import CalledProcessError

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config_manager.api.errors import InvalidAuthor
from config_manager.api.session import DEVELOPER, USER, Identity, author
from config_manager.core.errors import (
    ConfigListError,
    DuplicatePrefix,
    InvalidFormat,
    InvalidPrefix,
    NameUnderivable,
    ParseError,
    PrefixNotFound,
    SyntaxParse,
)
from config_manager.core.inference import Ambiguity, find_ambiguous, infer_types
from config_manager.core.models import FileEntry, Permissions
from config_manager.core.parse import Parsed, parse, values
from config_manager.core.state import State
from config_manager.core.whitelist import decide
from config_manager.io.allowed_roots import (
    add_allowed_root,
    read_allowed_roots,
    remove_allowed_root,
    root_prefixes,
)
from config_manager.io.browse import Entry, Listing, browse
from config_manager.io.candidate import count_candidates
from config_manager.io.errors import (
    AllowedRootUnreachable,
    BrowseError,
    BrowseNotADirectory,
    BrowseOutsideRoots,
    BrowseUnreadable,
    CandidateError,
    CandidateNotADirectory,
    CandidatePrefixEscape,
    CandidateUnreadable,
    ContentUnreadable,
    HostnameInvalid,
    OnboardLeftBehind,
    PreflightError,
    SourceError,
    WriterError,
)
from config_manager.io.onboard import OnboardRequest, onboard
from config_manager.io.preflight import read_config_list
from config_manager.io.scan import ScanFailure, scan
from config_manager.io.source import Source, local_hostname, read_source


class SessionInput(BaseModel):
    """身分輸入的請求主體。角色預設為一般使用者（預設值落向安全，不變式 4）。"""

    name: str
    email: str
    role: str = USER


_MAX_AMBIGUITY_NOTE = 10_000


class ConfigInput(BaseModel):
    """納管請求的主體：來源路徑、確認過的 format、歧義確認結果（#185）。

    `format` 與 `ambiguity_note` 是確認畫面（#14／#195 的偵測端點）的產物——後端不重做
    偵測，只記下確認過的值（#12 的 D1／D4）。`ambiguity_note` 沒有歧義時是空字串。
    """

    source_path: str
    format: str
    # 上限防過大 note 讓 import commit 以 OSError(E2BIG) 失敗、漏成裸 500（#222）。歧義確認是
    # 人看過的文字，10K 字元遠夠用、又遠低於 ARG_MAX；超過在 pydantic 就擋成 422，不進 commit。
    ambiguity_note: str = Field(default="", max_length=_MAX_AMBIGUITY_NOTE)


class InspectInput(BaseModel):
    """偵測請求的主體：候選檔案路徑 + 候選 format（#195）。

    `format` 由呼叫端提供（前端可從副檔名預填為建議、使用者可改），伺服器不自己猜——core
    沒有格式偵測器，不變式 8 也禁止副檔名成為持續權威。端點用它 parse、列歧義、算摘要。
    """

    source_path: str
    format: str


class AllowedRootInput(BaseModel):
    """把一個路徑前綴加進白名單的請求主體（§7.9, #202）。

    只收 `prefix`：是誰加的由 session 身分決定（不由請求自報，否則紀錄上的人可造假），
    何時加的由伺服器蓋時間。
    """

    prefix: str


class RemoveRootInput(BaseModel):
    """把一個路徑前綴從白名單移除的請求主體（§7.9, #15）。

    `prefix` 是**檔案原樣儲存的前綴**（檢視清單 `roots[].prefix` 那一份，非 realpath），
    移除以它定位（core.dump 也以原樣定位）。`confirmed` 是「確認在前」：未帶時端點先回
    受影響的納管項目清單、要求確認（不靜默移除，AC3），帶了才真刪。
    """

    prefix: str
    confirmed: bool = False

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
) -> FastAPI:
    """建立服務於 repo 這份 config-repo 的 app。

    納管與檔案瀏覽的白名單（系統可以碰主機上的哪些目錄，§7.9 的安全邊界）是
    `<repo>/allowed-roots.toml`（#202），**每次請求從檔讀**——從介面新增的根要立即生效、
    不必重啟。entrypoint 首次啟動從 `CM_ALLOWED_ROOTS` 種下那份檔（`io/allowed_roots`）。
    """
    app = FastAPI(title="config_manager", docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["content-type"],
    )

    # 清單檔／白名單設定檔在執行期讀不了（被改壞／刪除、掛載漂移）時，PreflightError 家族
    # 會從請求路徑冒出。一處統一映射成帶檔名與下一步的結構化 500，不讓它們變裸 500（#209）。
    app.add_exception_handler(PreflightError, _preflight_error)

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
        return _onboard_config(repo, root_prefixes(repo), held, payload)

    @app.get("/api/browse")
    def browse_filesystem(path: str) -> dict[str, object]:
        """檔案系統瀏覽，受白名單限制（設計文件 §3.5.3）。供納管畫面挑檔案用。"""
        return _browse_filesystem(root_prefixes(repo), path)

    @app.get("/api/candidate-count")
    def candidate_count(prefix: str) -> dict[str, object]:
        """數一個前綴底下有幾個可納管檔（§7.9 新增流程的即時預覽，#206）。

        僅開發者，且**走白名單外**——新增流程要數的前綴依定義還不在白名單內。這是系統唯一
        主動走訪白名單外目錄的讀取路徑，只數 metadata、不讀內容。
        """
        return _candidate_count(held, prefix)

    @app.post("/api/inspect")
    def inspect_source(payload: InspectInput) -> dict[str, object]:
        """偵測候選檔案（§3.5.3 追加，#195）。供納管確認畫面顯示偵測結果。"""
        return _inspect(root_prefixes(repo), payload)

    _register_allowed_roots(app, repo, held)
    return app


def _register_allowed_roots(app: FastAPI, repo: str, held: dict[str, Identity]) -> None:
    """把白名單維護的三個端點（檢視／新增／移除，§7.9）掛上 app。

    抽出來讓 `create_app` 不因這一組路由而過度複雜（C901）；三者同屬白名單維護、放一起讀。
    以 `held` 閉包共用身分，與 `create_app` 裡其餘路由一致。
    """

    @app.get("/api/allowed-roots")
    def list_allowed_roots() -> dict[str, object]:
        """列出目前白名單的根（§7.9, #13／#15）。browse 的起點與「檢視允許範圍」都讀它。

        唯讀、無角色門檻——看白名單允許哪些目錄，兩種角色都該做得到。回 `prefixes`（realpath
        後的前綴，#13 的 browse 消費）與 `roots`（每筆帶原樣 prefix、resolved、誰／何時，#15
        的檢視面板消費）。移除的角色門檻在 DELETE，不在這裡。
        """
        return _allowed_roots_view(repo)

    @app.post("/api/allowed-roots")
    def add_root(payload: AllowedRootInput) -> dict[str, object]:
        """把一個路徑前綴加進白名單（§7.9, #202）。僅開發者可用；記下是誰、何時加的。"""
        return _add_allowed_root(repo, held, payload)

    @app.delete("/api/allowed-roots")
    def remove_root(payload: RemoveRootInput) -> dict[str, object]:
        """把一個路徑前綴從白名單移除（§7.9, #15）。僅開發者可用；確認在前，回更新後的清單。"""
        return _remove_allowed_root(repo, held, payload)


def _reject_nul(value: str, field: str) -> None:
    """含 NUL 字元的路徑讓 `os.path.realpath` 拋 `ValueError`（非 OSError），下層的
    `except OSError` 接不住、漏成裸 500（#222）。先在端點擋下回 422——那是送錯的值、不是
    伺服器的錯。onboard／inspect 的 `source_path` 與 browse 的 `path` 都經此。"""
    if "\x00" in value:
        raise HTTPException(
            status_code=422,
            detail=f"{field} 含 NUL 字元，不是合法的路徑。下一步：移除路徑中的 NUL（\\x00）",
        )


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

    _reject_nul(payload.source_path, "source_path")
    request = OnboardRequest(
        source_path=payload.source_path,
        fmt=payload.format,
        allowed_roots=roots,
        ambiguity_note=_clean_note(payload.ambiguity_note),
    )
    try:
        entry = onboard(repo, request, identity.git_author)
    except (SourceError, ContentUnreadable, NameUnderivable, InvalidFormat) as error:
        # 送進來的值不合法 → 422：來源路徑的問題（白名單外、不是一般檔案、不存在、上層無
        # traverse 的 SourceError 家族；或檔案在、讀不出來的 ContentUnreadable，不屬該家族、
        # 早先漏成 500）、目標路徑推不出名稱（NameUnderivable）、format 不是允許值
        # （InvalidFormat 雖屬 ConfigListError，但那是**送錯值**不是衝突，先接、映 422）。
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ConfigListError as error:
        # 與既有條目衝突（target／uid／source 重複）：與目前狀態相牴觸。
        raise HTTPException(status_code=409, detail=str(error)) from error
    except HostnameInvalid as error:
        # CM_HOSTNAME 設成不安全值：部署層的錯（非請求端能修），大聲失敗成帶訊息的 500，
        # 不是先前那樣漏接成裸 500（#14 盤點發現）。inspect 也做同樣的映射。
        raise HTTPException(status_code=500, detail=str(error)) from error
    except (OnboardLeftBehind, WriterError, CalledProcessError, OSError) as error:
        # 寫入／commit／回滾路徑失敗（#222）：OnboardLeftBehind 是回滾也失敗、指名孤兒殘留；
        # WriterError／CalledProcessError／OSError 是磁碟或 git 出錯。比照 HostnameInvalid 映成
        # 帶訊息的 500（伺服器側的錯、非使用者輸入），不裸 500——OnboardLeftBehind 指名殘留與
        # 下一步的可行動訊息要保住，不被 FastAPI 的「Internal Server Error」蓋掉。
        raise HTTPException(status_code=500, detail=str(error)) from error
    return _as_entry(entry)


# ord < 32 是 C0 控制字元（含 io/git 的 \x1e／\x1f 分隔符）；保留可見字元與換行、tab。
_FIRST_PRINTABLE_ORD = 32


def _clean_note(note: str) -> str:
    """去掉 ambiguity_note 裡的控制字元（保留換行與 tab）。

    它會進 commit 內文，而 io/git 的 history() 以 \\x1e／\\x1f 當紀錄／欄位分隔符解析——含這些
    字元會把整庫的變更紀錄解析切壞（#14 審查的縱深防禦）。前端產的 note 本不含控制字元，這一道
    是擋直打端點的 client（curl、被改的前端）。
    """
    return "".join(
        character
        for character in note
        if character in "\n\t" or ord(character) >= _FIRST_PRINTABLE_ORD
    )


def _require_developer(held: dict[str, Identity], action: str) -> Identity:
    """取得目前 session 身分並要求它是開發者，回傳該身分，否則以具名 HTTP 錯誤擋下。

    白名單維護（新增／移除，§7.9、W2）與候選數預覽（#206）都走白名單外的敏感讀寫，僅開發者
    可用。三個呼叫端共用同一道門檻：**沒有身分 → 409**（先設身分）、**角色不足 → 403**。
    `action` 填在訊息裡（如「維護白名單」「查詢候選檔案數」），讓下一步對得上呼叫端在做的事。
    """
    identity = held.get("identity")
    if identity is None:
        raise HTTPException(
            status_code=409,
            detail=f"尚未設定身分，無法{action}。下一步：先 POST /api/session 設定姓名與 email",
        )
    if identity.role != DEVELOPER:
        raise HTTPException(
            status_code=403,
            detail=f"只有開發者能{action}。下一步：以開發者身分進入，或請開發者代為處理",
        )
    return identity


def _add_allowed_root(
    repo: str, held: dict[str, Identity], payload: AllowedRootInput
) -> dict[str, object]:
    """把 `payload.prefix` 加進白名單。僅開發者可用；`added_by` 取自 session、`added_at` 由
    伺服器蓋時間（抽成模組層函式，同 `_onboard_config`：端點的 closure 只負責接線）。"""
    identity = _require_developer(held, "維護白名單")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        add_allowed_root(repo, payload.prefix, identity.git_author, now)
    except (InvalidPrefix, AllowedRootUnreachable) as error:
        # 送進來的前綴不合法（相對／含 ..）或指向到不了的目錄：輸入的值有問題 → 422。
        raise HTTPException(status_code=422, detail=str(error)) from error
    except DuplicatePrefix as error:
        # 白名單已含這個前綴（存的是 realpath，尾斜線／symlink 都會解析到同一個）：與現狀
        # 衝突 → 409（同 _onboard_config 對重複條目的處置）。core 的訊息已指出是哪兩筆。
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"prefixes": list(root_prefixes(repo))}


def _remove_allowed_root(
    repo: str, held: dict[str, Identity], payload: RemoveRootInput
) -> dict[str, object]:
    """把 `payload.prefix` 從白名單移除。僅開發者可用；確認在前（未帶 confirmed 先回受影響
    清單＋409，不靜默移除，AC3），以檔案原樣 prefix 定位、定位不到 → 404。"""
    identity = _require_developer(held, "維護白名單")

    stored = {root.prefix for root in read_allowed_roots(repo).roots}
    if payload.prefix not in stored:
        # 以檔案原樣 prefix 定位，定位不到就 404——不是衝突（現狀沒這一筆）、不是輸入格式錯。
        raise HTTPException(
            status_code=404,
            detail=f"白名單裡沒有這個前綴：{payload.prefix}。"
            "下一步：以檢視清單列出的原樣前綴為準，確認拼寫與尾斜線",
        )

    if not payload.confirmed:
        # 確認在前（語義同 onboard）：先把受影響的納管項目回給前端、要求確認，不靜默移除。
        raise HTTPException(status_code=409, detail=_confirm_removal_detail(repo, payload.prefix))

    try:
        remove_allowed_root(repo, payload.prefix, identity.git_author)
    except PrefixNotFound as error:
        # 檢查與移除之間被別的請求刪掉了（同一行程一次一個編輯階段，實務上罕見）。
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _allowed_roots_view(repo)


def _confirm_removal_detail(repo: str, prefix: str) -> dict[str, object]:
    """未確認移除時回給前端的結構化 detail：受影響的納管項目＋一句可行動的訊息。"""
    affected = _affected_by_removing(repo, prefix)
    if affected:
        message = (
            "移除這個白名單根前請先確認。以下納管項目的目標落在此前綴底下（僅供知情，"
            "不會自動解除納管）。下一步：確認後再帶 confirmed 送出"
        )
    else:
        message = (
            "移除這個白名單根前請先確認。目前沒有納管項目的目標落在此前綴底下。"
            "下一步：確認後再帶 confirmed 送出"
        )
    return {"kind": "confirm_required", "affected": affected, "message": message}


def _affected_by_removing(repo: str, prefix: str) -> list[dict[str, str]]:
    """移除 `prefix` 會涉及哪些納管項目：清單檔中 target（realpath）落在該前綴（realpath）
    底下的條目。用 core.whitelist.decide、兩邊都 realpath（比照 io/browse 對根與路徑的處理）。

    **資訊性、不連動解除納管**：target 是絕對路徑、apply 不靠白名單（§7.9），所以移除白名單根
    不改變這些項目的部署——只是讓開發者在移除前知情。回 `ref`（指名條目）＋`target`（哪個位置）。
    """
    resolved_prefix = os.path.realpath(prefix)
    return [
        {"ref": entry.ref, "target": entry.target}
        for entry in read_config_list(repo).files
        if decide((resolved_prefix,), os.path.realpath(entry.target)).allowed
    ]


def _allowed_roots_view(repo: str) -> dict[str, object]:
    """白名單的檢視表示：`prefixes`（realpath 後，#13 的 browse 消費）＋`roots`（每筆帶原樣
    prefix〔移除識別碼〕、resolved〔顯示〕、added_by、added_at，#15 的檢視面板消費）。"""
    return {
        "prefixes": list(root_prefixes(repo)),
        "roots": [
            {
                "prefix": root.prefix,
                "resolved": os.path.realpath(root.prefix),
                "added_by": root.added_by,
                "added_at": root.added_at,
            }
            for root in read_allowed_roots(repo).roots
        ],
    }


_BROWSE_KINDS: tuple[tuple[type[BrowseError], str], ...] = (
    (BrowseOutsideRoots, "outside_roots"),
    (BrowseNotADirectory, "not_a_directory"),
    (BrowseUnreadable, "unreadable"),
)


def _browse_kind(error: BrowseError) -> str:
    """把 browse 的具名例外對應成機器可讀的原因碼，供前端依原因＋角色分流（#13）。"""
    for kind_type, kind in _BROWSE_KINDS:
        if isinstance(error, kind_type):
            return kind
    return "browse_error"


_CANDIDATE_KINDS: tuple[tuple[type[CandidateError], str], ...] = (
    (CandidatePrefixEscape, "prefix_escape"),
    (CandidateNotADirectory, "not_a_directory"),
    (CandidateUnreadable, "unreadable"),
)


def _candidate_kind(error: CandidateError) -> str:
    """把候選數的具名例外對應成機器可讀的原因碼，供前端依原因分流（比照 _browse_kind）。"""
    for kind_type, kind in _CANDIDATE_KINDS:
        if isinstance(error, kind_type):
            return kind
    return "candidate_error"


def _candidate_count(held: dict[str, Identity], prefix: str) -> dict[str, object]:
    """候選數預覽的邏輯（僅開發者，#206）：數一個白名單外前綴底下有幾個可納管檔（不讀內容）。

    走白名單外目錄，套與白名單維護一致的開發者門檻（`_require_developer`）。io 的具名例外
    （前綴 escape／不是目錄／讀不出來）映成 422＋結構化 detail，比照 `_browse_filesystem`——
    輸入的路徑值不合法，前端依 `kind` 分流、`message` 是原樣可行動訊息（含下一步）。
    """
    _require_developer(held, "查詢候選檔案數")
    try:
        result = count_candidates(prefix)
    except CandidateError as error:
        detail: dict[str, object] = {"kind": _candidate_kind(error), "message": str(error)}
        raise HTTPException(status_code=422, detail=detail) from error
    return {"count": result.count, "capped": result.capped}


def _browse_filesystem(roots: tuple[str, ...], path: str) -> dict[str, object]:
    """檔案系統瀏覽的邏輯（同 `_onboard_config`：抽出來讓 `create_app` 保持簡單）。"""
    _reject_nul(path, "path")
    try:
        listing = browse(path, roots)
    except BrowseError as error:
        # 白名單外、不是目錄、讀不出來：輸入的路徑值不合法。detail 帶 kind 讓前端分辨
        # 原因（只有 outside_roots 給加白名單入口），message 是原樣可行動訊息（含下一步）；
        # resolved／suggested 讓「加入白名單」預填解析後的目錄，而非使用者打的原字串（#13）。
        detail: dict[str, object] = {"kind": _browse_kind(error), "message": str(error)}
        if error.resolved is not None:
            detail["resolved"] = error.resolved
        if error.suggested is not None:
            detail["suggested"] = error.suggested
        raise HTTPException(status_code=422, detail=detail) from error
    return _as_listing(listing)


def _inspect(roots: tuple[str, ...], payload: InspectInput) -> dict[str, object]:
    """偵測候選檔案的邏輯：讀來源（白名單）→ 以呼叫端給的 format 解析 → 列歧義、算摘要。

    format 由呼叫端提供，伺服器不猜（#195）。歧義**不拒絕**、列在回應（yaml 才會有）；語法
    錯誤才拒絕。錯誤一律結構化（`{message, file, line}`，行號供編輯器就地標示，§3.5.3）——
    這正是 #185 把結構化錯誤延到 #195 的那一塊。
    """
    _reject_nul(payload.source_path, "source_path")
    # 納管當下會用的 hostname，讓確認畫面在按下納管前就能核對機器身分（AC5，#14）。
    # CM_HOSTNAME 設成不安全值是部署層的錯，非請求端能修——大聲失敗成帶訊息的 500，
    # 不是靜默把身分清洗掉，也不是裸 500（不變式 2）。
    try:
        hostname = local_hostname()
    except HostnameInvalid as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    source = _read_for_inspect(roots, payload.source_path)
    text = _decode_for_inspect(source.content, payload)
    try:
        parsed = _parse_for_inspect(text, payload)
        # json／ini 的 Parsed.document 是**原文字串**（為逐位元組 round-trip 而存，#217／
        # ADR-00000029）；型別推斷要的是資料結構，所以一律經 core.parse.values() 取值——它對
        # json／ini 再 parse 一次、對 yaml／toml 回其 round-trip 結構。少了這一步，json／ini 會
        # 回 field_count=0／types={}，在確認畫面上是靜默的假訊號（#195／#217）。
        data = values(parsed)
        types = infer_types(data)
        ambiguities = [_as_ambiguity(a) for a in find_ambiguous(text, payload.format)]
    except RecursionError as error:
        # 刻意構造的深層巢狀會讓 parse／infer_types 遞迴爆掉。擋成結構化 422，不讓它變成
        # 一個裸 500——這些偵測工作先前在 try 之外（#195 資安審查）。
        raise HTTPException(
            status_code=422,
            detail=_inspect_error(
                payload.source_path,
                "檔案巢狀太深，無法分析。下一步：確認它不是刻意構造的深層巢狀，"
                "或改用 raw 格式（只版控、不解析）",
                None,
            ),
        ) from error

    return {
        "format": payload.format,
        "field_count": len(types),
        "ambiguities": ambiguities,
        "types": types,
        "permissions": _as_permissions(source.permissions),
        "hostname": hostname,
    }


def _read_for_inspect(roots: tuple[str, ...], source_path: str) -> Source:
    try:
        return read_source(source_path, roots)
    except (SourceError, ContentUnreadable) as error:
        raise HTTPException(
            status_code=422, detail=_inspect_error(source_path, str(error), None)
        ) from error


def _decode_for_inspect(content: bytes, payload: InspectInput) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail=_inspect_error(
                payload.source_path,
                f"來源不是 UTF-8 文字，無法以 {payload.format} 解析。"
                f"下一步：確認它是文字檔，或改用 raw 格式（只版控、不解析）",
                None,
            ),
        ) from error


def _parse_for_inspect(text: str, payload: InspectInput) -> Parsed:
    try:
        return parse(text, payload.format)
    except SyntaxParse as error:
        # 語法錯誤直接拒絕，帶行號供編輯器就地標示（§3.5.3）。
        raise HTTPException(
            status_code=422, detail=_inspect_error(payload.source_path, str(error), error.line)
        ) from error
    except ParseError as error:
        # format 非允許值（UnsupportedFormat）或其他解析錯誤——接基底 ParseError，不綁死在
        # 個別葉子型別，才不會有哪一種解析錯誤漏成 500（#195 資安審查）。
        raise HTTPException(
            status_code=422, detail=_inspect_error(payload.source_path, str(error), None)
        ) from error


def _inspect_error(file: str, message: str, line: int | None) -> dict[str, object]:
    """結構化錯誤（§3.5.3：檔案、行號、修正建議）。修正建議在 message 的「下一步」。"""
    return {"message": message, "file": file, "line": line}


def _as_ambiguity(ambiguity: Ambiguity) -> dict[str, object]:
    """一筆歧義：行號、原樣的值、可能的讀法（`readings` 是 tuple → 轉成 list 給 JSON）。"""
    return {
        "line": ambiguity.line,
        "value": ambiguity.value,
        "readings": list(ambiguity.readings),
    }


def _as_permissions(permissions: Permissions) -> dict[str, str]:
    return {
        "owner": permissions.owner,
        "group": permissions.group,
        "mode": permissions.mode,
    }


def _as_session(identity: Identity) -> dict[str, str]:
    """身分在畫面上需要的欄位。git_author 一併回傳，讓「紀錄上會是誰」看得見。"""
    return {
        "name": identity.name,
        "email": identity.email,
        "role": identity.role,
        "git_author": identity.git_author,
    }


def _preflight_error(_request: Request, error: Exception) -> JSONResponse:
    """PreflightError 家族（清單檔／白名單設定檔在執行期讀不了）→ 帶檔名與下一步的結構化 500。

    這些例外啟動時由 preflight 攔下退出；服務起來之後從請求路徑冒出時沒有一處接，會變裸
    500——不指名檔、不可行動（不變式 2）。500：伺服器側資料完整性問題，比照 `HostnameInvalid`
    的處理。結構化 `{message, file}` 與 `browse`／`inspect` 的 detail 一致（#209 Q1／Q4）。
    `SourceMissing` 是逐筆條件、由 `scan` 折成 row error，不會走到這裡（#209 Q2）。
    """
    file = getattr(error, "file", None)
    return JSONResponse(
        status_code=500,
        content={"detail": {"message": str(error), "file": file}},
    )


def _as_row(entry: FileEntry, result: State | ScanFailure) -> dict[str, object]:
    """條目在清單畫面上需要的欄位。

    病態目標（FIFO／裝置／symlink／不可 traverse）那一筆帶 `error` 而非 `state`（#214，
    Q3=ii）：一筆比不了不弄垮整份清單，也不裸 500——該列標記錯誤、其餘照顯示。兩個鍵都
    在，畫面不必猜：正常列 state 有值、error 為 null；病態列反之。
    """
    row: dict[str, object] = {
        "uid": entry.uid,
        "name": entry.name,
        "hostname": entry.hostname,
        "ref": entry.ref,
        "target": entry.target,
        "format": entry.format,
        "groups": entry.groups,
    }
    if isinstance(result, ScanFailure):
        row["state"] = None
        row["error"] = result.message
    else:
        row["state"] = result.value
        row["error"] = None
    return row


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
