# TEST

怎麼跑測試，以及每一格測什麼。**測什麼**的完整定義在 `doc/TEST-PLAN.md`
（21 個測試介面 T1–T21、6 個驗收旅程 A1–A6）——該文件已於 2026-09-04 逐項確認，
不再是提案；新增測試介面仍須先更新它並取得確認（ADR-00000018）。

本檔只負責**怎麼跑**與**三軸的劃分**。判準（刻意留空、明確不測的、迴圈規則）
一律以 `doc/TEST-PLAN.md` 為準，這裡不另記一份——理由見文末。

## 跑

**一律在容器內。** `script/test.sh` 偵測到自己不在測試映像內時會自行建置
`docker/Dockerfile.test-tools` 並轉進去重跑；CI 走同一條路徑（ADR-00000027）。
`CM_TEST_LOCAL=1` 可在本機跑，但它會指名哪幾項因缺工具而沒跑。

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
| 工具 | 擋什麼 | 範圍 |
|---|---|---|
| `ruff` | 巢狀深度、循環複雜度、函式長度、參數個數、禁止吞錯誤 | `src` 與 `test` |
| `mypy --strict` | 型別錯誤成本最高、且無 I/O 可躲的一層 | **只有 `src/config_manager/core/`** |
| `pylint` | `ruff` 未涵蓋的設計層面檢查 | `src` |
| `hadolint` | Dockerfile | — |

閾值不寫在文件裡，寫在 `pyproject.toml`——無法自動檢查的規範等於不存在。
調整閾值須修改設定檔並在 PR 中說明理由，使閾值本身成為可見的決策。

**`mypy` 與覆蓋率的範圍都只有 `core/`，因此 `io/` 與 `api/` 兩者皆無。** 那兩層有整合與
系統層的規格，但沒有任何數字會在它們的分支未被執行時轉紅。這是已知缺口，現況、影響與
修法記在 `doc/TEST-PLAN.md`「已知的量測缺口」，追蹤於 #97——**寫下來，不假裝不存在。**

### 軸 2｜層級（範圍階層）

| 層級 | 範圍 | 本專案的具體對象 | 位置 |
|---|---|---|---|
| Unit | 單一函式／模組、隔離狀態下 | `core/` 的狀態判定、驗證、config 清單檔完整性 | `test/pytest/unit/`；守門腳本（T19）在 `test/bats/unit/` |
| Integration | 數個模組協作 | 納管流程、commit + apply 的原子性、round-trip 讀寫、git wrapper 對真實 repo | `test/pytest/integration/`、`test/bats/integration/` |
| System | 整個建好的映像，端到端 | 容器啟動 → API 可用 → 完整使用週期 | **`test/bats/runtime/`**（見下方說明）；`test/pytest/system/` 目前是空的佔位 |
| Acceptance | 使用者實際收到的東西與 UX | Web UI 的可操作性、錯誤訊息可行動性、CLI help | `test/pytest/acceptance/` |

最高層級是 System。「End-to-end」是在 System／Acceptance 層執行的**型別**，不是層級名稱。

**shell 用 bats、Python 用 pytest**，一個層級有哪種規格就跑哪種；`script/test.sh --level <name>`
兩邊都跑。

**System 層是唯一不由 `script/test.sh` 執行的位置。** `test/bats/runtime/` 由 `Dockerfile` 的
`runtime-test` 階段在建置時執行，CI 的 build job 直接建那個階段。原因是結構性的：`test.sh`
把自己 `docker run` 進工具映像裡跑，那個容器沒掛 docker socket、也沒有 docker CLI，
因此無法啟動「被測的那個容器」；而 `runtime-test` 階段**本身就是**那個容器。
完整說明見 `doc/TEST-PLAN.md` 的 T9。

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

## 判準不在這裡

**刻意留空**、**明確不測的**、**迴圈規則**這三份判準，唯一的記載位置是
`doc/TEST-PLAN.md`：

| 要找什麼 | 去哪裡 |
|---|---|
| 哪些位置沒有測試介面、為什麼可以沒有 | `doc/TEST-PLAN.md`「覆蓋率審計 → 刻意的空格」 |
| 哪些東西明確不測 | `doc/TEST-PLAN.md`「明確不測的」 |
| 紅燈先行、一次一片、垂直切片、重構不在迴圈裡 | `doc/TEST-PLAN.md`「撰寫規則」 |
| 每個模組／腳本落在哪個測試介面上 | `doc/TEST-PLAN.md`「覆蓋率審計 → 模組 → 測試介面」 |

**這三節先前在本檔各有一份副本，而副本已經漂移**：本檔的「刻意留空」只列三項，
`TEST-PLAN.md` 是四項（少的是「未納管」的判定）；「明確不測的」兩份措辭不同；
「迴圈規則」是「撰寫規則」七條裡的四條。沒有人動手讓它們不一致，
**它們只是各自被改過一次**（#74）。

這正是 ADR-00000024 記下的形態：兩份寫著同一件事，只有一份會被讀，
**沒被讀的那份會靜默過期**。判準是實作前要查的東西，查的人打開的是 `TEST-PLAN.md`，
所以判準留在那裡，這裡只留指標。
