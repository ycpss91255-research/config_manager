"""api/cli — 命令列介面（ADR-00000009；測試介面 T10）。

查詢與操作的子命令是 HTTP client，走與瀏覽器完全相同的那組端點——不存在
「CLI 能做但介面不能」或反之的情況，因為根本是同一組端點（設計文件 §3.5.2）。

`serve` 是唯一的例外，它不是 client 而是把服務起起來：總得有一個地方做這件事，
而讓它待在同一支 CLI 裡，容器的啟動指令與開發者手動起服務就是同一條路徑。
"""

import argparse
import json
import os
import sys
import urllib.request

import uvicorn

from config_manager.api.routes import create_app

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

    if args.command == "serve":
        return _serve(args.host, args.port)
    if args.command == "list":
        return _list(args.api)
    return 2


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
    # config-repo 的位置從環境變數進來，因為那是容器的接線方式（compose 的
    # CM_CONFIG_REPO）。這一層是邊界，讀環境變數是它的工作；再往內的每一層
    # 都只從參數收（ADR-00000011）。
    repo = os.environ.get("CM_CONFIG_REPO")
    if not repo:
        print(
            "config_manager: CM_CONFIG_REPO 未設定，服務沒有 config-repo 可服務。"
            "下一步：設定它指向掛載進來的 config-repo",
            file=sys.stderr,
        )
        return 2

    app = create_app(repo, _allowed_origins()) if _allowed_origins() else create_app(repo)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def _allowed_origins() -> list[str]:
    """CM_ALLOWED_ORIGINS 的逗號分隔清單；未設定時回空清單，由 app 用它的預設。

    非本機的部署（用 IP 或主機名開頁面）來源會不同，必須自己指定。指定錯的話
    請求會被瀏覽器擋下，而頁面會顯示「讀不到 config 清單」——大聲失敗，不是
    顯示一份空清單假裝沒事。
    """
    raw = os.environ.get("CM_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
