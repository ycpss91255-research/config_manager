# ADR lint 檢查結構，不只檢查編號

> 服務：不變式 2（絕不靜默失敗）

- **Date:** 2026-09-02
- **Status:** Accepted
- **Relates to:** ADR-00000001

## Context

ADR-00000001 規定了 ADR 的結構。問題是——誰來確保它被遵守。

抽查組織共用容器模板的 ADR 目錄作為對照組：**27 份全部有 Serves 回指、全部有
Context / Decision / Consequences、25 份有 Alternatives**。一致性極高。

但檢視其 ADR lint 的實作（128 行）發現它只做三件事：檔名符合正規式、重號、跳號。
**完全不碰內容結構。** 掃過該專案全部 20 個檢查驅動，沒有任何一支驗證 ADR 的段落。

也就是說，那個 27/27 是**靠人的自律維持的**。

## Decision

ADR lint 同時檢查編號與結構：缺 `> 服務：`、缺 Context／Decision／Consequences、
Status 不在允許值內 → **fail**；缺 Alternatives → **warn**；
檔名不符或重號 → fail；跳號 → warn。

**於 v0.1.0 即納入**，與編號檢查同一支腳本，成本是多幾行 grep。

## Alternatives

- **維持只檢查編號，結構靠 review**：這正是對照組的現況。它今天有效，因為寫 ADR
  的人少且都記得規則。人一多、時間一長，第一份缺 `Serves` 的 ADR 會安靜地通過 CI。
- **Alternatives 也硬擋**：對照組有 2/27 缺此段落，硬擋會讓既有內容無法通過；
  且確實存在「只有一種做法」的決策。缺少它是訊號而非錯誤，因此 warn。
- **用專門的 markdown lint 工具**：需要另外設定與維護一套規則語法，而這裡要檢查的
  只是幾個固定字串是否存在。

## Consequences

- ADR 的結構契約從「靠自律」變成「被強制」，CI 綠燈與契約完整性一致。
- 若某份 ADR 確實沒有替代方案，應在 Alternatives 段落明寫「無其他可行做法，理由是……」
  而非略過——那本身就是資訊。
