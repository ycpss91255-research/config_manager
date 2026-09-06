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

# repo 擁有的東西由 repo 註冊，操作者擁有的由操作者註冊——兩者不共用同一個入口。
#
# `cfg` 是 repo 自己的命令組，`script/local/cfg/justfile.cfg` 與 `cfg.sh` 兩個檔案
# 都在版控裡，所以這裡用 **`mod`（沒有問號）**：檔案不見了就大聲失敗。先前它是靠
# `import? 'script/local/justfile.local'` 註冊的，而 `.gitignore` 的 `*.local` 讓
# 那個檔案**永遠不存在於乾淨簽出**——問號使缺席不報錯，於是「repo 送出了一組沒有人
# 叫得動的指令」安靜地成立（#108）。本 issue 原本傾向 `mod?`；改成 `mod`，因為問號
# 正是讓這件事無聲的那個東西，而 repo 擁有的檔案不見了不是一個合法狀態（不變式 2）。
mod cfg 'script/local/cfg/justfile.cfg'

# 操作者自有的追加。這一個維持 `import?`：它本來就可能不存在，而那是合法的。
import? 'script/local/justfile.local'

default:
    @just --list
