#!/usr/bin/env bash
# post-setup_tui hook：在 `just docker setup_tui` 之後執行。
#
# 刻意留空。這是共用容器模板定義的擴充點；現在先佔位並接好線，之後要加一個
# 步驟時只改這個檔案，不必動 wrapper。post-hook 失敗時工作已經做完了，但離開
# 狀態仍會傳出去，wrapper 以非零碼結束。
set -euo pipefail

# 注意：目前沒有任何東西呼叫這支。`setup` 與 `setup_tui` 是模板自己的 wrapper
# （config 解析 + 主機偵測），附錄 A 把它們列在「現在不要做」那一欄。這支 hook
# 先放著，是為了那個 wrapper 到來時擴充點已經存在；在那之前它是因為沒有呼叫者
# 而不動，不是不小心漏掉。
