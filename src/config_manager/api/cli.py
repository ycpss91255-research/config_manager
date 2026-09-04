"""api/cli — 命令列介面（ADR-00000009；測試介面 T10）。

查詢與操作的子命令是 HTTP client，走與瀏覽器完全相同的那組端點——不存在
「CLI 能做但介面不能」或反之的情況，因為根本是同一組端點（設計文件 §3.5.2）。

`serve` 是唯一的例外，它不是 client 而是把服務起起來：總得有一個地方做這件事，
而讓它待在同一支 CLI 裡，容器的啟動指令與開發者手動起服務就是同一條路徑。
"""

import argparse
import os
import sys

import uvicorn

from config_manager.api.routes import create_app

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8080


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="config_manager")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="啟動 HTTP 服務")
    serve.add_argument("--host", default=_DEFAULT_HOST, help=f"預設 {_DEFAULT_HOST}")
    serve.add_argument("--port", type=int, default=_DEFAULT_PORT, help=f"預設 {_DEFAULT_PORT}")
    return parser


def main(argv: list[str]) -> int:
    """進入點。回傳結束碼。"""
    args = _parser().parse_args(argv[1:])

    if args.command == "serve":
        return _serve(args.host, args.port)
    return 2


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

    uvicorn.run(create_app(repo), host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
