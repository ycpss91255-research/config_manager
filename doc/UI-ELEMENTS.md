# UI 元素對照

**Wireframe 是 HTML 的規格，不是插圖。** 本文件把三者綁在一起：

```
figures/w*.svg   →   HTML 元素   →   測試選取器
（長什麼樣）          （怎麼寫）        （怎麼測）
```

三者必須一致。改動任一項就要同步另外兩項——**wireframe 改了但 HTML 沒改，
或 HTML 改了但測試選取器沒改，都會讓「有測試」變成假象**。

---

## 選取器規則

依 ADR-00000019：**以語意屬性選取，不以 CSS class 或 DOM 路徑。**

優先序：

1. **可見標籤文字** — 按鈕、連結
2. **`data-testid`** — 沒有穩定可見文字的容器與清單項
3. **可及性角色 + 名稱** — 表單控制項

**絕不使用**：CSS class、`nth-child`、DOM 路徑。

`data-testid` 命名：`<區域>-<元素>`，kebab-case。動態項目加識別碼後綴，
例如 `tree-item-mfz3k9q1`。

---

## W1 身分輸入

| 元素 | 選取器 | 行為 |
|---|---|---|
| 姓名輸入 | 標籤「姓名」 | 必填 |
| Email 輸入 | 標籤「Email」 | 必填 |
| 角色切換 | `data-testid="role-toggle"` | 兩個並排的選項按鈕：一般使用者 ／ 🔧 開發者。**明顯可見，不藏在選單裡** |
| 目前選中的角色 | `role-toggle` 內 `aria-pressed="true"` | 測試以此斷言，不以 CSS class |
| 記住此裝置 | 標籤「記住此裝置」 | **僅開發環境出現**。不記住角色 |
| 進入 | 文字「進入」 | — |

---

## W2 主畫面

### 工具列

| 元素 | 選取器 | 行為 |
|---|---|---|
| 搜尋範圍 | `data-testid="search-scope"` | 全部（預設）／config 名稱／目標路徑／參數名稱／參數值；選項值即 `GET /api/search` 的 `scope`（#37） |
| 搜尋框 | `data-testid="search-input"` | 即時過濾（150ms 去抖後走 `GET /api/search`，命中的 config 留在樹上）；無結果→`no-matches`；搜尋失敗→`search-error`（不靜默顯示成沒有結果）。展開命中的 config 時，命中的參數列帶 `data-hit="true"` 並捲進視野（#37） |
| 檢查差異 | 文字「檢查差異」（`data-testid="rescan"`） | 觸發全項目掃描；檢查期間停用 |
| 檢查結果 | `data-testid="scan-status"` | 手動「檢查差異」的回饋，`data-state` 為 `running`（檢查中…）／`done`（時間、共幾份、全部一致或各有幾份偏離／未部署／判不出狀態）／`failed`（讀不到清單）。開頁面時的自動載入不顯示 |
| 白名單 | 文字「白名單」 | **僅開發者出現**（非停用） |
| 納管 | 文字「納管」 | 開啟納管流程 |
| 進版 | `data-testid="promote-all"` | 文字含待進版草稿數，如「進版 (2)」。無草稿時停用。`POST /api/promote`；成功→`promote-done` 橫幅、草稿清空、重掃；失敗→`promote-error` 橫幅原樣列結構化錯誤（哪一份／哪些行號與建議）並打開那份（#22） |
| 捨棄變更（全域） | `data-testid="discard-all"` | 文字「捨棄變更」；無草稿時停用；經 W6 確認對話框後 `DELETE /api/drafts`（#22） |
| 進版結果橫幅 | `data-testid="promote-done"` ／ `"promote-error"` | 位於工作區上方、橫跨兩欄；下一次進版／捨棄前清掉 |
| 退出 | 文字「退出」 | 有未進版草稿時二次確認 |
| 目前角色 | `data-testid="current-role"` | 恆常可見於標題列：`姓名・角色`；唯讀時顯示「唯讀」，**取回編輯階段後改回身分**（重新整理會先釋放再取回） |

### 左側樹

