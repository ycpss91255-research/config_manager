#!/usr/bin/env bash
# pre-build hook：在 `just docker build` 之前執行。
#
# 刻意留空。這是共用容器模板定義的擴充點；現在先佔位並接好線，之後要加一個
# 步驟時只改這個檔案，不必動 wrapper。pre-hook 失敗會中止整個操作。
set -euo pipefail
