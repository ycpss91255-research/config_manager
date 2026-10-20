"""api/cli — 命令列介面（ADR-00000009；測試介面 T10）。

查詢與操作的子命令是 HTTP client，走與瀏覽器完全相同的那組端點——不存在
「CLI 能做但介面不能」或反之的情況，因為根本是同一組端點（設計文件 §3.5.2）。

`serve` 是唯一的例外，它不是 client 而是把服務起起來：總得有一個地方做這件事，
而讓它待在同一支 CLI 裡，容器的啟動指令與開發者手動起服務就是同一條路徑。

**決定要跑什麼與真的跑起來是兩件事。** `serve_plan` 收 environ 當參數、回一份
`ServePlan`，不碰網路也不建 app；`_serve` 拿那份計畫建 app 並交給 uvicorn。分開
之前，讀環境變數、決定放行來源、啟動伺服器擠在同一個函式裡，而測試不會去跑
`uvicorn.run`——於是那些**決定**跟著那一行一起量不到，`api/cli` 是 0%（#97）。
"""

import argparse
import http.client
import http.cookiejar
import json
import os
from datetime import timedelta
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import uvicorn

from config_manager.api.errors import (
    ConfigRepoMissing,
    IdleTimeoutInvalid,
    ModeInvalid,
    ServePortInvalid,
    SessionTimeoutInvalid,
)
from config_manager.api.session import Timeouts
from config_manager.core.roles import DEPLOYMENT, MODES
from config_manager.api.routes import DEFAULT_ORIGINS, create_app
from config_manager.io.backup import bundle, restore
from config_manager.io.errors import BackupError

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080
_DEFAULT_API = f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
_TIMEOUT = 5
# TCP 埠的上界（16-bit）。具名以避開對字面量的比較（ruff PLR2004）。
_MAX_PORT = 65535
# 閒置逾時的上限（秒）：超過一天的「逾時」在現場等於沒有逾時（#48）。
_MAX_IDLE_SECONDS = 86_400

# 狀態的中文說法取自 CONTEXT.md。與網頁用的是同一組字，因為使用者在兩邊看到的
# 是同一件事——CLI 說「偏離」而畫面說別的，等於憑介面決定術語。
_STATE_TEXT = {
    "in_sync": "一致",
    "drift": "偏離",
    "missing": "未部署",
}

# 病態目標（FIFO／裝置／不可 traverse）那一列判不出狀態：標「錯誤」並帶原因，不偽裝成
# 某個狀態（#214，Q3=ii）。與畫面用同一個詞。
_ERROR_TEXT = "錯誤"


@dataclass(frozen=True)
class ServePlan:
    """要起的服務：服務哪一份 config-repo、聽在哪裡、放行哪些來源。

    純資料，沒有 app 也沒有 socket。這份計畫是 `serve` 做的**全部決定**，所以把它
    測完，`serve` 就只剩「照著做」——而照著做的那兩行看得出對錯，不需要靠測試。
    """

    repo: str
    host: str
    port: int
    allowed_origins: tuple[str, ...]
    # 編輯階段的續期逾時（秒）；None＝用預設值（#33）。部署模式的閒置逾時是 #48。
    session_timeout: float | None = None
    # 開發／部署模式（#47）：由後端依環境變數判定、回報給前端；前端改不了它。
    mode: str = DEPLOYMENT
    # 部署模式的閒置逾時（秒）；None＝用預設值（10 分鐘，#48）。開發模式不逾時，設了也不生效。
    idle_timeout: float | None = None

    @property
    def timeouts(self) -> Timeouts:
        """交給 `create_app` 的兩種逾時：沒設定的用預設值。"""
        default = Timeouts()
        return Timeouts(
            renew=_seconds(self.session_timeout, default.renew),
            idle=_seconds(self.idle_timeout, default.idle),
        )


def _seconds(value: float | None, default: timedelta) -> timedelta:
    return default if value is None else timedelta(seconds=value)