| 元素 | 選取器 | 行為 |
|---|---|---|
| 樹根 | `data-testid="config-tree"` | 階層來自 `groups`，**不是機器** |
| 排列 | `data-testid="tree-layout"` | 下拉：依群組（預設）／依主機。依主機時第一層是 `tree-host-<hostname>`（標題帶彙總色點）、第二層群組節點（#34；單機部署只有一個主機節點） |
| 群組節點 | `data-testid="tree-group-<群組名>"` | 可折疊；標題內 `group-status-dot`（帶 `data-state`）彙總子節點最嚴重的狀態：判不出 > 偏離 > 未部署 > 一致（§7.4.1，#31） |
| config 節點 | `data-testid="tree-item-<uid>"`（帶 `data-uid`） | 單擊選取（`aria-selected="true"`）、**雙擊展開參數**到右側工作區堆疊（#35） |
| 狀態色點 | 節點內 `data-testid="status-dot"` | **左側**。一致／偏離／未部署 |
| 草稿標記 | 節點內 `data-testid="draft-dot"` | **右側**。有未進版草稿時出現。與狀態色點分開 |
| 未分群節點 | `data-testid="tree-group-ungrouped"` | 無 `groups` 的項目集中於此 |

### 右側工作區

| 元素 | 選取器 | 行為 |
|---|---|---|
| 工作區 | `data-testid="workspace"` | 右側容器；尚未點選時顯示 `workspace-empty` 提示 |
| 展開區塊 | `data-testid="panel-<uid>"` | **可同時存在多個**（#36），新開的在最上面；工作區每份 config 一個槽（`data-uid`／`data-view`＝panel／history／diff），歷史與差異檢視只換自己那一槽。標頭（`panel-head`）單擊折疊／展開（`data-collapsed`），`panel-close` 關閉。讀不到內容時顯示 `panel-error-<uid>`（原樣錯誤，不留空表） |
| 狀態標籤 | `data-testid="panel-status-<uid>"` | 文字為一致／偏離／未部署 |
| 草稿指示 | `data-testid="panel-draft-<uid>"` | 該區塊有草稿時出現（`GET /api/configs/{uid}` 回 `draft_values`） |
| 儲存按鈕 | 文字「儲存」（`data-testid="panel-save"`） | **存為草稿**（`POST /api/drafts`，送相對來源的全部改動），不記錄也不寫出。驗證未過或沒有改動時停用 |
| 儲存錯誤 | `data-testid="panel-save-error"` | 驗證沒過的 422（第 1 層；有 schema 時含第 2 層，#39）逐條列行號／原因／建議——給不出行號的不印行號，訊息已指名欄位；其他錯誤原樣顯示 |
| 規則警告區 | `data-testid="rule-warnings"`（`.notice.warning`） | 儲存時後端回 409 `override_required`、或重開一份草稿有規則警告的 config 時出現。每條規則一列（`rule-warning-<規則>`）：原因、建議、略過的理由欄 `override-reason-<規則>`；「填理由後儲存」`save-with-overrides` 帶理由重送 `POST /api/drafts`；沒填的規則→`override-error`、草稿不存；帶理由存成後標頭改為「已儲存，已略過 N 條」、理由欄可再改（#44／#45） |
| 捨棄變更（單一） | `data-testid="panel-discard"` | 文字「捨棄變更」；**只在該份有草稿時出現**；經 W6 確認後 `DELETE /api/drafts/{uid}`，面板重讀為來源內容（#22） |
| 退版按鈕 | 文字「退版」 | 針對單一 config，與草稿無關 |
| 歷史按鈕 | 文字「歷史」（`data-testid="panel-history"`） | 右側工作區切成該 config 的歷史檢視（W4），左側樹不動（#25） |
| 屬性按鈕 | 文字「屬性」（`data-testid="panel-attributes"`） | **僅開發者出現**（一般使用者不在 DOM）；在區塊標頭（`raw` 也有）、唯讀時不出現。按一下展開屬性面板，再按一下收起（#286） |
| 產生 schema 按鈕 | 文字「產生 schema」（`data-testid="panel-schema-draft"`） | **僅開發者出現**，且只在這份還沒有 schema 時；經 W6 確認後 `POST /api/configs/{uid}/schema`，成功後就地換成「有 schema」標示、不重畫欄位表（#38） |
| 有 schema 標示 | `data-testid="panel-schema-<uid>"` | 這份 config 有 schema 時出現在標頭（`GET /api/configs/{uid}` 回 `schema`） |
| schema 錯誤橫幅 | `data-testid="panel-schema-error"` | schema 讀不出來時出現在標頭下：明說修好之前存不了、附後端的原因（指名檔案）。值照樣顯示（#40） |
| 未驗證標示 | `data-testid="panel-unvalidated-<uid>"` | 文字「未驗證」；**僅 `raw`**——唯一不受把關的格式（§3.4） |
| schema 已產生通知 | `data-testid="schema-drafted-notice"` | 產生成功後出現在標頭下，寫出 schema 的路徑 |
| 解除納管按鈕 | 文字「解除納管」（`data-testid="panel-unmanage"`） | 在區塊標頭（`raw` 也有）；唯讀時不出現。經 W6 確認（寫明目標檔案保留、歷史仍在）後 `DELETE /api/configs/{uid}`；成功後這份從樹與工作區消失、`promote-done` 寫出目標路徑；後端拒絕（有草稿、寫入失敗）時原因顯示在 `promote-error`（#287） |

