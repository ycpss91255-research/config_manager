# The CLI is an HTTP client of the same endpoints as the web interface

> 服務：設計原則 N-5（每個介面操作都有 CLI 對等）

- **Date:** 2026-09-02
- **Status:** Accepted

## Context

N-5 要求 CLI 與介面等價。達成方式有兩種，保證強度差很多。

## Decision

CLI 透過 HTTP 呼叫與介面完全相同的端點。核心邏輯是 port，HTTP 是唯一的 adapter
（Ports and Adapters）。

業界既有做法：Docker CLI 是 daemon API 的 client；kubectl 與 Dashboard 都是
apiserver 的 client；GitHub CLI 是其 API 的純 client。

## Alternatives

- **共用函式庫**：只保證編譯期共用。兩邊各自的參數處理、預設值、錯誤轉換
  仍會分歧，而且分歧不會被任何測試抓到。
- **CLI 開本機直呼旁路**（服務掛掉時可用）：重新引入分歧。且救援路徑已存在——
  來源 repo 就是普通 git repo，任何時候都能直接操作。

## Consequences

- CLI 需要 backend 服務在跑。Docker 亦如此，可接受。
- 自動產生的 API 文件即是 CLI 與介面共同的契約。