def serve_plan(host: str, port: int, environ: Mapping[str, str]) -> ServePlan:
    """從參數與環境變數決定要起什麼服務。

    config-repo 的位置從環境變數進來，因為那是容器的接線方式（compose 的
    CM_CONFIG_REPO）。這一層是邊界，讀環境變數是它的工作；再往內的每一層都只從
    參數收（ADR-00000011）——包含這個函式本身，所以 environ 是參數而不是隱含輸入。
    """
    repo = _config_repo(environ)

    # argparse 的 type=int 只驗整數不驗範圍：0–65535 之外會在 uvicorn 綁 socket 時以
    # OverflowError（非 OSError）炸成裸 traceback。在計畫階段就具名擋下（#250）。
    if not 0 <= port <= _MAX_PORT:
        raise ServePortInvalid(
            f"--port {port} 不在合法範圍（0–{_MAX_PORT}）。下一步：改用該範圍內的埠"
        )

    return ServePlan(
        repo=repo,
        host=host,
        port=port,
        allowed_origins=_allowed_origins(environ),
        session_timeout=_session_timeout(environ),
        mode=_mode(environ),
        idle_timeout=_idle_timeout(environ),
    )


def _config_repo(environ: Mapping[str, str]) -> str:
    """這台機器上那份 config-repo 在哪：`CM_CONFIG_REPO`。serve、backup、restore 三個都問它。"""
    repo = environ.get("CM_CONFIG_REPO", "")
    if not repo:
        raise ConfigRepoMissing(
            "CM_CONFIG_REPO 未設定，不知道 config-repo 在哪。"
            "下一步：設定它指向掛載進來的 config-repo"
        )
    return repo