### 屬性面板（僅開發者）

| 元素 | 選取器 |
|---|---|
| 名稱 / 群組 / 主機 / 說明 | 標籤文字 |
| 儲存屬性 | `data-testid="attributes-save"` |
| 取消 | `data-testid="attributes-cancel"` |
| 屬性錯誤 | `data-testid="attributes-error"` |
| 已更新通知 | `data-testid="attributes-saved-notice"` |
| 面板容器 | `data-testid="attributes-<uid>"` |

群組以逗號（`,`／`，`／`、`）分隔，留空＝未分群。儲存走 `POST /api/configs/{uid}/attributes`：成功後樹依新的群組／主機／名稱重建、這一塊重畫並顯示已更新通知；後端指名不合法的那一項時，原因顯示在屬性錯誤、對應的輸入框標 `aria-invalid="true"`；這一塊有未儲存的參數改動時先擋下不重畫。樹節點（`tree-item-<uid>`）有說明時以 `title` 顯示（滑鼠停留）。

---

## W3 參數表

資料來自 `GET /api/configs/{uid}` 的 `types`＋`values`（#20）；前端只渲染，不推斷型別。

| 元素 | 選取器 | 行為 |
|---|---|---|
| 欄位表 | `data-testid="param-table"` | 一列一個參數；`raw`／頂層非物件時不出現，改顯示 `panel-unstructured`（§7.5.4） |
| 參數列 | `data-testid="param-<參數路徑>"` | 路徑文法與 `set_value` 一致：點號串接、key 內字面點跳脫成 `\.`、list 元素 `[i]`。帶 `data-name`／`data-type`（API 型別名）／`data-depth`；容器（物件／list）帶 `data-container="true"`，單擊折疊其子列；改值後 `data-changed`、驗證結果 `data-valid` |
| 清單元素的增刪調序 | 元素列內 `data-testid="list-up"`／`"list-down"`／`"list-remove"`，清單列內 `"list-add"` | #46：↑↓ 交換順序（第一項不能上移、最後一項不能下移）、✕ 移除、＋ 在末端新增（依元素型別給空值；元素是物件時以最後一項為樣板）。改在清單的工作副本上、整段重畫；儲存時整個清單一起送。清單列的 `data-dirty`／`data-changed` 說整個清單有沒有改。都是 `.editing-only`，唯讀不出現 |
| 未結構化編輯 | 列內 `data-testid="param-unstructured"` | 型別未知（null、空清單的元素）的欄位退回文字輸入，列上標「未結構化編輯」，`title` 提醒補 schema（§7.5.4） |
| 型別欄 | 列內 `data-testid="param-type"` | **開發者對單一參數是下拉選單（int／double／bool／string），一般使用者是純文字**（#285）。選了即 `POST /api/configs/{uid}/types` 指定型別，成功後重畫這一塊並顯示 `type-specified-notice`；失敗（與現值不相容等）原因顯示在 `panel-save-error`、選單回到原本的型別；這一塊有未儲存的改動時先擋下不重畫。容器、清單元素、型別未知的列一律純文字。唯讀時選單不出現，改顯示 `param-type-text`。顯示名依設計 §7.5.1（`double`／`object`），列的 `data-type` 留 API 名（`float`／`dict`）；有 `enum` 的欄位純文字顯示 enum |
| 已指定標記 | 列內 `data-testid="type-overridden"` | 型別經人工指定時出現（兩種角色都看得到；依 `GET /api/configs/{uid}` 的 `manual_types`） |
| 清除指定 | 文字「清除」（`data-testid="type-clear"`） | **逐欄位**，不是全部重設；僅開發者出現、唯讀時不出現。`POST /api/configs/{uid}/types` 帶 `type: null`，回到指定之前 |
| 值欄 | 列內 `data-testid="param-value"` | 控制項依型別。**有 schema 時（#40）**：數字輸入框帶 `min`／`max`／`step`；有 `enum` 的欄位是下拉選單（選項來自 schema；目前的值不在選項裡時多一個標明「不在允許的值裡」的選項，`data-outside`） |
| 欄位說明 | 列內 `data-testid="param-description"` | schema 有 `description` 時出現在參數名旁（ⓘ），`title` 是說明全文（#40） |
| 來源值 | 列內 `data-testid="param-source-value"` | 與目前值不同時標色 |
| 驗證狀態 | 列內 `data-testid="param-validation"` | 錯誤時含原因文字（列的 `data-valid="false"`、整列標紅）。輸入當下由前端算（整數、範圍、倍數、不在列舉裡）；**後端擋下的問題若指名欄位，也標在那一列並附修正建議**，那一列再改動即清掉（#40） |
| 警告列 | 列的 `data-warning="true"` | 違反第 3 層規則的那一列（琥珀色，與紅色的 `data-valid="false"` 分得開；硬擋優先）。值一改即清除，由下一次儲存重標（#44） |
| 儲存 | 文字「儲存」 | 存為草稿。**驗證未過時為停用** |

