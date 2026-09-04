#!/usr/bin/env bash
#
# 把 `just cfg <verb>` 轉給 CLI，而 CLI 是 HTTP client，打的是瀏覽器用的同一組
# 端點（ADR-00000009）。
#
# 底下的守衛檢查 cli.py 這個檔案在不在。它原本的用意是「CLI 還不存在，就大聲說
# 出來」——比讓它以 ModuleNotFoundError 收場好，更遠比靜默成功好（靜默成功正是
# 不變式 2 要擋的失效形態）。
#
# 但那個檔案自 #95 起就存在了，所以守衛現在恆真通過，而 cli.py 目前只實作
# `serve`：import／scan／apply／revert 四個 verb 都還沒有。這四個 verb 因此被原樣
# 交給 argparse，使用者拿到的是 "invalid choice"，而不是底下那則指名 milestone 的
# 訊息。守衛要判斷的其實是 verb 有沒有實作，不是檔案在不在——記在 #76，留待後續。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
readonly REPO_ROOT

if [[ ! -f "${REPO_ROOT}/src/config_manager/api/cli.py" ]]; then
  printf 'cfg: the CLI does not exist yet.\n' >&2
  printf 'cfg: it is an API client, so it arrives with the API in v0.1.0.\n' >&2
  printf 'cfg: see https://github.com/ycpss91255-research/config_manager/milestone/1\n' >&2
  exit 1
fi

cd "${REPO_ROOT}"
exec python -m config_manager.api.cli "$@"
