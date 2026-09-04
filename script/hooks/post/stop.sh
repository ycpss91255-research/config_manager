#!/usr/bin/env bash
# post-stop hook：在 `just docker stop` 之後執行。
#
# 刻意留空。這是共用容器模板定義的擴充點；現在先佔位並接好線，之後要加一個
# 步驟時只改這個檔案，不必動 wrapper。post-hook 失敗時工作已經做完了，但離開
# 狀態仍會傳出去，wrapper 以非零碼結束。
set -euo pipefail