### 值控制項依型別

| 型別 | HTML | 測試斷言 |
|---|---|---|
| `bool` | `<input type="checkbox">` | 勾選狀態 |
| `int` | `<input type="number" step="1">` | 輸入小數被拒 |
| `double` | `<input type="number" step="any">` | **輸出必帶小數點** |
| `enum` | `<select>` | 選項集合來自 schema（`options`）。**渲染器已支援、後端於 #40 之前不送 options——刻意留空，見 TEST-PLAN T11** |
| `string` | `<input type="text">` | — |
| `list` | 每元素一列 + 新增／移除／**↑↓** | 順序調整後儲存生效（#46；**#20 先只列元素、唯讀**） |
| 物件 | 可折疊區塊，內部遞迴 | — |

### list 的操作

| 元素 | 選取器 |
|---|---|
| 元素列 | `data-testid="list-item-<參數路徑>-<索引>"` |
| 上移 / 下移 | 列內 `data-testid="list-move-up"` / `"list-move-down"` |
| 移除 | 列內文字「移除」 |
| 新增 | 文字「新增」 |

---

## W4 歷史

從 W2 面板的「歷史」進入，右側工作區切成歷史檢視（#25 的決策：不另開整頁）。資料：`GET /api/configs/{uid}/history`
（列表）與 `GET /api/configs/{uid}/history/{sha}`（選定那一版的值樹，#26）；「目前版本」＝來源複本現況。

| 元素 | 選取器 | 行為 |
|---|---|---|
| 歷史檢視 | `data-testid="history-<uid>"` | 容器；標頭有名稱、目標路徑與「返回欄位表」（`history-back`） |
| 篩選 | `data-testid="history-filter"` | 兩個並排選項按鈕（`aria-pressed`）：「只看內容變更」（預設，`cfg`＋`adopt`）／「全部」（六種都列） |
| 列表 | `data-testid="history-list"` | 最新在前；空時顯示 `history-empty`、讀不到顯示 `history-error` |
| 變更列 | `data-testid="history-entry-<sha>"` | 顯示**行為描述**（內部類型→介面顯示對照表 §7.6.1），不顯示內部代號；退回舊版本附「退回到版本 <sha7>」；點選→`aria-selected="true"` 並載入差異 |
| 作者／時間 | 列內 `data-testid="history-author"`／`"history-time"` | 姓名（email 在 title）；`<time datetime=ISO>` 顯示本地時間 |
| 略過的規則 | 列內 `data-testid="history-overrides"` | 這一筆略過了哪些規則與理由（`略過規則 <規則>：<理由>`），來自 `GET …/history` 的 `overrides`；沒有就不出現（#45） |
| 差異區 | `data-testid="history-diff"` | 以參數為單位：`history-diff-summary`（N 個參數不同）＋每參數一列 |
| 差異列 | `data-testid="diff-row-<參數路徑>"` | 帶 `data-change`（`same`／`changed`／`added`／`removed`）；列內 `diff-from`（那一版）→`diff-to`（目前）。顏色語言與 W3／W5 一致（改動＝偏離紅） |
| 退回此版本 | 文字「退回此版本」（`data-testid="history-revert"`） | 選定一版、看過差異後才出現在差異區下方；與目前相同（0 個參數不同）時停用。點擊→W6 確認對話框（標題「退回此版本？」、後果：幾個參數會改變、產生新紀錄並寫出、歷史不改寫；**退版會動到 schema 時另寫明**——回到那一版當時的 schema，或那一版還沒有 schema 所以拿掉，#39）→ `POST /api/configs/{uid}/revert`；成功→重開歷史並顯示 `history-notice`（已退回到版本 X）、左側樹重掃；被擋（有草稿 409 等）→ `history-revert-error` 原樣顯示原因與下一步（#27） |
| 通知 | `data-testid="history-notice"` | 退版成功等訊息；空時隱藏 |

