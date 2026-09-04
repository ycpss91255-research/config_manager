#!/usr/bin/env bash
# post-exec hook：在 `just docker exec` 之後執行。
#
# 刻意留空。這是共用容器模板定義的擴充點；現在先佔位並接好線，之後要加一個
# 步驟時只改這個檔案，不必動 wrapper。post-hook 失敗時工作已經做完了，但離開
# 狀態仍會傳出去，wrapper 以非零碼結束。
set -euo pipefail

# 注意：沒有任何東西呼叫這支。exec.sh 以 `exec` 把終端機交出去，永不返回，所以
# post-hook 只可能是一個靜默跳票的承諾。留著是為了與模板的目錄結構對稱，刻意
# 不動作。