def _session_timeout(environ: Mapping[str, str]) -> float | None:
    """`CM_SESSION_TIMEOUT`（秒）：編輯階段的續期逾時——持有者這麼久沒續期就回收。未設＝用預設值
    （`api.session.DEFAULT_RENEW_TIMEOUT`），**不是不逾時**：異常中斷的分頁兩種模式都要收得回來。
    不是正數就具名拒絕——寫錯的值不能靜默當成別的意思。"""
    raw = environ.get("CM_SESSION_TIMEOUT", "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        seconds = -1.0
    if seconds <= 0:
        raise SessionTimeoutInvalid(
            f"CM_SESSION_TIMEOUT 必須是正數（秒），現在是 {raw!r}。"
            "下一步：改成如 150（秒），或取消設定改用預設值"
        )
    return seconds


def _idle_timeout(environ: Mapping[str, str]) -> float | None:
    """`CM_IDLE_TIMEOUT`（秒）：部署模式下，一個瀏覽器的人這麼久沒操作就自動退出並釋放編輯階段
    （#48）。未設＝預設 10 分鐘。**不是一天以內的正數就具名拒絕**——寫錯的值（含 nan、inf、
    大到等於不逾時的數）不能靜默當成不逾時，那正是部署模式要防的事（不變式 4）。開發模式不逾時，
    設了也不生效。"""
    raw = environ.get("CM_IDLE_TIMEOUT", "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        seconds = -1.0
    # 寫成「落在範圍內才放行」：nan 與任何數比較都是 False，寫成「超出範圍就拒絕」會放它過去。
    if not 0 < seconds <= _MAX_IDLE_SECONDS:
        raise IdleTimeoutInvalid(
            f"CM_IDLE_TIMEOUT 必須是一天以內的正數（秒），現在是 {raw!r}。"
            "下一步：改成如 600（秒），或取消設定改用預設的 10 分鐘"
        )
    return seconds


def _mode(environ: Mapping[str, str]) -> str:
    """`CM_MODE`：`development` 或 `deployment`（CONTEXT.md「角色與環境」）。

    **未設定＝部署模式。** 忘了設定的機器比較可能是現場那台，而部署模式是較嚴的那一種（閒置
    逾時、不記住裝置），預設落向它不會造成損害（不變式 4）。`.setup.conf` 目前只是佔位檔，沒有人
    讀它，所以判定來源是這個環境變數；compose 以 `CM_MODE` 接線。寫錯的值具名拒絕，不猜。
    """
    raw = environ.get("CM_MODE", "").strip()
    if not raw:
        return DEPLOYMENT
    if raw not in MODES:
        raise ModeInvalid(
            f"CM_MODE 必須是 {' 或 '.join(MODES)}，現在是 {raw!r}。"
            "下一步：改成其中一個，或取消設定改用部署模式"
        )
    return raw


def _allowed_origins(environ: Mapping[str, str]) -> tuple[str, ...]:
    """CM_ALLOWED_ORIGINS 的逗號分隔清單；未設定時回 app 的預設。

    非本機的部署（用 IP 或主機名開頁面）來源會不同，必須自己指定。指定錯的話
    請求會被瀏覽器擋下，而頁面會顯示「讀不到 config 清單」——大聲失敗，不是
    顯示一份空清單假裝沒事。

    沒指定不代表「放行全部」：回的是 DEFAULT_ORIGINS，預設值落向安全（不變式 4）。
    """
    raw = environ.get("CM_ALLOWED_ORIGINS", "")
    named = tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    return named or DEFAULT_ORIGINS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="config_manager")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="啟動 HTTP 服務")
    serve.add_argument("--host", default=_DEFAULT_HOST, help=f"預設 {_DEFAULT_HOST}")
    serve.add_argument("--port", type=int, default=_DEFAULT_PORT, help=f"預設 {_DEFAULT_PORT}")

    listing = subcommands.add_parser("list", help="列出納管項目與狀態")
    listing.add_argument("--api", default=_DEFAULT_API, help=f"預設 {_DEFAULT_API}")

    importing = subcommands.add_parser("import", help="納管一份 config 檔案")
    importing.add_argument("--api", default=_DEFAULT_API, help=f"預設 {_DEFAULT_API}")
    importing.add_argument("--source", required=True, help="要納管的來源檔案路徑")
    _formats = "yaml/json/toml/ini/raw"
    importing.add_argument("--format", required=True, help=f"確認過的 format（{_formats}）")
    importing.add_argument("--note", default="", help="歧義確認結果，寫入 commit 內文（可省略）")
    # 身分屬於各自的 client（#288）：CLI 不是瀏覽器，沒有頁面上輸入過的身分可沿用，納管的作者
    # 要在這裡明寫。角色預設一般使用者（預設值落向安全）。
    importing.add_argument("--name", required=True, help="納管紀錄的作者姓名")
    importing.add_argument("--email", required=True, help="納管紀錄的作者 email")
    importing.add_argument("--role", default="user", help="角色：user（預設）或 developer")

    browsing = subcommands.add_parser(
        "browse", help="列出白名單內某目錄的內容（指向檔案時列它所在的目錄並寫出已選的那一個）"
    )
    browsing.add_argument("--api", default=_DEFAULT_API, help=f"預設 {_DEFAULT_API}")
    browsing.add_argument(
        "--path", required=True, help="要列出的目錄，或白名單內的一般檔案（受白名單限制）"
    )

    inspecting = subcommands.add_parser("inspect", help="偵測候選檔案：格式、歧義、摘要")
    inspecting.add_argument("--api", default=_DEFAULT_API, help=f"預設 {_DEFAULT_API}")
    inspecting.add_argument("--source", required=True, help="要偵測的來源檔案路徑")
    inspecting.add_argument("--format", required=True, help=f"候選 format（{_formats}）")

    # 備份與還原（#52）與 serve 同一類：對本機那份 config-repo（CM_CONFIG_REPO）的操作，不是
    # 端點的 client。服務開著也能備份；還原的目的地要是空的，而且要先把服務停掉——還原寫進去的
    # 那份，跑著的服務不會重新讀。主機端的入口是 script/backup.sh 與 script/restore.sh。
    backing_up = subcommands.add_parser("backup", help="把 config-repo 備份成一個 bundle 檔")
    backing_up.add_argument("destination", help="備份檔要放進哪個目錄（不存在會建）")
    restoring = subcommands.add_parser("restore", help="把備份還原到 CM_CONFIG_REPO（必須是空的）")
    restoring.add_argument("bundle_file", help="要還原的備份檔（.bundle）")
    return parser