**測試須斷言**：列表中**不出現** `cfg`、`revert`、`import` 等字串。

---

## W5 差異檢視

只在偏離時出現（#30 的決策：不取代欄位表）。偏離的 config 其欄位表上方出現偏離橫幅＋「檢視差異」
（`panel-diff`）→ 右側工作區切成差異檢視（`diff-<uid>`），有「返回欄位表」（`diff-back`）。資料：
`GET /api/configs/{uid}` 的 `values`（來源）與 `target_values`（磁碟現況；壞掉時 `target_error`）。

| 元素 | 選取器 | 行為 |
|---|---|---|
| 偏離橫幅 | `data-testid="drift-banner"` | 說明此修改未經介面進行、沒有對應的變更紀錄與作者資訊；欄位表與差異檢視都有 |
| 檢視差異 | `data-testid="panel-diff"` | 欄位表橫幅內；開差異檢視 |
| 差異檢視 | `data-testid="diff-<uid>"` | 容器；`diff-summary` 說幾個參數不同；目標現況壞掉時顯示 `diff-target-error` |
| 來源側 / 目標側 | `data-testid="diff-source"` / `"diff-target"` | 欄首：左 repo（唯一真實來源）、右 target（磁碟現況）——並排而非合併，使用者要判斷哪一邊是對的 |
| 差異列 | `data-testid="diff-row-<參數路徑>"` | 每參數一列，帶 `data-change`（`same`／`changed`／`added`〔目標多出〕／`removed`〔目標缺少〕）；列內 `diff-source-value`／`diff-target-value`。與 W4 同一套比對與顏色語言 |
| 處置區 | `.resolve-actions`（差異檢視底部） | 三個出口並排，每個按鈕下方一行**後果說明**（A3：按下之前就說清楚）；失敗原樣顯示於 `resolve-error` |
| 以來源覆蓋 | 文字「以來源覆蓋目標」（`resolve-overwrite`） | W6 二次確認 → `resolve {action: overwrite}`；成功→`promote-done` 橫幅、重掃、回欄位表 |
| 納入來源 | 文字「將目標現況納入來源」（`resolve-adopt`） | 走完整驗證，不另確認；**含非法值則被拒**（`resolve-error` 列行號／原因／建議並指去先納入待修正）；成功→`promote-done`、重掃、回欄位表 |
| 先納入、待修正 | 文字「先納入、待修正」（`resolve-adopt-draft`） | W6 二次確認 → 目標現況載入草稿；回欄位表並在上方顯示 `adopt-draft-notice`（**含非法值則列出警告、進版前須改正**，見 TEST-PLAN T18／A3） |

---

## W6 唯讀、逾時與確認對話框

| 元素 | 選取器 | 行為 |
|---|---|---|
| 唯讀橫幅 | `data-testid="readonly-banner"` | 非持有分頁顯示；含持有者姓名、email、開始時間；階段失效（續期 410）時也用它說明。唯讀時 `body[data-readonly="true"]`，所有會寫入的控制項（`.editing-only`：納管、白名單、捨棄變更、進版、面板儲存／捨棄、退回此版本、處置三鍵）**不出現於 DOM 流程**（不是停用）；工具列 `current-role` 顯示「唯讀」（#33） |
| 逾時退出提示 | `data-testid="session-timeout"` | 部署模式閒置逾時後出現，階段已釋放 |
| 確認對話框 | `data-testid="confirm-dialog"` | 退出丟草稿／**捨棄變更**／以來源覆蓋／退回此版本／先納入待修正共用；`<dialog>`，開啟時帶 `open`；標題 `confirm-title`、後果 `confirm-body` |
| 對話框確認鈕 | 對話框內文字「確認」（`confirm-ok`） | 執行該動作 |
| 對話框取消鈕 | 對話框內文字「取消」（`confirm-cancel`） | 關閉、不執行（Esc 同） |

