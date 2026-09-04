# config_manager

集中式 config 管理系統。以 Git 為版控核心，Web UI 是唯一正規修改入口：
納管、驗證後修改、進版、退版、偏離偵測。

目標版本 **v0.10.0**（功能完整、可正式投入使用）。設計見 `config-manager-design.pdf`。

---

## 讀的順序

1. **`CONTEXT.md`** — 先統一用語，後面才讀得懂
2. **`doc/PRD.md`** — 9 條產品不變式，任何設計都不得違反
3. **PDF §0–§8** — 目前的設計長什麼樣
4. **`doc/adr/`** — 22 份決策紀錄：為什麼不是別的樣子
5. **`doc/TEST-PLAN.md`** — 動工前先確認測什麼

## 開始

```bash
just docker build      # 建置 devel 映像
just docker run        # 啟動 backend + frontend
just test              # lint + 全層級 + coverage
just                   # 列出所有命令
```

命令模型是**零特例**的：每個動作都在一個 namespace 底下，沒有 top-level 命令；
由廣到小，`just test` 是最大範圍，選項只用來收窄。`lint` 不是 `test` 的同級，
它是 `just test lint`——lint 是測試的一部分。

## 結構

| 路徑 | 放什麼 |
|---|---|
| `Dockerfile` | 多階段：`sys` → `devel-base` → `devel` → `runtime` → `runtime-test` |
| `compose.yaml` | 兩個服務：backend（API + git + 寫出）、frontend（純靜態）。**唯一的服務定義來源**（ADR-00000024） |
| `.setup.conf` | 佔位，無值。導入模板後才成為權威，屆時刪除手寫的 `compose.yaml` |
| `script/` | 任務進入點；`entrypoint.sh` 以 `exec "$@"` 交棒 |
| `script/hooks/` | 模板擴充點，已由自建 wrapper 接線（ADR-00000023） |
| `script/local/` | repo 自有的 just 命令組；`cfg` namespace 是本專案的 CLI |
| `config/` | 專案設定資產（pip、shell） |
| `src/config_manager/core/` | 純邏輯，不碰檔案系統與 git——因此可在無檔案系統下完整測試 |
| `src/config_manager/io/` | 一切對外互動：parsers、git wrapper、原子寫出 |
| `src/config_manager/api/` | HTTP 端點、身分與編輯階段、CLI（CLI 是 API client） |
| `src/config_manager/web/` | 單一 HTML 頁面與靜態資源 |
| `test/` | 三軸：靜態分析 `lint/`、層級 `pytest/`、型別 `bats/`、`reserved/` |
| `doc/` | `adr/`、`changelog/`、`test/`、`TEST-PLAN.md`、`UI-ELEMENTS.md`、`PRD.md` |
| `tools/` + `figures/` | 圖表產生腳本與輸出 |

`src/` 底下只有一個頂層套件 `config_manager/`——頂層目錄名會與 stdlib 撞名，
`io` 就撞了，且在目錄還空著時完全不可見（ADR-00000026）。

依賴方向是 **api → core → io** 單向。`core/` 不 import `api/`；所有檔案系統與
git 操作集中在 `io/`，測試時可替換為 fake——這是整個系統可測試性的地基。

## 與組織共用容器模板的關係

`ycpss91255-docker/base` 提供的共用模板在 v0.10.0 **不引入**（見 PDF 附錄 A）。
理由是模板提供的是完整 lifecycle，而本專案早期不需要那麼多；代價是模板給的東西
要自己做一份。因此區分成三類，只做前兩類：

- **現在就對齊**（改起來貴）：stage 命名、產物烘焙於 `/opt` 而非 `$HOME`、
  網路預設 `host`、`.local` 覆蓋檔名規則、`just` 命令模型、測試三軸、
  ADR 格式、Conventional Commits。
- **自建最小版**：`compose.yaml`（單層，不長主機偵測）、任務入口腳本、
  CI workflow（直接寫，不用共用 worker）、覆蓋率工具（pytest-cov）。
- **現在不要做**：設定解析 + 主機偵測 TUI、共用 CI worker、subtree 升級機制、
  容器 lifecycle（init／restart／watchdog／log persistence）。

最容易犯的錯是把中欄的東西做大。自建的 `compose.yaml` 一旦長出主機偵測與多語
預設，就變成一個競爭版本的設定解析器——導入模板時要再拆掉，等於兩套並存。

## 實作方式

**測試先行（red → green）。** 規則見 `doc/TEST-PLAN.md`：

- **沒有經過確認的測試介面，不寫測試。** 動工前先逐項確認該文件。
- 一次一片：一個測試介面、一個測試、一個最小實作。
- 垂直切片，不橫向切片——不先寫完一個版本的所有測試再實作。
- 重構不在 red → green 迴圈裡，它屬於 review 階段。

## 動手前：分支與 PR

`main` 受保護，**直推會被拒絕**。設定與 `ycpss91255-docker/base` 逐項一致。

```bash
git switch -c <topic>
git push -u origin <topic>
gh pr create --fill
# ci-rollup 綠了就可以自己 merge
gh pr merge --squash
```

