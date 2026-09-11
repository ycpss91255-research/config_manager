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
| 搜尋範圍 | `data-testid="search-scope"` | 全部（預設）／config 名稱／目標路徑／參數名稱／參數值 |
| 搜尋框 | `data-testid="search-input"` | 即時過濾 |
| 檢查差異 | 文字「檢查差異」 | 觸發全項目掃描 |
| 白名單 | 文字「白名單」 | **僅開發者出現**（非停用） |
| 納管 | 文字「納管」 | 開啟納管流程 |
| 進版 | `data-testid="promote-all"` | 文字含待進版草稿數，如「進版 (2)」。無草稿時停用 |
| 退出 | 文字「退出」 | 有未進版草稿時二次確認 |
| 目前角色 | `data-testid="current-role"` | 恆常可見於標題列 |

### 左側樹

| 元素 | 選取器 | 行為 |
|---|---|---|
| 樹根 | `data-testid="config-tree"` | 階層來自 `groups`，**不是機器** |
| 群組節點 | `data-testid="tree-group-<群組名>"` | 可折疊 |
| config 節點 | `data-testid="tree-item-<uid>"` | 單擊選取、**雙擊展開參數** |
| 狀態色點 | 節點內 `data-testid="status-dot"` | **左側**。一致／偏離／未部署 |
| 草稿標記 | 節點內 `data-testid="draft-dot"` | **右側**。有未進版草稿時出現。與狀態色點分開 |
| 未分群節點 | `data-testid="tree-group-ungrouped"` | 無 `groups` 的項目集中於此 |

### 右側工作區

| 元素 | 選取器 | 行為 |
|---|---|---|
| 展開區塊 | `data-testid="panel-<uid>"` | **可同時存在多個** |
| 狀態標籤 | `data-testid="panel-status-<uid>"` | 文字為一致／偏離／未部署 |
| 草稿指示 | `data-testid="panel-draft-<uid>"` | 該區塊有草稿時出現 |
| 儲存按鈕 | 文字「儲存」 | **存為草稿**，不記錄也不寫出 |
| 退版按鈕 | 文字「退版」 | 針對單一 config，與草稿無關 |
| 歷史按鈕 | 文字「歷史」 | 開啟該 config 的變更紀錄 |
| 屬性按鈕 | 文字「屬性」 | **僅開發者出現** |

### 屬性面板（僅開發者）

| 元素 | 選取器 |
|---|---|
| 名稱 / 群組 / 主機 / 說明 | 標籤文字 |
| 面板容器 | `data-testid="attributes-<uid>"` |

---

## W3 參數表

| 元素 | 選取器 | 行為 |
|---|---|---|
| 參數列 | `data-testid="param-<參數路徑>"` | 路徑以點號串接 |
| 型別欄 | 列內 `data-testid="param-type"` | **開發者為下拉選單，一般使用者為純文字** |
| 已指定標記 | 列內 `data-testid="type-overridden"` | 型別經人工指定時出現 |
| 清除指定 | 文字「清除」 | **逐欄位**，不是全部重設 |
| 值欄 | 列內 `data-testid="param-value"` | 控制項依型別 |
| 來源值 | 列內 `data-testid="param-source-value"` | 與目前值不同時標色 |
| 驗證狀態 | 列內 `data-testid="param-validation"` | 錯誤時含原因文字 |
| 儲存 | 文字「儲存」 | 存為草稿。**驗證未過時為停用** |

### 值控制項依型別

| 型別 | HTML | 測試斷言 |
|---|---|---|
| `bool` | `<input type="checkbox">` | 勾選狀態 |
| `int` | `<input type="number" step="1">` | 輸入小數被拒 |
| `double` | `<input type="number" step="any">` | **輸出必帶小數點** |
| `enum` | `<select>` | 選項集合來自 schema |
| `string` | `<input type="text">` | — |
| `list` | 每元素一列 + 新增／移除／**↑↓** | 順序調整後儲存生效 |
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

| 元素 | 選取器 | 行為 |
|---|---|---|
| 篩選 | `data-testid="history-filter"` | 「只看內容變更」（預設）／「全部」 |
| 變更列 | `data-testid="history-entry-<sha>"` | 顯示**行為描述**，不顯示內部代號 |
| 差異區 | `data-testid="history-diff"` | 以參數為單位 |
| 退回此版本 | 文字「退回此版本」 | 二次確認 |

**測試須斷言**：列表中**不出現** `cfg`、`revert`、`import` 等字串。

---

## W5 差異檢視

