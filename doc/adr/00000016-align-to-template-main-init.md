# Align the repo layout to the container template main branch, pinned to a commit

> 服務：不變式 6（單一真實來源）＋ 不變式 8（依據來自檔案而非推論）

- **Date:** 2026-09-02
- **Status:** Accepted

## Context

組織有一套共用容器模板。對齊的依據可以是 main 分支、最新 release、
或既有 downstream repo 的形狀，三者不同。

## Decision

以模板 **main 分支初始化腳本中的安裝路徑清單**為權威——它明文列出裝入 downstream
的每一個路徑，是最完整、最不需推論的定義。

**對應的紀錄必須釘住具體 commit 與日期**（撰寫時參照 2026-09-02 的 main）。

本專案初期**不引入模板**，僅對齊形狀；引入時機為功能完整之後。

## Alternatives

- **以最新 release 為準**：較穩定，但其初始化腳本沒有明文路徑清單，只能從
  符號連結呼叫反推。已比對兩者產生的佈局完全相同，故差異不構成風險。
- **參照既有 downstream**：抽查五個，僅一個跟上最新 release，其餘停在數個版本前——
  符號連結仍指向舊路徑、設定檔仍在舊位置。照抄會抄到已被取代的結構。

## Consequences

- 「main」不是可驗證的參照點，因此釘住 commit 是必要配套而非選項。
- 自建的最小版本（服務定義、任務入口、CI）**只能修 bug，不能長功能**——
  長出功能就變成競爭版本，引入模板時要拆掉重做。
