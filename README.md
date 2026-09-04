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
| `compose.yaml` | 兩個服務：backend（API + git + 寫出）、frontend（純靜態） |
| `script/` | 任務進入點；`entrypoint.sh` 以 `exec "$@"` 交棒 |
| `config/` | 專案設定資產（pip、shell） |
| `src/core/` | 純邏輯，不碰檔案系統與 git——因此可在無檔案系統下完整測試 |
| `src/io/` | 一切對外互動：parsers、git wrapper、原子寫出 |
| `src/api/` | HTTP 端點、身分與編輯階段、CLI（CLI 是 API client） |
| `src/web/` | 單一 HTML 頁面與靜態資源 |
| `test/` | 三軸：靜態分析 `lint/`、層級 `pytest/`、型別 `bats/`、`reserved/` |
| `doc/` | `adr/`、`changelog/`、`test/`、`TEST-PLAN.md`、`UI-ELEMENTS.md`、`PRD.md` |
| `tools/` + `figures/` | 圖表產生腳本與輸出 |

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

## 給後續維護者 / agent

- **不要重建設計文件。** 直接使用；有新決策就在 `doc/adr/` 加新號（從 `00000023` 續接）。
- **ADR 的編號與結構 lint 要在 v0.1.0 就寫**：重號 fail、檔名格式不符 fail、
  缺 `> 服務：` 或必要段落 fail、跳號與缺 Alternatives 僅 warn。
  平行開發撞號是真實發生過的缺陷，人工檢查抓不到。
- **圖表不要手改 SVG。** 改 `tools/` 的腳本再重跑（`cd tools && python3 gen_flows.py`）。
- **Wireframe 是 HTML 的規格。** 實作介面時對照 `doc/UI-ELEMENTS.md`。
- **新增術語前先查 `CONTEXT.md`**，不重新造詞。
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

骨架已建立。`src/core/` 進行中：v0.1.0 的 T1（清單檔載入與寫回）與
T5（身分推導）已通過。`io/`、`api/`、`web/` 尚未開始。
