# TEST

怎麼跑測試，以及每一格測什麼。**測什麼**的完整定義在 `doc/TEST-PLAN.md`
（18 個測試介面 T1–T18、6 個驗收旅程 A1–A6）——那份文件是提案，動工前需逐項確認。

## 跑

```bash
just test                    # 全部：lint + 所有層級 + coverage
just test lint               # 只跑靜態分析
just test lint ruff          # 單一 linter
just test level unit         # 單一層級
just test file <path>        # 單一 spec 檔
just test filter <regex>     # 符合樣式的 spec
```

## 三軸

過去常見的錯誤是把三個不同性質的東西混成一個「四分類」：Unit 與 Integration 是
**層級**（範圍），Smoke 是**型別**（目的），Lint 是**靜態分析**（根本不是動態測試）。
本專案明確分成三軸。

### 軸 1｜靜態分析

不是動態測試層級。位於 `test/lint/<tool>/`，設定集中在 `pyproject.toml`
與 `.hadolint.yaml`。

| 工具 | 擋什麼 |
|---|---|
| `ruff` | 巢狀深度、循環複雜度、函式長度、參數個數、禁止吞錯誤 |
| `mypy --strict` | `src/core/` 全覆蓋——型別錯誤成本最高、且無 I/O 可躲的一層 |
| `pylint` | `ruff` 未涵蓋的設計層面檢查 |
| `hadolint` | Dockerfile |

閾值不寫在文件裡，寫在 `pyproject.toml`——無法自動檢查的規範等於不存在。
調整閾值須修改設定檔並在 PR 中說明理由，使閾值本身成為可見的決策。

### 軸 2｜層級（範圍階層）

| 層級 | 範圍 | 本專案的具體對象 | 位置 |
|---|---|---|---|
| Unit | 單一函式／模組、隔離狀態下 | `core/` 的狀態判定、驗證、config 清單檔完整性 | `test/pytest/unit/` |
| Integration | 數個模組協作 | 納管流程、commit + apply 的原子性、round-trip 讀寫、git wrapper 對真實 repo | `test/pytest/integration/` |
| System | 整個建好的映像，端到端 | 容器啟動 → API 可用 → 完整使用週期 | `test/pytest/system/` |
| Acceptance | 使用者實際收到的東西與 UX | Web UI 的可操作性、錯誤訊息可行動性、CLI help | `test/pytest/acceptance/` |

最高層級是 System。「End-to-end」是在 System／Acceptance 層執行的**型別**，不是層級名稱。

### 軸 3｜型別（目的，套用於某個層級）

| 型別 | 意思 | 位置 |
|---|---|---|
| Smoke | 建置驗證：「它到底跑不跑得起來」的關鍵子集 | `test/bats/smoke/`，在 `runtime-test` stage 建置過程中執行 |
| End-to-end | 一條從頭到尾的完整流程 | 套用於 System 層 |
| Regression | 守住每個已修好的缺陷。**每個修好的 bug 都應留下一條 regression 測試** | 套用於其發生的層級 |
| 非功能性 | Performance／Security／Usability／Reliability | `test/reserved/`，空目錄佔位 |

## 測試檔鏡射原始碼

**測試檔鏡射原始碼。原始碼的結構由設計決定，絕不由測試檔的數量或形狀決定。**

- 一個原始碼檔預設對應一個 spec：`core/config_list.py` ↔ `test/pytest/unit/test_config_list.py`
- 確實需要多個單元 spec 的模組，開一個以原始碼檔命名的資料夾：
  `test/pytest/unit/config_list/test_<subunit>.py`
- **絕不為了達成 1:1 對應而拆分原始碼**
- Integration／end-to-end 測試放在**它所驅動的 orchestrator 旁**，不放在某端點模組旁

## 刻意留空

沒有測試介面覆蓋、但**寫下了理由**的位置是刻意留空；沒寫理由的就是漏掉。

| 沒有測試介面 | 為何可以沒有 |
|---|---|
| 資料模型 | 是資料宣告，本身無行為。其約束（必填、型別）在 T1 載入時被驗證 |
| 模式判定（開發／部署） | 讀設定回傳一個判定值，無邏輯可測。其效果在 T13（逾時差異）與 T11（介面差異）被驗證 |
| 三層驗證的「層」本身 | 層是組織方式而非行為。各層的實際檢查在 T3 逐項驗證 |

## 明確不測的

| 不測 | 理由 |
|---|---|
| 前端 DOM 結構、CSS class、元素排列順序 | 改樣式即壞而行為沒變。介面行為改由 T11 以語意選取器驗證 |
| 第三方函式庫的內部行為 | 不是我們的行為 |
| HTTP 框架的路由機制 | 同上 |
| 核心層的私有函式 | 反模式：測實作細節，重構就壞 |
| 「某函式有被呼叫」 | 反模式：以呼叫次數或順序斷言 |

## 迴圈規則

- **紅燈先行。** 先寫失敗的測試，再寫剛好讓它通過的程式碼。不預先為未來的測試寫東西。
- **一次一片。** 一個測試介面、一個測試、一個最小實作。
- **垂直切片。** 每個測試是一發曳光彈，依上一輪學到的東西調整下一輪。
- **重構不在迴圈裡**，它屬於 review 階段。
