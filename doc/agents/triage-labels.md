# Triage 標籤

`/triage` skill 用五個 canonical 角色描述「一張 issue 現在卡在哪」。這份檔案把那些角色
對映到**這個 repo 實際使用的標籤字串**。

**標籤名稱維持英文、說明寫中文**，與 repo 其餘標籤一致，也與 ADR-00000028 的判準一致：
名稱是給工具讀的識別碼（`gh issue list --label ...` 要打進去），說明是給人讀的散文。

| skill 的角色 | 本 repo 的標籤 | 意思 |
|---|---|---|
| `needs-triage` | `backlog` | 已接受但尚未排程，等著被挑走 |
| `ready-for-agent` | `ready-to-agent` | 規格已完整，可以直接交給 agent 實作 |
| `wontfix` | `wontfix` | 不處理 |
| `needs-info` | **無對應** | 見下 |
| `ready-for-human` | **無對應** | 見下 |

## 兩個刻意沒有對應的角色

`needs-info`（等待回報者補資訊）與 `ready-for-human`（需要人做，不適合 agent）**在這個
repo 沒有對應標籤，這是刻意的**：目前 issue 全部由維護者自己開，沒有外部回報者可以等，
而「誰來做」是當下決定的，不需要一個長期存在的標籤來記。

**`/triage` 遇到這兩種狀態時不要自己造一個標籤**，而是在 issue 留言說明狀態，讓它維持
可見但不增加詞彙。哪天真的有外部回報者了，再把標籤加回來並更新這張表。

## 這與「種類」標籤是不同的軸

上面五個回答的是**「卡在哪」**。repo 另有一組回答**「這是什麼」**的標籤，兩者正交，
一張 issue 可以同時帶兩邊各一個：

| 標籤 | 意思 |
|---|---|
| `bug` | 已交付行為的缺陷 |
| `documentation` | 文件 |
| `enhancement` | 新功能或改進 |
| `backend` | API、業務邏輯、git 操作 |
| `frontend` | 單一 HTML 頁面與互動 |
| `acceptance` | 驗收檢查點：關閉此 issue 才算該版本通過 |
| `design-gate` | 動工前必須先確認的設計項目 |
