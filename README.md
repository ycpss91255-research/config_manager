# config_manager

集中式 config 管理系統。以 Git 為版控核心，正規的修改都經由這個服務：
納管、驗證後修改、進版、退版、偏離偵測。目前 Web UI 能做全部操作，CLI 只能做其中幾項（見下一節）。

目標版本 **v0.10.0**（功能完整、可正式投入使用）。設計見 `config-manager-design.pdf`。

---

## Web UI 與 CLI 能做什麼

| 操作 | Web UI | CLI |
|---|---|---|
| 列出納管項目與狀態（含偏離） | 可以 | `just cfg list` |
| 納管（選檔、偵測格式與歧義） | 可以 | `just cfg browse`、`just cfg inspect`、`just cfg import` |
| 修改參數值、存草稿、捨棄草稿、進版 | 可以 | 還沒有 |
| 查看歷史、退版 | 可以 | 還沒有 |
| 偏離處置（以來源覆蓋、納入現況、先納入待修正、以來源重新寫出）、寫出修復 | 可以 | 還沒有 |
| 搜尋參數名稱與值 | 可以 | 還沒有 |
| 解除納管 | 可以 | 還沒有 |
| 開發者：維護白名單、產生 schema、指定型別、編輯屬性 | 可以 | 還沒有 |
| 備份、還原 | 沒有 | `just docker backup`、`just docker restore`（見 [`doc/DEPLOYMENT.md`](doc/DEPLOYMENT.md)） |

CLI 現有的指令：

```bash
just cfg list
just cfg browse --path <白名單內的目錄>
just cfg inspect --source <檔案> --format <yaml|json|toml|ini|raw>
just cfg import --source <檔案> --format <格式> --name <姓名> --email <email>
```

- 都連到這台機器上執行中的服務（預設 `http://127.0.0.1:8080`，可用 `--api` 改）。
- `just cfg import` 與網頁走同一套規則：要先取得編輯權，別人正在編輯時會被拒絕；角色預設是一般使用者，開發者加 `--role developer`。
- `just cfg serve` 是直接在這台機器上起後端；一般請用下面「開始」裡的 `just docker run`。
- **限制**：`just cfg` 是在這台機器上直接執行 CLI，需要本機有 CLI 的執行環境。目前的安裝步驟沒有包含這個環境，從零 clone 之後能不能直接用，也沒有驗證過。

## 支援平台

這裡只寫**有什麼證據**；沒有證據的一律標「未驗證」，不寫成「已支援」。兩種證據分開看：

- **CI 自動化**：GitHub Actions 的 `ubuntu-latest` 上自動跑的檢查。它證明那些檢查在 CI 上通過，**不等於有人照這份 README 操作過**。
- **真人照文件操作**：有人照這份 README 從零走過一次，並留下紀錄。

| 平台 | 網路模式 | CI 自動化 | 真人照文件操作 |
|---|---|---|---|
| Linux（Ubuntu） | `host`（預設） | 有：建映像；`host` 與 `bridge` 各實際起一次服務、確認只有本機連得到；備份與復原演練。都用隔離的專案名與暫存目錄，**不是照 README 的步驟** | 未驗證 |
| macOS（Docker Desktop） | `bridge` | 沒有 | 未驗證 |
| Windows（Docker Desktop） | `bridge` | 沒有 | 未驗證 |

- Docker Desktop（macOS、Windows）上，預設的 `host` 網路不是主機的網路，要改用 bridge 網路的疊加檔。做法與限制見 [`doc/DEPLOYMENT.md` 的「bridge 網路」](doc/DEPLOYMENT.md#bridge-網路)。
- Windows 的 WSL2 裡直接安裝 Docker Engine 的情況，沒有評估過。
- 擴展到其他平台的評估，見 [`doc/CROSS-PLATFORM.md`](doc/CROSS-PLATFORM.md)。

## 開始

```bash
just docker dev build  # 建置開發映像（疊上 compose.dev.yaml）
just docker dev run    # 以開發模式啟動 backend + frontend
just                   # 列出所有命令
```

`just docker build`／`just docker run` 不帶 `dev` 起的是**現場部署**那一套（正式映像、部署模式、
只有本機連得到、會自動重啟）；怎麼部署、怎麼從別台機器連進來、起不來時怎麼看原因，見
[`doc/DEPLOYMENT.md`](doc/DEPLOYMENT.md)。

## 開發者文件

目錄結構、測試與檢查、分支與 PR 的規則，以及給後續維護者的說明，見 [`doc/DEVELOPMENT.md`](doc/DEVELOPMENT.md)。

## 目前狀態

**每個模組落地到哪裡、由哪個測試介面觀察，看 `doc/TEST-PLAN.md` 的「覆蓋率審計 →
模組 → 測試介面」表。** 那張表有「狀態」欄，逐列寫著那個模組落地了沒有，
而它是這件事的唯一 owner。

這裡不放第二份。上一份放在這裡的清單過期了——它說 `io/` 與 `api/` 尚未開始，
而那兩層當時都已經有模組合併進來（#98）。兩份寫著同一件事，沒被讀的那份會過期
（ADR-00000024），而讀者無從分辨自己讀到的是哪一份。
