#!/usr/bin/env bash
# hook 分派器，由 script/ 底下的 wrapper source 進去。
#
# 共用容器模板自己帶了一支分派器，引入模板時會把這個檔案換掉。在那之前，是這支
# 讓 script/hooks/ 成為真的擴充點，而不是一個看起來有接線的目錄：每支 wrapper 都
# 在工作前後呼叫 run_hook，所以丟一支 hook 進去就會被執行，不必去改呼叫它的
# wrapper。
#
# hook 不存在或沒有執行權限不算錯誤——多數 hook 本來就是空的。hook **失敗**才算：
# pre-hook 的存在就是為了擋下這個操作，所以它的離開狀態會傳出去，由 wrapper 的
# `set -e` 中止。

run_hook() {
  local phase="$1" verb="$2"
  shift 2
  local hook="${REPO_ROOT}/script/hooks/${phase}/${verb}.sh"
  [[ -x "${hook}" ]] || return 0
  "${hook}" "$@"
}