def main(argv: list[str]) -> int:
    """進入點。回傳結束碼。"""
    args = _parser().parse_args(argv[1:])
    # 子指令 → 做法。一張表而不是一串 if：加一個子指令是加一列，不是再長一個分支。
    commands: dict[str, Callable[[], int]] = {
        "serve": lambda: _serve(args.host, args.port),
        "import": lambda: _import(
            args.api,
            ImportRequest(args.source, args.format, args.note, args.name, args.email, args.role),
        ),
        "browse": lambda: _browse(args.api, args.path),
        "inspect": lambda: _inspect(args.api, args.source, args.format),
        "backup": lambda: _backup(args.destination),
        "restore": lambda: _restore(args.bundle_file),
        "list": lambda: _list(args.api),
    }
    return commands[args.command]()


def _list(api: str) -> int:
    """列出納管項目與狀態。

    走 HTTP 打與瀏覽器完全相同的那支端點（ADR-00000009），不是自己讀一遍清單檔。
    自己讀的話，「CLI 看到的」與「畫面看到的」就有兩條路徑，而它們遲早會分歧。
    """
    try:
        with urllib.request.urlopen(f"{api}/api/configs", timeout=_TIMEOUT) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # 後端起來了、卻回 4xx/5xx：呈現它的原因，別當連線失敗。HTTPError 是 OSError 子類，
        # 少了這個分支就會被下面的連線失敗文案吞掉，把「起來了但出錯」說成「沒起來」（不變式 2）。
        print(
            f"config_manager: 列出失敗（{_http_detail(error)}）。下一步：依上面的原因修正後重試",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(
            f"config_manager: 讀不到 {api}/api/configs（{error}）。"
            f"下一步：確認 backend 已啟動，或以 --api 指定它的位址",
            file=sys.stderr,
        )
        return 1

    try:
        if not rows:
            print("還沒有納管任何 config。")
            return 0
        width = max(len(row["ref"]) for row in rows)
        for row in rows:
            if row.get("error"):
                # 判不出狀態的那一列：標「錯誤」並帶原因，不印一個偽裝的狀態（不變式 2）。
                print(f"{_ERROR_TEXT:<4}  {row['ref']:<{width}}  {row['target']}  ← {row['error']}")
            else:
                state = _STATE_TEXT.get(row["state"], row["state"])
                print(f"{state:<4}  {row['ref']:<{width}}  {row['target']}")
    except (KeyError, TypeError) as error:
        return _unexpected_response(api, error)
    return 0


@dataclass(frozen=True)
class ImportRequest:
    """一次納管要送的東西：來源、確認過的 format、歧義確認，以及作者（姓名、email、角色）。"""

    source: str
    fmt: str
    note: str
    name: str
    email: str
    role: str


def _import(api: str, wanted: ImportRequest) -> int:
    """納管一份 config：先以 `--name`／`--email` 設身分，取得編輯權，帶識別碼 POST 到與畫面相同的
    /api/configs，做完（含失敗）釋放（ADR-00000009；F23：寫入只接受持有編輯權的那一方，CLI 和
    頁面同一套規則——有人正在編輯時不繞過它）。

    偵測（format／歧義）是確認畫面的事；CLI 這一層要求 `--format` 明寫，與端點收
    「已確認的值」對齊，不在 CLI 自己重做一套偵測。身分屬於各自的 client（#288）：後端發的
    瀏覽器票在 cookie 裡，幾個請求用同一個 cookie 罐，後面的才接得上第一個設的身分。
    """
    jar = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    who = {"name": wanted.name, "email": wanted.email, "role": wanted.role}
    payload = {"source_path": wanted.source, "format": wanted.fmt, "ambiguity_note": wanted.note}
    try:
        with jar.open(_json_request(f"{api}/api/session", who), timeout=_TIMEOUT):
            pass
        token = _take_editing(jar, api)
    except urllib.error.HTTPError as error:
        print(_import_refusal(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, http.client.HTTPException) as error:
        return _unreachable(f"{api}/api/session", error)
    except (KeyError, TypeError) as error:
        return _unexpected_response(api, error)
    try:
        return _onboard_holding(jar, api, payload, token)
    finally:
        _release_editing(jar, api, token)


def _take_editing(jar: urllib.request.OpenerDirector, api: str) -> str:
    """取得編輯權、回識別碼。上一個階段逾時清了草稿：CLI 一取得就把這個數字領走了，說出來。"""
    request = urllib.request.Request(f"{api}/api/session/lock", data=b"", method="POST")
    with jar.open(request, timeout=_TIMEOUT) as response:
        held = json.loads(response.read().decode("utf-8"))
    if not isinstance(held, dict):
        # --api 指到別的服務：200 但不是物件。交給 _unexpected_response 說，不崩成 traceback。
        raise TypeError(f"取得編輯權的回應不是物件：{type(held).__name__}")
    if held.get("cleared_drafts"):
        print(
            f"注意：上一個編輯階段逾時釋放時，清除了 {held['cleared_drafts']} 份未進版的草稿"
            "（不是這次納管造成的）。"
        )
    return str(held["token"])


def _onboard_holding(
    jar: urllib.request.OpenerDirector, api: str, payload: dict[str, str], token: str
) -> int:
    request = _json_request(f"{api}/api/configs", payload, token=token)
    try:
        with jar.open(request, timeout=_TIMEOUT) as response:
            entry = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # 端點以結構化訊息回絕（白名單外、重複…）。把它的 detail 帶出來——那是可行動的
        # （欄位＋原因＋下一步），不是自己另編一句。
        print(
            f"config_manager: 納管失敗（{_http_detail(error)}）。下一步：依上面的原因修正後重試",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, http.client.HTTPException) as error:
        return _unreachable(f"{api}/api/configs", error)
    try:
        print(f"已納管 {entry['ref']} ← {entry['target']}")
    except (KeyError, TypeError) as error:
        return _unexpected_response(api, error)
    return 0


def _release_editing(jar: urllib.request.OpenerDirector, api: str, token: str) -> None:
    request = _json_request(f"{api}/api/session/lock", {"token": token}, method="DELETE")
    try:
        with jar.open(request, timeout=_TIMEOUT):
            pass
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(
            f"config_manager: 編輯權沒能釋放（{error}）。"
            "下一步：不必處理，約 150 秒後會因續期逾時自動收回；急著編輯的話稍候再重新整理頁面",
            file=sys.stderr,
        )


def _import_refusal(error: urllib.error.HTTPError) -> str:
    """設身分或取得編輯權被拒。有人正在編輯（409 held）時說出是誰、從何時開始；其餘照
    `_http_detail` 同一套排成一行（body 只能讀一次，所以先讀出來）。"""
    detail = _error_detail(error)
    if isinstance(detail, dict) and detail.get("kind") == "held":
        holder = detail.get("holder") or {}
        return (
            f"config_manager: 沒有納管：{holder.get('name')}（{holder.get('email')}）"
            f"自 {detail.get('started_at')} 起正在編輯（一次只允許一個編輯階段）。"
            "下一步：等對方關掉頁面或逾時釋放後再試；如果那是你自己在頁面上編輯，先關掉那個分頁"
        )
    reason = f"HTTP {error.code}" if detail is _UNREADABLE else _detail_line(detail)
    return f"config_manager: 納管失敗（{reason}）。下一步：依上面的原因修正後重試"


def _unreachable(url: str, error: Exception) -> int:
    print(
        f"config_manager: 讀不到 {url}（{error}）。"
        "下一步：確認 backend 已啟動，或以 --api 指定它的位址",
        file=sys.stderr,
    )
    return 1


def _json_request(
    url: str, payload: dict[str, str], *, token: str = "", method: str = "POST"
) -> urllib.request.Request:
    headers = {"content-type": "application/json"}
    if token:
        headers["x-cm-editing-token"] = token
    return urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method=method
    )


def _browse(api: str, path: str) -> int:
    """列出白名單內某目錄的內容：GET 與畫面相同的 /api/browse（ADR-00000009）。"""
    query = urllib.parse.urlencode({"path": path})
    try:
        with urllib.request.urlopen(f"{api}/api/browse?{query}", timeout=_TIMEOUT) as response:
            listing = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(
            f"config_manager: 瀏覽失敗（{_http_detail(error)}）。下一步：依上面的原因修正後重試",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(
            f"config_manager: 讀不到 {api}/api/browse（{error}）。"
            f"下一步：確認 backend 已啟動，或以 --api 指定它的位址",
            file=sys.stderr,
        )
        return 1

    try:
        print(listing["path"])
        for entry in listing["entries"]:
            # 目錄尾端加 /，一眼分得出可再進去的與可挑的。
            suffix = "/" if entry["kind"] == "dir" else ""
            print(f"  {entry['name']}{suffix}")
        # 指向檔案時與畫面一樣寫出已選的那一個（F1）；瀏覽目錄時沒有。
        if listing.get("selected"):
            print(f"已選：{listing['selected']}")
    except (KeyError, TypeError, AttributeError) as error:
        return _unexpected_response(api, error)
    return 0


def _inspect(api: str, source: str, fmt: str) -> int:
    """偵測候選檔案：POST 與畫面相同的 /api/inspect（ADR-00000009）。

    format 由呼叫端明寫（`--format`），與端點「收候選 format、不自己猜」一致；印出格式、
    欄位數、原始權限與歧義清單，供人先看過再決定要不要納管。
    """
    payload = {"source_path": source, "format": fmt}
    request = urllib.request.Request(
        f"{api}/api/inspect",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(
            f"config_manager: 偵測失敗（{_http_detail(error)}）。下一步：依上面的原因修正後重試",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, http.client.HTTPException) as error:
        print(
            f"config_manager: 讀不到 {api}/api/inspect（{error}）。"
            f"下一步：確認 backend 已啟動，或以 --api 指定它的位址",
            file=sys.stderr,
        )
        return 1

    try:
        perms = result["permissions"]
        print(f"format：{result['format']}　欄位數：{result['field_count']}")
        print(f"原始權限：{perms['owner']}:{perms['group']} {perms['mode']}")
        if result["ambiguities"]:
            print("歧義（確認後以 import --note 帶進去）：")
            for item in result["ambiguities"]:
                print(f"  第 {item['line']} 行「{item['value']}」：{' / '.join(item['readings'])}")
        else:
            print("沒有歧義。")
    except (KeyError, TypeError) as error:
        return _unexpected_response(api, error)
    return 0


def _http_detail(error: urllib.error.HTTPError) -> str:
    """把 HTTPError 的 body 解出可讀訊息；解不出來就回原始狀態行。

    有些端點的 detail 是結構化物件、有些是字串，這裡兩種都挑出人看的那一段（取 `message`）：
    偵測端點回 `{message, file, line}`（#195），檔案瀏覽的拒絕回 `{kind, message, ...}`（#13），
    納管／白名單維護等回字串。
    """
    detail = _error_detail(error)
    return f"HTTP {error.code}" if detail is _UNREADABLE else _detail_line(detail)


_UNREADABLE = object()  # body 讀不出 detail


def _error_detail(error: urllib.error.HTTPError) -> object:
    """HTTPError body 裡的 detail；讀不出來回 `_UNREADABLE`。body 只能讀一次。"""
    try:
        return json.loads(error.read().decode("utf-8"))["detail"]
    except (OSError, ValueError, http.client.HTTPException, KeyError, TypeError):
        # TypeError：body 是合法 JSON 但非物件（null／陣列／字面值），["detail"] 下標會拋它。
        # 少了它，指到別的服務／代理層時 CLI 會崩成裸 traceback 而非回退狀態行（不變式 2）。
        return _UNREADABLE


def _detail_line(detail: object) -> str:
    """把 detail 排成人看的一行：物件取 `message`（有行號帶行號），驗證錯誤清單逐筆
    「欄位：訊息」。"""
    if isinstance(detail, dict):
        message = detail.get("message", detail)
        line = detail.get("line")
        return f"{message}（第 {line} 行）" if line else str(message)
    if isinstance(detail, list):
        # FastAPI 的 request-validation 錯誤 detail 是 list of {msg, loc, input, ...}——接每筆
        # msg（帶欄位末段）串成一行，不 str() 整個結構：那會把使用者送的輸入（如超長 note）
        # 原樣回吐給使用者，違反 T10「可讀訊息」與不變式 2（#250）。
        return "；".join(_validation_line(item) for item in detail)
    return str(detail)


def _validation_line(item: object) -> str:
    """把一筆 FastAPI 驗證錯誤化成「欄位：訊息」，不含回吐的 input。"""
    if not isinstance(item, dict):
        return str(item)
    field = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
    message = item.get("msg", "")
    return f"{field}：{message}" if field else str(message)


def _unexpected_response(api: str, error: Exception) -> int:
    """回應是 200 但形狀不對（--api 指到別的服務／代理落地頁）：渲染時的欄位存取拋
    KeyError／TypeError。回退成可讀訊息＋退出碼，不讓它崩成裸 traceback（T10、#250）。"""
    print(
        f"config_manager: {api} 回了非預期的回應（{error}）。"
        "下一步：確認 --api 指向的是本服務的 backend",
        file=sys.stderr,
    )
    return 1


def _serve(host: str, port: int) -> int:
    try:
        plan = serve_plan(host, port, os.environ)
    except (
        ConfigRepoMissing,
        IdleTimeoutInvalid,
        ModeInvalid,
        ServePortInvalid,
        SessionTimeoutInvalid,
    ) as error:
        print(f"config_manager: {error}", file=sys.stderr)
        return 2

    uvicorn.run(
        create_app(plan.repo, plan.allowed_origins, plan.timeouts, mode=plan.mode),
        host=plan.host,
        port=plan.port,
        log_level="warning",
    )
    return 0


def _backup(destination: str) -> int:
    """備份到 `destination` 目錄。印出檔案路徑與裡面的 HEAD，那是之後對照用的識別。"""
    try:
        repo = _config_repo(os.environ)
    except ConfigRepoMissing as error:
        print(f"config_manager: {error}", file=sys.stderr)
        return 2
    try:
        made = bundle(repo, destination)
    except BackupError as error:
        print(f"config_manager: {error}", file=sys.stderr)
        return 1
    print(f"config_manager: 備份完成：{made.path}（HEAD {made.head[:7]}，已驗證還原得出來）")
    return 0


def _restore(bundle_file: str) -> int:
    """把 `bundle_file` 還原到 CM_CONFIG_REPO。那裡要是空的，服務要已經停了。"""
    try:
        repo = _config_repo(os.environ)
    except ConfigRepoMissing as error:
        print(f"config_manager: {error}", file=sys.stderr)
        return 2
    try:
        done = restore(bundle_file, repo)
    except BackupError as error:
        print(f"config_manager: {error}", file=sys.stderr)
        return 1
    print(
        f"config_manager: 還原完成：{repo}（HEAD {done.head[:7]}）。"
        "下一步：啟動服務，到頁面按「檢查差異」重新比對目標狀態——缺的目標檔用「寫出到目標」補回，"
        "偏離的到差異檢視處置，系統不會自動覆寫它們"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
