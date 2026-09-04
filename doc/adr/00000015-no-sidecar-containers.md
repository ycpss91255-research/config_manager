# The system ships two containers; no monitor or log sidecars

> 服務：不變式 1

- **Date:** 2026-09-02
- **Status:** Accepted

## Context

常見的「一個模組 = app + monitor + log 三容器」是 sidecar 模式，源自 Kubernetes：
Pod 是部署單位，同 Pod 內的容器共用網路與磁碟命名空間。

## Decision

維持 frontend + backend 兩個容器。不加 monitor 與 log 容器。

## Alternatives

- **每個應用配一組 sidecar**：(1) 容器把日誌寫到標準輸出，由容器執行環境的
  logging driver 收集——額外開 log 容器是重造已有的機制；(2) 指標收集應該是主機層
  共用一份，每個應用一組會讓容器數量隨應用數線性增長而做的是同一件事；
  (3) 純 compose 沒有 Pod 這層抽象，三個容器就是三個獨立容器，運維面積三倍而收益接近零。

## Consequences

- 日誌落地與存活監看由容器模板的執行期腳本處理，不需額外容器。
- 若未來出現無法寫標準輸出的第三方程式，屆時再以新 ADR 重新評估。