---

## W7 檔案瀏覽

受白名單限制的檔案系統瀏覽，供納管時選定路徑（#13、設計 §5.1／§7.9／圖5）。從 W2 工具列
的「納管」進入，是獨立整頁 view（與清單、身分輸入平行，靠 `hidden` 切換）。詞用 CONTEXT.md
的「檔案瀏覽」，不另造同義詞。

| 元素 | 選取器 | 行為 |
|---|---|---|
| 進入瀏覽 | W2 工具列文字「納管」 | 開啟檔案瀏覽 view（#14 之後接上完整納管流程） |
| 瀏覽 view | `data-testid="browse"` | 整頁容器；開啟時隱藏清單、顯示自己 |
| 返回 | 文字「返回」 | 關閉瀏覽、回到清單 |
| 提示 | `data-testid="browse-hint"` | 承載可見提示與非同步載入錯誤（「挑一個允許瀏覽的根開始…」「白名單目前是空的。」「讀不到白名單：…」）；空時隱藏 |
| 起點根清單 | `data-testid="browse-roots"` | 剛開啟時列出白名單根（`GET /api/allowed-roots`）供挑選起點 |
| 根項目 | `data-testid="browse-root-<前綴>"` | 單擊從該根開始瀏覽 |
| 手動路徑輸入 | `data-testid="browse-path-input"` | 開發者可貼／打一個絕對路徑當瀏覽目標——**這是走到白名單外、觸發加入白名單的途徑** |
| 前往 | 文字「前往」 | 以輸入欄的路徑瀏覽 |
| 麵包屑 | `data-testid="breadcrumb"` | 目前路徑（用回應的 `path`，即 realpath）；各段可點回上層，**上溯夾在白名單根為界** |
| 麵包屑段 | 段內文字為該層名稱 | 單擊跳到該層 |
| 目錄清單 | `data-testid="browse-list"` | 目前目錄的內容，依名字排序（後端已排） |
| 項目 | `data-testid="browse-entry-<名字>"` | 帶 `data-kind="dir"`／`"file"`；`dir` 單擊往下鑽（用該項回應的 `path`），`file` 單擊選取 |
| 已選檔案 | `data-testid="browse-selection"` | 選取一個 `file` 後顯示其路徑（供 #14 納管；#13 只到選取） |
| 拒絕通知 | `data-testid="browse-rejected"` | 瀏覽被拒時出現，含 `data-kind`（`outside_roots`／`not_a_directory`／`unreadable`）與**原樣**的 `message`（含「下一步」，不改寫） |
| 加入白名單 | 文字「加入白名單」 | **僅開發者、且僅 `outside_roots`**；在一般使用者模式或其他拒絕原因下**不存在於 DOM**（ADR-00000020） |
| 白名單前綴輸入 | `data-testid="whitelist-prefix-input"` | 「加入白名單」預填被拒目標的目錄 realpath（被拒目標是檔案則取父目錄），可編輯；按「加入白名單」先經 W6 確認框（寫明即將開放的目錄與底下有幾個可納管檔，同白名單維護頁），確認才 `POST /api/allowed-roots`，成功後重跑被拒的瀏覽 |
| 允許範圍 | `data-testid="allowed-range"` | **一般使用者**在 `outside_roots` 拒絕下看到的唯讀白名單清單（理解為何被擋）；文案沿用端點 403 說法「請開發者代為加入」，不自造 |

**測試須斷言**：一般使用者模式下「加入白名單」入口**找不到**（不是 disabled）；`not_a_directory`／
`unreadable` 拒絕下即使開發者也**沒有**「加入白名單」入口（加白名單無濟於事，見 TEST-PLAN T11）。

**下鑽與麵包屑用回應的 `path`（realpath），不用使用者點的原字串**——兩者可能不同（symlink／相對）。
往下鑽用被點項目回應裡的 `path` 欄位，不在前端自行拼路徑（拼錯會與後端白名單判定不一致）。

**範圍**：W7 只涵蓋瀏覽＋選檔＋`outside_roots` 的內嵌加入入口。完整白名單管理面板（檢視誰／
何時、新增含候選檔案數預覽、移除含受影響確認）是 **#15**，另立 W 段。

---

## W8 納管確認

