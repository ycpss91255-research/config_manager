# CLAUDE.md

`config_manager` 的 agent 指引。**先讀 `CONTEXT.md`** 取得領域用語——用那裡的詞，不要另造同義詞。

## 這是什麼

集中式 config 管理系統：config repo 是唯一真實來源，寫出至目標位置，變更前先驗證，偏離會被呈現而非自動處置。Python 3.11 + FastAPI backend；目前只有核心層落地。

閱讀順序：`CONTEXT.md` → `doc/PRD.md`（9 條不變式，不得違反）→ `doc/adr/`（為什麼長成這樣）→ `doc/TEST-PLAN.md`（已確認的測試介面）。

## 分層

`api → core → io` 單向（ADR-00000011）。以頂層模組名匯入（`from config_manager.core.config_list import load`、`python -m config_manager.api.cli`）。

- `src/config_manager/core/` — 純邏輯，**不做 I/O**：需要檔案內容？當參數傳進來。核心層的每個測試都在無檔案系統、無 git、無網路的情況下執行。
- `src/config_manager/io/` — 檔案系統與 git 的 adapter。所有東西都在單一頂層套件底下，因為頂層的 `io/` 會遮蔽 stdlib 的同名模組，甚至讓直譯器無法啟動（ADR-00000026）。
- `src/config_manager/api/` — HTTP 端點與 CLI（CLI 是 HTTP client，ADR-00000009）。
- `src/config_manager/web/` — 單一 HTML 入口。

## 工作流程

- **依 milestone 與 issue 推進。** 照 GitHub milestone 的順序做（`gh issue list --milestone v0.1.0`），不要跳版。
- **分支 → PR → squash。** 新工作開 feature branch，開 PR，由維護者 squash merge。**不得直推 `main`，不得 force push**——這個 org 是多人共推，推之前先 `git fetch` 加 `git rebase origin/main`。
- **自己無法決定的事**（owner 層級／架構）→ 開 GitHub issue 並等待，但不要空轉：同時去做另一個沒被擋住的 issue。
- **已交付行為的 bug** → `fix` 加 patch 版號；一個 milestone 的功能是 minor。
- **決策要寫回它被提出的地方。** 討論定案後，把決策、佐證、以及被否決的選項與理由寫回該 issue 或 PR，不要只留在對話裡。關閉帶驗收條件的 issue 時，**證據附在 issue 上，勾選框實際勾起來**。

## Commit 訊息

格式沿用 base 的慣例，由 `script/lint_commit.sh` 檢查（ADR-00000025、ADR-00000028）。自查：`./script/test.sh --lint commit`。

- type 限 `feat fix docs refactor test chore ci perf`，其餘一律 fail
- `type(scope): ` 前綴必須存在且格式正確；句尾不加句號
- **主旨以中文書寫**，且是**陳述句**，說明現在什麼成立（`feat(state): 目標不存在時狀態判定為未部署`），不是「新增 X」這種祈使句
- **只有 type 與 scope 維持英文**——那兩個是給工具讀的識別碼（ADR-00000028）
- scope 與 issue 回指建議附上；長度不檢查

commit 內文結尾加 `Co-Authored-By:` trailer；PR 描述結尾加 Claude Code 那一行。

## 品質閘門（本機與 CI 同一組）

**所有檢查一律在容器內執行，絕不對這台主機跑。**

`./script/test.sh`（或 `just test`）會建置 `docker/Dockerfile.test-tools` 並把自己轉進容器重跑；CI 呼叫同一支腳本，不安裝任何工具。一份映像，兩個呼叫者（ADR-00000027）。綠了才推。

不要為了「快速確認一下」就去呼叫主機的直譯器。**主機不是這個專案的證據**，而這句話不是風格偏好——它已經在這裡給過四個錯誤答案：

| 主機說 | 實際 |
|---|---|
| pytest 收集不到測試 | 主機的 pytest 6.2.5 靜默忽略 `pythonpath`，專案需要 8+ |
| lint 乾淨 | 主機沒有 hadolint，CI 因此連續六次全紅 |
| 直譯器無法啟動 | 在 3.11 下同一個 `io` 撞名（#56）是 `ModuleNotFoundError` |
| workflow 沒東西可檢查 | actionlint 只存在於 CI，而它讀的是 YAML parser 看不到的那一層 |

`CM_TEST_LOCAL=1` 仍可在主機跑。它會先盤點主機並列出**全部**缺少的檢查工具，然後停下。再加 `CM_LINT_ALLOW_MISSING=1` 才會跑其餘的，並重述哪些沒跑——**有跳過的執行不算通過**，不要把它報成綠燈。`CM_APT_MIRROR` 可覆寫映像的 Debian 鏡像。

- `ruff check src test`／`mypy --strict src/config_manager/core`／`pylint src`（10.00/10）／`pytest test/pytest --cov=src/config_manager/core`（下限 85）
- shell 腳本需要 **bash 4+**（macOS 用 `brew install bash`，內建的 3.2 跑不動）

## TDD

紅 → 綠，一次一片。**測試只寫在 `doc/TEST-PLAN.md` 已確認的介面上**，絕不寫在未確認的介面上。期望值來自獨立來源；每個測試一個邏輯斷言；測試名稱說明程式碼做什麼，用 `CONTEXT.md` 的詞彙。

## Agent skills

### Issue tracker

GitHub Issues（`ycpss91255-research/config_manager`），以 `gh` CLI 操作。決策與驗收證據
一律寫回 issue。見 `doc/agents/issue-tracker.md`。

### Triage 標籤

狀態標籤用 `backlog` ／ `ready-to-agent` ／ `wontfix`；`needs-info` 與 `ready-for-human`
刻意無對應（沒有外部回報者）。見 `doc/agents/triage-labels.md`。

### 領域文件

單一 context：`CONTEXT.md` ＋ **`doc/adr/`**（不是 `docs/adr/`——skill 的預設路徑在這裡
是錯的）。見 `doc/agents/domain.md`。
