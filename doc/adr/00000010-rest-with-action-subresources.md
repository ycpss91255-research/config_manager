# Resources over REST; state changes as action subresources

> 服務：機制，無對應不變式

- **Date:** 2026-09-02
- **Status:** Accepted

## Context

本系統多數操作是**動作**（寫出、退版、處置偏離）而非資源的增刪改查。
純 REST 表達不了動作，硬套會產生形式是 REST、語意是 RPC 的端點。

## Decision

資源查詢走標準 REST；狀態變更走 `POST /api/configs/{uid}/<動作>` 子資源。

## Alternatives

- **純 RPC**：誠實但失去 HTTP 慣例與工具鏈支援。
- **GraphQL**：本系統查詢型態固定（清單、單筆、歷史），彈性用不上。

採用子資源的依據是既有大型 API 的慣例：Kubernetes 的 API Conventions 明確規定，
當資源需要暴露與其緊密耦合的替代動作或視圖時，應以新的子資源表達；其自身即以
`/status`、`/scale`、`/exec`、`/log`、`/binding` 實作，其中 `/scale` 是虛擬資源，
不對應任何儲存物件，純粹作為動作介面存在。GitHub 以 `/pulls/{n}/merge`、
Stripe 以 `/payment_intents/{id}/capture` 表達同類動作。

## Consequences

- 端點語意誠實，接自動化的人零學習成本。
- 錯誤回應必須結構化（檔案、行號、欄位、修正建議），不可只回字串。
