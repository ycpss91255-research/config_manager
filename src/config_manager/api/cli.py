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
import json
import os
import sys
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass

import uvicorn

from config_manager.api.errors import ConfigRepoMissing
from config_manager.api.routes import DEFAULT_ORIGINS, create_app

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080
_DEFAULT_API = f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
_TIMEOUT = 5

# 狀態的中文說法取自 CONTEXT.md。與網頁用的是同一組字，因為使用者在兩邊看到的
# 是同一件事——CLI 說「偏離」而畫面說別的，等於憑介面決定術語。
_STATE_TEXT = {
    "in_sync": "一致",
    "drift": "偏離",
    "missing": "未部署",
}


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


def serve_plan(host: str, port: int, environ: Mapping[str, str]) -> ServePlan:
    """從參數與環境變數決定要起什麼服務。

    config-repo 的位置從環境變數進來，因為那是容器的接線方式（compose 的
    CM_CONFIG_REPO）。這一層是邊界，讀環境變數是它的工作；再往內的每一層都只從
    參數收（ADR-00000011）——包含這個函式本身，所以 environ 是參數而不是隱含輸入。
    """
    repo = environ.get("CM_CONFIG_REPO", "")
    if not repo:
        raise ConfigRepoMissing(
            "CM_CONFIG_REPO 未設定，服務沒有 config-repo 可服務。"
            "下一步：設定它指向掛載進來的 config-repo"
        )

    return ServePlan(
        repo=repo,
        host=host,
        port=port,
        allowed_origins=_allowed_origins(environ),
    )


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
    return parser


def main(argv: list[str]) -> int:
    """進入點。回傳結束碼。"""
    args = _parser().parse_args(argv[1:])

    # 子命令是 required=True，所以走到這裡的只會是這兩個之一；沒有第三條分支可寫，
    # 寫了也永遠不會執行。
    if args.command == "serve":
        return _serve(args.host, args.port)
    return _list(args.api)


def _list(api: str) -> int:
    """列出納管項目與狀態。

    走 HTTP 打與瀏覽器完全相同的那支端點（ADR-00000009），不是自己讀一遍清單檔。
    自己讀的話，「CLI 看到的」與「畫面看到的」就有兩條路徑，而它們遲早會分歧。
    """
    try:
        with urllib.request.urlopen(f"{api}/api/configs", timeout=_TIMEOUT) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        print(
            f"config_manager: 讀不到 {api}/api/configs（{error}）。"
            f"下一步：確認 backend 已啟動，或以 --api 指定它的位址",
            file=sys.stderr,
        )
        return 1

    if not rows:
        print("還沒有納管任何 config。")
        return 0

    width = max(len(row["ref"]) for row in rows)
    for row in rows:
        state = _STATE_TEXT.get(row["state"], row["state"])
        print(f"{state:<4}  {row['ref']:<{width}}  {row['target']}")
    return 0


def _serve(host: str, port: int) -> int:
    try:
        plan = serve_plan(host, port, os.environ)
    except ConfigRepoMissing as error:
        print(f"config_manager: {error}", file=sys.stderr)
        return 2

    uvicorn.run(
        create_app(plan.repo, plan.allowed_origins),
        host=plan.host,
        port=plan.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
