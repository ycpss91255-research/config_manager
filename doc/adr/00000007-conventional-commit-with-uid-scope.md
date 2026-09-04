# Change records use Conventional Commits with the uid as scope

> 服務：機制，無對應不變式

- **Date:** 2026-09-02
- **Status:** Accepted

## Context

退版以反向變更實作，歷史會累積退版紀錄；純看歷史分不出
「這份 config 真正被改過幾次」與「被退過幾次」。

## Decision

採用 `<類型>(<uid>): <說明>`。類型為 `import` / `cfg` / `revert` / `adopt` /
`meta` / `unmanage`。**範圍只放 uid**，不放可讀名稱。自 v0.1.0 起強制。

**介面上不顯示這些代號**，顯示對應的行為描述（納入管理／修改參數／退回舊版本等）。

## Alternatives

- **範圍放可讀名稱**：`name` 與 `hostname` 都可改，改名後歷史被切成兩段。
- **不加類型前綴**：歷史無法過濾，退版紀錄與內容變更混在一起。
- **事後補前綴**：需要重寫全部歷史，成本遠高於現在就做。

## Consequences

- 介面的歷史檢視可預設只顯示 `cfg` 與 `adopt`（實際改變內容者）。
- 可讀性由變更的檔案路徑提供，訊息裡不重複。
