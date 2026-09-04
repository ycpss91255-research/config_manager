#!/usr/bin/env bash
#
# 把 `just cfg <verb>` 轉給 CLI，而 CLI 是 HTTP client，打的是瀏覽器用的同一組
# 端點（ADR-00000009）。
#
# 這裡曾經有一道守衛，檢查 cli.py 這個檔案在不在，不在就印「CLI 還不存在」。
# 那個檔案自 #95 起就存在，所以守衛恆真通過——而它印的那則訊息，在唯一會觸發它的
# 情況下（有人刪掉 cli.py）說的也不是實話：那不是「還沒做」，是「被刪了」。
#
# 拿掉它，讓 CLI 自己回答（#106）。verb 有沒有實作是 cli.py 的知識，在這裡再放一份
# 清單就是第二個真實來源，而兩份清單遲早會分歧。argparse 的 "invalid choice" 會把
# 現有的 verb 列出來，那本身就是可行動的；真的沒有 cli.py 時，ModuleNotFoundError
# 會指名那個模組，一樣大聲。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd -P)"
readonly REPO_ROOT

cd "${REPO_ROOT}"
exec python -m config_manager.api.cli "$@"
