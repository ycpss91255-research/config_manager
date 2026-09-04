# 紀錄一律以中文書寫，只有機器讀的 type 與 scope 維持英文

> 服務：不變式 8（身分與命名一次解析，來自檔案）

- **Date:** 2026-09-04
- **Status:** Accepted
- **Relates to:** ADR-00000025（commit 慣例取樣自 base）、ADR-00000016（對齊模板 main）

## Context

本 repo 的紀錄語言長成了混的：issue、milestone、README、`doc/test/TEST.md`、
ADR 內文用中文；commit 訊息、PR 標題、CLAUDE.md、程式碼註解、ADR 標題用英文。
沒有任何一條規則說明分界在哪，所以每個人每次都要重新判斷，而判斷結果不一致。

成因可追溯：ADR-00000025 決定 commit 慣例「取樣自 base 的實際 commit」，
而 base 的 commit 是英文的——於是語言跟著格式一起被抄了過來，**但那不是同一件事**。
base 用英文，是因為它的讀者是取用該模板的任何人；本 repo 不是 base 的 downstream
（ADR-00000016 明確決定不引入模板），它的讀者就是這裡的維護者。

## Decision

**紀錄一律以中文書寫。** 唯一的例外是 `type` 與 `scope`——那兩個是給工具讀的識別碼，
維持 base 定義的形式。

```
feat(core): 清單檔載入時攔截未知欄位
```

適用範圍：commit 訊息、PR 標題與內文、issue、milestone、ADR（含標題）、
專案文件、CLAUDE.md、程式碼註解。

`script/lint_commit.sh` 檢查主旨含中文字，**fail**——依 §0.6，無法自動檢查的規範等於不存在。
既有歷史不在檢查範圍內（只看 `origin/main..HEAD`），重寫它意味著對別人手上的分支強制推送。

ADR-00000025 的其餘部分不受影響：type 白名單、scope、格式、長度不設限、
規則取樣自 base——那些都是**格式**決策，與語言正交。本 ADR 只推翻其中隱含的語言假設。

## Alternatives

- **維持混用，並寫下分界。** 例如「對齊 base 的用英文、給人讀的用中文」。
  問題是 commit 訊息同時是兩者：它既要與 base 的慣例可比對（格式），
  也是給這裡的人讀的（內容）。把它整條劃到英文那邊，是讓格式的理由決定了內容的語言。
- **全部改英文。** 與現況距離更遠（issue、milestone、README、ADR 內文都要翻），
  且沒有對應的收益——沒有英文讀者。
- **不設規則，各憑判斷。** 這正是先前的狀態，結果是同一份 repo 裡兩種語言交錯，
  而且無法檢查。

## Consequences

- 既有的英文紀錄不回頭改寫 commit 歷史。**活的文件**（CLAUDE.md、程式碼註解、
  ADR 標題）逐步轉換，各自獨立的變更，不與功能改動混在同一個 diff 裡。
- 與 base 的 commit 並排時，只有 `type(scope)` 對得上。這是刻意接受的：
  本 repo 不是它的 downstream，可比對性的價值僅止於格式。
- 若日後真的引入模板並成為 downstream，本決策應重新檢視——屆時 repo 會有
  上游讀者，語言的權衡就不同了。