從 W7 選檔後進入：偵測（`POST /api/inspect`）→ 顯示格式／型別／歧義／權限／hostname 供確認 →
確認後寫入（`POST /api/configs`）（#14、設計 §5.1／圖5 納管流程）。是唯讀的「確認偵測結果」面，
與 W3（可編輯的參數表）不同——**人工指定型別是 #16／W3，不在這裡**。納管無角色門檻，一般
使用者與開發者都能納管，故 W8 無角色差異元素。

| 元素 | 選取器 | 行為 |
|---|---|---|
| 進入確認 | W7 選檔後文字「檢視並納管」（`data-testid="onboard-start"`） | 對選定的檔 `POST /api/inspect`，切到確認 view |
| 確認 view | `data-testid="onboard-confirm"` | 整頁容器；開啟時隱藏瀏覽 |
| 來源路徑 | `data-testid="onboard-path"` | 承載選定的來源路徑（`source_path`），送給 inspect／configs |
| 格式選擇 | `data-testid="onboard-format"` | 下拉（yaml／json／toml／ini／raw）；預填副檔名的**建議**、使用者可改，改則重新偵測（不變式 8：副檔名不是持續權威） |
| hostname 預覽 | `data-testid="onboard-hostname"` | 「將以 hostname＝<X> 納管」——inspect 回應帶的、納管當下會寫進條目的機器身分（AC5 核對） |
| 權限 | `data-testid="onboard-permissions"` | 原始 owner:group mode |
| 摘要 | `data-testid="onboard-summary"` | 偵測到幾個欄位；`raw` 顯示「只版控、不解析」，不顯示「0 個欄位」這種假訊號 |
| 型別樹 | `data-testid="onboard-types"` | 可折疊樹：容器（dict／list）為可折疊節點、葉為葉；顯示每個值的解析型別供確認（AC3） |
| 型別節點 | `data-testid="onboard-type-<欄位路徑>"` | 帶 `data-type`（型別名）、`data-name`（欄位路徑）；容器帶 `data-container="true"`、單擊折疊/展開子節點 |
| 歧義清單 | `data-testid="onboard-ambiguities"` | `yaml` 才非空；每筆指名值、行號與可能讀法（AC4） |
| 歧義項 | `data-testid="onboard-ambiguity-<行號>"` | 顯示 value／line／readings |
| 歧義確認 | 項內 `data-testid="onboard-ambiguity-ack-<行號>"` | 勾選框；**全部歧義勾完前「確認納管」停用**（確認後才繼續，AC4） |
| 確認納管 | 文字「確認寫入」（`data-testid="onboard-submit"`） | 有未勾的歧義時停用；`POST /api/configs`（source_path／format／ambiguity_note＝逐條確認的彙整），成功回 W2 清單、新條目出現在左側樹 |
| 取消 | 文字「取消」 | 回到 W7 瀏覽（不寫入） |
| 錯誤 | `data-testid="onboard-error"` | inspect／onboard 的錯誤**原樣**顯示（inspect 的語法錯誤帶行號就地標示；容忍結構化 `{message,file,line}`、純字串、與 FastAPI 驗證 list 三形狀） |

**測試須斷言**：有歧義的 yaml → 「確認寫入」起始停用、逐條勾完才啟用（AC4）；json 的葉節點型別
以 `data-type` 呈現（AC3）；納管成功後新條目出現在 W2 左側樹（名字由目標路徑推導）。

**hostname 只顯示、不由前端送**：`POST /api/configs` 不收 hostname，後端納管當下自行決定並凍結
（不變式 8、ADR-00000012）；確認畫面顯示的值來自 inspect 回應，供人核對而已。

---

## W9 白名單維護

完整的白名單管理面板（#15、設計 §7.9、設計原則 5）：檢視目前允許的根（誰／何時）、新增前綴
（含 §7.9 的候選檔案數預覽）、移除前綴（含受影響納管項目的確認）。**整個面板開發者專屬**——
工具列「白名單」按鈕對一般使用者**不存在於 DOM**（角色表、ADR-00000020）；一般使用者的「檢視」
走 W7 的根清單與 `outside_roots` 拒絕下的允許範圍，不進本面板。獨立整頁 view，從 W2 工具列開啟。

消費既有後端：`GET`／`POST`／`DELETE /api/allowed-roots`（#202／#15）與 `GET /api/candidate-count`
（#206）。逃逸（`../`、symlink）由後端 `check_prefix`＋realpath 擋成 422，前端原樣呈現。

