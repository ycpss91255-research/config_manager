# config 清單檔採 TOML，不用 YAML 或 JSON

> 服務：不變式 2（經設計原則 N-1）

- **Date:** 2026-09-02
- **Status:** Accepted

## Context

清單檔是系統的地基，其解析結果不應有任何不確定性。
使用者透過介面閱讀解析後的內容，不直接讀原始檔案。

## Decision

採用 TOML。

## Alternatives

- **YAML**：解析歧義最多（`no`/`08`/`1.10` 的結果依版本而異）、有縮排語意。
- **JSON**：型別明確但完全不支援註解，維護者無法留下區塊性說明。
- **TOML 的已知缺點**（深層巢狀笨拙）在本結構下不成立——清單檔只有兩層，
  權限以 inline table 撰寫即可。

## Consequences

- 需要 round-trip parser（`tomlkit`）以保留註解與欄位順序。
- 權限的 `mode` 必須加引號，避免被解析為整數。
- 被管理的 config 格式不受此決策影響——那由既有系統決定，系統必須全部支援。
