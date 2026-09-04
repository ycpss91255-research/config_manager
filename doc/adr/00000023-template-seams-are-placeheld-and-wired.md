# 模板的擴充點佔位，並接到自建的 wrapper 上

> 服務：不變式 2（絕不靜默失效）

- **Date:** 2026-09-04
- **Status:** Accepted
- **Relates to:** ADR-00000016（對齊模板 main，釘住 commit）

## Context

ADR-00000016 決定初期不引入共用容器模板，只對齊形狀。模板定義了兩處擴充點：
`script/hooks/{pre,post}/<動作>.sh`（七個動作各一對）與 `script/local/`
（repo 自有的 just 命令組）。設計文件 §3.3.4 已經指名本專案要放什麼進去：
`pre/run.sh` 檢查 config-repo 掛載點在主機端存在、`post/build.sh` 建置後執行
smoke、`local/cfg/justfile.cfg` 是本專案的 CLI 命令組。

問題是這些目錄現在要不要建。模板的 dispatcher 隨模板而來，現在不存在。
**建了目錄卻沒有東西會去呼叫它，是不變式 2 要防的形態**：目錄看起來接好了，
放進去的腳本靜默不執行，而放的人不會收到任何訊號。這正是「CI 綠燈但契約已破」
的同一種失效。

## Decision

**佔位，並由自建的 wrapper 真的接線。**

- `script/hooks/dispatch.sh` 提供 `run_hook <phase> <verb>`，由五支自建 wrapper
  （build／run／exec／stop／prune）在工作前後呼叫。**hook 因此真的會執行。**
- 七個動作的 `pre`／`post` 全部建立為空腳本，各自說明契約。
- §3.3.4 指名的兩處內容**現在就搬進去**：`pre/run.sh` 的掛載點檢查、
  `post/build.sh` 的 runtime smoke。它們先前內嵌在 wrapper 裡。
- `script/local/justfile.local` 註冊 `cfg` 命令組，主 `justfile` 以 `import?` 掛上。
- **沒有呼叫者的擴充點必須在檔案裡寫明**：`setup`／`setup_tui` 的四支
  （其 wrapper 屬模板，附錄 A 列為現在不要做）與 `post/exec.sh`
  （`exec.sh` 以 `exec` 交出終端機，永不返回，post-hook 只能是一個靜默跳票的承諾）。

判準是：**擴充點要嘛真的會被呼叫，要嘛在檔案裡說明它不會。** 兩者皆非的目錄不建。

## Alternatives

- **等模板導入時才建。** 先前的做法。缺點是 §3.3.4 指名的邏輯無處可放，只能內嵌在
  wrapper 裡，導入時要一支一支挖出來——而挖漏一支不會有任何訊號。
- **建目錄但不接線。** 成本最低，也最危險：與「已接線」在外觀上完全無法區分。
  被否決的正是這個選項。
- **自建一個完整的 dispatcher。** 會長成競爭版本的模板，違反 ADR-00000016 的
  「只能修 bug，不能長功能」。因此 `dispatch.sh` 刻意只有一個函式、七行。

## Consequences

- 導入模板時，`dispatch.sh` 被模板的版本取代，`hooks/` 底下的內容原封不動繼續運作。
  遷移面縮小到一個檔案。
- 五支 wrapper 各自多兩行 `run_hook`。這是接線的全部成本。
- `setup`／`setup_tui` 的四支 hook 是刻意的空殼。它們的 NOTE 段落是本 ADR 的執行機制——
  未來讀者看到空檔案時，答案在檔案裡，不必回頭翻 ADR。
- 新增第八個動作時，wrapper 與 hook 對必須一起加。這個耦合是刻意的：
  少了任何一半，另一半就是靜默失效。