| 元素 | 選取器 | 行為 |
|---|---|---|
| 白名單按鈕 | `data-testid="open-whitelist"` | 文字「白名單」；**僅開發者存在於 DOM**（角色表）；開啟本面板 |
| 面板 view | `data-testid="whitelist"` | 整頁容器；開啟時隱藏其他 view |
| 返回 | `data-testid="whitelist-back"` | 回 W2 清單 |
| 新增前綴輸入 | `data-testid="whitelist-add-input"` | 要加入白名單的絕對路徑（與 W7 的 `whitelist-prefix-input` 不同元素） |
| 預覽 | `data-testid="whitelist-preview-button"` | 對輸入的前綴 `GET /api/candidate-count`；不每字打就數（避免每字遞迴走訪主機目錄，§7.9／#206） |
| 候選數預覽 | `data-testid="whitelist-preview"` | 「此路徑下有 N 個可納管檔」；觸上限顯示「N+」（`capped`）；前綴不合法（`..`／不存在／非目錄）顯示原樣錯誤訊息；空時隱藏；**輸入一改或按加入時清掉**，不留著舊路徑的數字 |
| 加入白名單 | `data-testid="whitelist-add"` | 先比對目前的白名單——已經在裡面的直接在 `whitelist-error` 說已存在、不開確認框；再數候選檔、經 W6 確認框（寫明即將開放的目錄含子目錄、底下有幾個可納管檔）；確認才 `POST /api/allowed-roots`（prefix 取自輸入），成功後重載清單、清空輸入；取消什麼都不變、輸入還在 |
| 新增錯誤 | `data-testid="whitelist-error"` | 新增失敗原樣顯示（`..`／symlink 逃逸 422、指向到不了的目錄、重複前綴 409）；空時隱藏 |
| 根清單 | `data-testid="whitelist-roots"` | 目前允許的根（`GET /api/allowed-roots` 的 `roots[]`）；空時顯示「白名單目前是空的」 |
| 根項目 | `data-testid="whitelist-root-<原樣前綴>"` | 顯示原樣 prefix、resolved（realpath）、`由 <added_by> 於 <added_at>`（誰／何時，AC2） |
| 移除 | 根項目內 `data-testid="whitelist-remove"` | 文字「移除」；先以 `confirmed=false` `DELETE`，回受影響清單＋要求確認（AC3） |
| 移除確認 | `data-testid="whitelist-remove-confirm"` | 說要移除哪一個前綴、移除後那個路徑不能再瀏覽或納管、已納管的不受影響，並列出受影響的納管項目與確認／取消；文字由介面自己寫，不出現 API 的參數名；未在確認流程時隱藏 |
| 受影響項目 | `data-testid="whitelist-affected"` | 受影響的納管項目（target 落在被移除前綴底下），資訊性、不連動解除納管（AC3） |
| 確認移除 | `data-testid="whitelist-confirm-remove"` | 以 `confirmed=true` `DELETE`，成功後重載清單 |
| 取消移除 | `data-testid="whitelist-cancel-remove"` | 收起確認、不移除 |

**測試須斷言**：一般使用者模式下「白名單」按鈕**找不到**（不是 disabled，角色表）；列根帶誰／何時
（AC2）；新增前先預覽候選數，`capped` 顯示「N+」（§7.9／#206）；`../`／symlink 前綴新增被擋、
原樣顯示 422（AC5）；移除有受影響項目時先列出＋要求確認、確認後才真移除（AC3）。

---

## 角色差異的測試方式

僅開發者可用的元素在一般使用者模式下**直接不存在於 DOM**，而非停用
（ADR-00000020）。因此測試斷言是「找不到該元素」，不是「該元素為 disabled」：

| 元素 | 一般使用者 | 開發者 |
|---|---|---|
| 白名單按鈕 | 不存在 | 存在 |
| 屬性按鈕 | 不存在 | 存在 |
| 產生 schema 按鈕 | 不存在 | 存在（還沒有 schema 時） |
| 型別欄 | 純文字 | 下拉選單 |
| 清除指定 | 不存在 | 存在 |
| 瀏覽拒絕的「加入白名單」入口（W7，`outside_roots`） | 不存在（改看唯讀允許範圍） | 存在 |

---

## 維護

Wireframe 由 `tools/gen_wireframes_*.py` 產生。**改介面的順序**：

1. 改腳本、重跑、確認 wireframe
2. 依本文件更新 HTML 與選取器
3. 更新對應的測試（`doc/TEST-PLAN.md` 的 T11 與 A 系列）

三者不同步時，**測試會通過但介面是錯的**——那是不變式 2 要防的形態。