**不需要 review approval**（`required_approving_review_count = 0`）——閘門是 CI 不是人。
`strict = true`，merge 前分支要與 `main` 同步。`enforce_admins = true`，對所有人一視同仁。

`ci-rollup` 是唯一的 required check，它 `needs` 其餘全部 job。加新 job 只要改 `needs`，
不用動保護規則。它用 `always()` 加明確的結果判定——否則被 skip 的 job 會被當成成功，
閘門會在什麼都沒檢查的 run 上打開。

## 檢查一律在容器內跑

`just test` 會自行建置 `docker/Dockerfile.test-tools` 並**轉進容器**執行；
CI 呼叫的是同一支 `./script/test.sh`，不安裝任何工具。**一份映像，兩個呼叫者**
（ADR-00000027）。

因為**本機不是專案的證據**。這個 repo 已經為此付過四次代價：本機 pytest 6.2.5
靜默忽略 `pythonpath`；本機沒有 hadolint 而 lint 靜默跳過，CI 連續六次全紅；
`#56` 的 `io` 撞名在本機 3.10 與容器 3.11 呈現兩種不同症狀；workflow 表達式
只有 actionlint 驗得到，而它先前只存在於 CI。

逃生口是 `CM_TEST_LOCAL=1`（在本機跑），它會指名哪幾項因缺工具而沒跑——
**明確跳過仍然不是檢查**。需要別的 apt 鏡像時用 `CM_APT_MIRROR`。

| 檢查 | 擋什麼 |
|---|---|
| `ruff` | 巢狀深度、循環複雜度、函式長度、參數個數、禁止吞錯誤 |
| `mypy --strict` | `src/config_manager/` 全覆蓋（`core`／`io`／`api`） |
| `pylint` | ruff 未涵蓋的設計層面 |
| `shellcheck` | 全部 shell 腳本 |
| `hadolint` | Dockerfile |
| `actionlint` | workflow 的**表達式**——YAML parser 看不到的那一層 |
| `commit` | commit 訊息，規則取樣自 base（ADR-00000025） |
| `adr` | ADR 檔名、編號與結構 |

`pytest` 的 coverage 下限 85% 寫在 `pyproject.toml`，CI 不重述。`core`／`io`／`api`／`web`
**四個資料夾各自計算、各自擋**（`script/coverage_gate.sh`）——一個門檻同時守四個標準不同的
區域，結果是守住最鬆的那個。

## 給後續維護者 / agent

- **不要重建設計文件。** 直接使用；有新決策就在 `doc/adr/` 加新號（目前 28 份，從 `00000029` 續接）。
- **ADR 的編號與結構 lint 要在 v0.1.0 就寫**：重號 fail、檔名格式不符 fail、
  缺 `> 服務：` 或必要段落 fail、跳號與缺 Alternatives 僅 warn。
  平行開發撞號是真實發生過的缺陷，人工檢查抓不到。
- **圖表不要手改 SVG。** 改 `tools/` 的腳本再重跑（`cd tools && python3 gen_flows.py`）。
- **Wireframe 是 HTML 的規格。** 實作介面時對照 `doc/UI-ELEMENTS.md`。
- **新增術語前先查 `CONTEXT.md`**，不重新造詞。
- **commit 訊息**：`<type>(<scope>): <中文陳述句>`。格式沿用 base，**語言是中文**——
  本 repo 不是 base 的 downstream，紀錄的讀者就是這裡的人；只有 `type` 與 `scope`
  維持英文，那兩個是給工具讀的（ADR-00000025、ADR-00000028）。
  規則不在文件裡，在 `just test lint commit`，錯誤訊息會直接說該怎麼改。
  只檢查分支新增的 commit，不檢查歷史。
- **PDF 中標記為「推導內容」的章節**（目錄樹、端點表、欄位表、閾值表、里程碑）
  在對應程式碼落地後應刪除，改為指向真實來源。見 PDF §0.7。已落地的有：
  §0.6 閾值表 → `pyproject.toml`；§8 版本里程碑與驗收檢查點 → GitHub Milestones + Issues。

## 里程碑

v0.1.0 – v0.10.0 十個里程碑已建立於
[GitHub Milestones](https://github.com/ycpss91255-research/config_manager/milestones)，
每個 milestone 的說明含該版本的能力矩陣與 §8.2 驗收檢查點；
54 個 issue 依能力矩陣逐項拆分，各自帶驗收條件（§0.7：驗收條件屬 issue 的 acceptance criteria）。

**動工前先關掉 [#1](https://github.com/ycpss91255-research/config_manager/issues/1)**——
逐項確認 `doc/TEST-PLAN.md` 的 18 個測試介面與 6 個驗收旅程。沒有經過確認的測試介面，不寫測試。

`v1.0.0` 不設在本規劃內，待實際運行一段時間後由維護者自行標記。

## 目前狀態

骨架已建立。`src/config_manager/core/` 進行中：v0.1.0 的 T1（清單檔載入與寫回）與
T5（身分推導）已通過。`io/`、`api/`、`web/` 尚未開始。
