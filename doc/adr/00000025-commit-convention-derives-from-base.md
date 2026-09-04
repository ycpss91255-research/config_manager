# Follow base's commit convention, with the lint deriving its rules from base rather than restating them

> 服務：不變式 2（絕不靜默失效）

- **Date:** 2026-09-04
- **Status:** Accepted
- **Relates to:** ADR-00000016（對齊模板 main）、ADR-00000017（ADR 結構 lint）、ADR-00000007（config-repo 的 commit 前綴）

## Context

本 repo 出現了三種 commit 訊息風格並存：協作者的 `T14：…`／`雜項：…`、
本次骨架工作的英文 Conventional Commits（無 scope）、以及組織共用模板
`ycpss91255-docker/base` 的形式。維護者決定**對齊 base**。

問題不在於決定本身，而在於「對齊 base」這句話沒有可檢查的內容。
base 沒有 CONTRIBUTING、沒有 commit lint、沒有任何一份文件寫下它的慣例——
它的慣例只存在於它的 commit 歷史裡。一個只靠自律維持的慣例，
正是 ADR-00000017 對 ADR 結構所描述的同一種處境：外觀一致，
但沒有東西擋下第一筆偏離的，而偏離不會有任何訊號。

**注意這與 ADR-00000007 是兩件事。** 那份規範的是 config-repo（資料 repo）的
commit——類型為 `import`／`cfg`／`revert`／`adopt`／`meta`／`unmanage`，範圍放 `uid`。
本 ADR 規範的是本 repo（程式碼 repo）自己的 commit。兩者不共用類型集合。

## Decision

**慣例的定義來源是 base 的實際 commit，不是任何人對它的複述。**

以 base 最近 200 筆 commit 取樣，得出的分布寫進 `script/lint_commit.sh` 的檔頭，
並據此設定規則：

| 面向 | base 的實際分布 | 規則 |
|---|---|---|
| type | fix 63／feat 38／refactor 31／docs 26／test 15／chore 14／ci 7／perf 6 | 八種以外 **fail** |
| scope | 181／200 有 | 缺少 **warn** |
| 主旨首字 | 188／200 小寫 | 大寫 **warn** |
| 標題長度 | 中位數 90、最長 153 | **不檢查** |
| issue 回指 | 118／200 有 | **不檢查** |

fail／warn 的分界沿用 ADR-00000017：**明確的擋，是訊號的印出來但不擋。**

**長度刻意不檢查。** 傳統的 50 字上限會擋掉 base 自己絕大多數的 commit。
base 的標題是**陳述句，說明現在什麼成立**（"base owns the orchestrator, the repo
owns its bringup"），不是祈使句列舉改動（"add orchestrator"）。加長度上限會安靜地
與這個風格對抗。

**只檢查 `origin/main..HEAD`，不檢查歷史。** 既有 commit 早於本規則，
重寫它們意味著對別人手上已有的分支強制推送。

## Alternatives

- **把慣例寫成 CONTRIBUTING.md。** 讀者要先想到去讀它，而想不到的人正是會寫錯的人。
  且它是 base 慣例的一份副本，base 改了它不會跟著改——不變式 6 的同一種漂移。
- **在 base 上游加 commit lint，本 repo 沿用。** 方向正確，但本 repo 不是 base 的
  downstream（ADR-00000016 決定不引入模板），沒有承接管道。日後導入時應把此 lint 上推。
- **維持現狀，靠 review 抓。** 已經失效過一次——三種風格並存正是它失效的結果。

## Consequences

- 取樣數字會過期。**這是刻意接受的成本**：規則寫在腳本檔頭並註明取樣基準，
  重新取樣是一個明確的動作，而不是一份沒人知道何時失準的文件。
  base 的慣例明顯改變時，重跑取樣並更新規則。
- 既有歷史不符合規則，且**永遠不會符合**。lint 的範圍限制使這不成為問題。
- 協作者不需要去讀 base 的歷史才知道規範——`just test lint commit`
  的錯誤訊息直接說明該怎麼改。
- 日後導入模板時，本 lint 應上推至 base，使定義與被定義者合一。