| 元素 | 選取器 | 行為 |
|---|---|---|
| 偏離橫幅 | `data-testid="drift-banner"` | 說明此修改未經介面進行 |
| 來源側 / 目標側 | `data-testid="diff-source"` / `"diff-target"` | 並排參數比對 |
| 差異列 | `data-testid="diff-row-<參數路徑>"` | 標色 |
| 以來源覆蓋 | 文字「以來源覆蓋」 | 二次確認 |
| 納入來源 | 文字「將目標現況納入來源」 | 走完整驗證；**含非法值則被拒** |
| 先納入、待修正 | 文字「先納入、待修正」 | 目標現況載入草稿供修正；**含非法值則載入後警告、進版前須改正**（見 TEST-PLAN T18／A3） |

---

## W6 唯讀、逾時與確認對話框

| 元素 | 選取器 | 行為 |
|---|---|---|
| 唯讀橫幅 | `data-testid="readonly-banner"` | 非持有分頁顯示；含持有者姓名、email、開始時間 |
| 逾時退出提示 | `data-testid="session-timeout"` | 部署模式閒置逾時後出現，階段已釋放 |
| 確認對話框 | `data-testid="confirm-dialog"` | 退出丟草稿／以來源覆蓋／退回此版本／先納入待修正共用；含可見標題與後果說明 |
| 對話框確認鈕 | 對話框內文字「確認」 | 執行該動作 |
| 對話框取消鈕 | 對話框內文字「取消」 | 關閉、不執行 |

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
| 白名單前綴輸入 | `data-testid="whitelist-prefix-input"` | 「加入白名單」預填被拒目標的目錄 realpath（被拒目標是檔案則取父目錄），可編輯；送出走 `POST /api/allowed-roots`，成功後重跑被拒的瀏覽 |
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
| 進入確認 | W7 選檔後文字「檢視並納管」 | 對選定的檔 `POST /api/inspect`，切到確認 view |
| 確認 view | `data-testid="onboard-confirm"` | 整頁容器；開啟時隱藏瀏覽 |
| 格式選擇 | `data-testid="onboard-format"` | 下拉（yaml／json／toml／ini／raw）；預填副檔名的**建議**、使用者可改，改則重新偵測（不變式 8：副檔名不是持續權威） |
| hostname 預覽 | `data-testid="onboard-hostname"` | 「將以 hostname＝<X> 納管」——inspect 回應帶的、納管當下會寫進條目的機器身分（AC5 核對） |
| 權限 | `data-testid="onboard-permissions"` | 原始 owner:group mode |
| 摘要 | `data-testid="onboard-summary"` | 偵測到幾個欄位；`raw` 顯示「只版控、不解析」，不顯示「0 個欄位」這種假訊號 |
| 型別樹 | `data-testid="onboard-types"` | 可折疊樹：容器（dict／list）為可折疊節點、葉為葉；顯示每個值的解析型別供確認（AC3） |
| 型別節點 | `data-testid="onboard-type-<欄位路徑>"` | 帶 `data-type`（型別名）、`data-name`（欄位路徑）；容器帶 `data-container="true"`、單擊折疊/展開子節點 |
| 歧義清單 | `data-testid="onboard-ambiguities"` | `yaml` 才非空；每筆指名值、行號與可能讀法（AC4） |
| 歧義項 | `data-testid="onboard-ambiguity-<行號>"` | 顯示 value／line／readings |
| 歧義確認 | 項內 `data-testid="onboard-ambiguity-ack-<行號>"` | 勾選框；**全部歧義勾完前「確認納管」停用**（確認後才繼續，AC4） |
| 確認納管 | 文字「確認寫入」 | 有未勾的歧義時停用；`POST /api/configs`（source_path／format／ambiguity_note＝逐條確認的彙整），成功回 W2 清單並高亮新條目 |
| 取消 | 文字「取消」 | 回到 W7 瀏覽（不寫入） |
| 錯誤 | `data-testid="onboard-error"` | inspect／onboard 的錯誤**原樣**顯示（inspect 的語法錯誤帶行號就地標示；容忍結構化 `{message,file,line}`、純字串、與 FastAPI 驗證 list 三形狀） |

**測試須斷言**：有歧義的 yaml → 「確認寫入」起始停用、逐條勾完才啟用（AC4）；json 的葉節點型別
以 `data-type` 呈現（AC3）；納管成功後新條目出現在 W2 左側樹（以回應的 uid／ref 對 `tree-item`）。

**hostname 只顯示、不由前端送**：`POST /api/configs` 不收 hostname，後端納管當下自行決定並凍結
（不變式 8、ADR-00000012）；確認畫面顯示的值來自 inspect 回應，供人核對而已。

---

## 角色差異的測試方式

僅開發者可用的元素在一般使用者模式下**直接不存在於 DOM**，而非停用
（ADR-00000020）。因此測試斷言是「找不到該元素」，不是「該元素為 disabled」：

| 元素 | 一般使用者 | 開發者 |
|---|---|---|
| 白名單按鈕 | 不存在 | 存在 |
| 屬性按鈕 | 不存在 | 存在 |
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
