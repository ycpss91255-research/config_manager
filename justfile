# 任務進入點。自建（設計附錄 A.1）——共用模板的 justfile 在 v0.10.0 不引入——但
# **命令模型**現在就採用，因為等到指令已經長在別人的手指記憶裡才改名，屬於昂貴的
# 那種變更：
#
#   零例外——每個動作都住在一個命名空間裡，頂層不放東西
#   由寬到窄——`just test` 是最寬的執行，選項把它收窄
#   `lint` 不是 `test` 的同輩；它是 `just test lint`，測試的一部分
#
# `just` 不帶參數會列出全部。

mod docker 'script/justfile.docker'
mod test 'script/justfile.test'

# repo 自己的命令組，註冊在 script/local/justfile.local。用 `import?`，這樣註冊表
# 是空的不算錯誤。
import? 'script/local/justfile.local'

default:
    @just --list
