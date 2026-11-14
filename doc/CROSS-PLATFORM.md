# 跨平台擴展的評估

#362 第 2 條：評估並記錄是否要針對 macOS／Windows 進行跨平台擴展。這份依 main `e7dd278` 的程式與設定、以及 2026-11-14 讀到的官方文件寫成。支援到哪裡、有什麼證據，看 `README.md` 的「支援平台」。

每一項分四部分寫：**已定案**（維護者的決定）、**現在可評估的方案**、**需要實驗回答的問題**、**相關決策**（只列維護者明定、或具體會被影響的部分）。標「推論」的沒有實測。

## 已定案（維護者在 #362）

- 跨平台入口工具與後端重構統一使用 Rust，編成各平台的單一執行檔；不採用 Python、Go（11/12）。
- 能換 Rust 就換 Rust；換不成，就維持現行方式，或採用該領域的主流做法（11/14）。
- 這份評估的**順序**依 #334 的 spike 結果自行調整（11/14）。這是排序上的安排，不是「評估要等 spike 完成」。

## 現況：哪裡綁定 Linux

| 項目 | 現況 | 在 macOS／Windows 上的影響 |
|---|---|---|
| 服務本體 | 兩個容器（backend、frontend），以 `compose.yaml` 定義；映像內的使用者 uid 1000 | 只要有能跑 Linux 容器的 Docker，本體不分主機平台 |
| 網路 | 預設 `network_mode: host`；另有 `compose.bridge.yaml` 疊加檔 | Docker Desktop 上 `host` 網路不是主機的網路（`script/compose_smoke.sh` 的說明），現行做法是改走 bridge |
| 入口 | `just` 的 recipe 轉給 `script/` 底下的 bash 腳本（都以 `#!/usr/bin/env bash` 開頭）；腳本需要 bash 4 以上 | macOS 內建 bash 3.2 跑不動；Windows 要另有 bash 環境 |
| 疊加檔的串接 | `script/justfile.docker` 以 `COMPOSE_FILE=compose.yaml:compose.dev.yaml`（冒號）串接 | Docker 文件：`COMPOSE_FILE` 的分隔符在 Windows 預設是分號，macOS 與 Linux 是冒號，可用 `COMPOSE_PATH_SEPARATOR` 改。推論：照現在的寫法在 Windows 上串不起來 |
| 目標目錄的掛載 | `${CM_TARGET_ROOT}:${CM_TARGET_ROOT}`，容器內外同一條絕對路徑 | Docker Desktop（Mac）文件：預設只分享 `/Users`、`/Volumes`、`/private`、`/tmp`、`/var/folders`，所以預設的 `/opt/robot/config` 不在分享範圍內。推論：Windows 的路徑（例如 `C:\…`）不能原樣當成容器內的路徑 |
| CI | 全部 job 都跑在 `ubuntu-latest` | macOS、Windows 都沒有任何自動化檢查 |

## 逐項評估

### 1. 入口與疊加檔的串接

- **已定案**：入口工具用 Rust，編成各平台的單一執行檔。
- **現在可評估的方案**：
  - (a) 維持現行的 `just`＋bash：macOS 要另裝 bash 4 以上；Windows 要有 bash 環境，而且疊加檔要改用 `COMPOSE_PATH_SEPARATOR` 或多個 `-f`。
  - (b) 依定案，以 Rust 寫一支入口，涵蓋現有的動作（建置、啟動、停止、進入容器、清理、備份、還原、啟動前的檢查），由它呼叫 `docker compose`；疊加檔用多個 `-f` 傳，不經 `COMPOSE_FILE`，分隔符的問題就不存在。
  - 評估：(b) 符合定案，也同時解掉 bash 版本與分隔符兩個問題。(a) 是 (b) 完成前的現況。
  - 不論哪一種，入口都要對得上同一份行為清單（上面那些動作與它們的前置檢查）；這份清單現在就能寫。
- **需要實驗回答的問題**：
  - Rust 單一執行檔在 macOS、Windows、Linux 上能否不依賴其他執行環境就跑起來（要實際建置與執行）。
  - Windows 上由入口傳給 `docker compose` 的路徑，在容器裡長成什麼樣子（與第 3 項一起量）。
- **相關決策**：
  - #365（發版時附各平台的單一執行檔）是同一方向的發布端。
  - #349 的標題明寫以「後端改用 Rust 或其他語言」為條件，與入口工具無關。
  - 維護者沒有明定入口工具要等 #334，這裡不加這個依賴。

### 2. Docker Desktop 上的網路

- **已定案**：預設 `host`，bridge 要明確選用（`compose.bridge.yaml` 的說明，不變式 4）；理由是 bridge 會讓同一台機器上的 ROS 2 discovery 靜默失效。
- **現在可評估的方案**（現有的 bridge 路徑，讀設定檔得到）：
  - 啟動：疊上 `compose.bridge.yaml`，`CM_HOSTNAME` 必填（沒給就在啟動前擋下）。
  - 連線：主機端只綁 `127.0.0.1:8080`、`127.0.0.1:8081`；backend 在容器內聽 `0.0.0.0`。頁面以 `${location.hostname}:8080` 找後端（`index.html` 第 704 行），所以在同一台機器開 `http://localhost:8081` 時，頁面會去連 `localhost:8080`。推論：兩者對得上。
  - 埠：8080、8081 與兩個容器名都寫死在 compose 檔裡，不能改；同一台機器上已有東西在用時起不來。
  - 選項：
    - (a) 維持現狀：Docker Desktop 上由使用者明確選 bridge（README 已這樣寫）。
    - (b) 由入口偵測 Docker Desktop，自動疊上 bridge。在 Docker Desktop 上 `host` 本來就不是主機的網路，ROS 2 discovery 的理由不成立（推論）；但這是改預設，要維護者決定。
    - (c) Docker 文件：Docker Desktop 4.34 以後有可選的 host networking（Settings → Resources → Network → Enable host networking），只支援 Linux 容器、容器不能綁主機的 IP、只到第 4 層。Docker Desktop 的設定頁把這個選項列在 Mac 底下；Windows 上能不能用，沒有查。推論：可能讓 Docker Desktop 也走預設的 `compose.yaml`，但現有設定把服務綁在 `127.0.0.1`，在這種模式下能否從主機連到，要實測。
- **需要實驗回答的問題**：
  - bridge 路徑在 Docker Desktop 上實際起得來、頁面連得到後端（#348 的實跑）。
  - (c) 的 host networking 在 Docker Desktop 上能否跑現有的 `compose.yaml`。
- **相關決策**：#361 的單一入口與 `/config_manager` 路徑前綴若定案，會改變「前後端兩個埠」與「頁面怎麼找後端」（`CM_API_BASE`），bridge 的埠對應要跟著改。只有這一部分與 #361 有關；啟動、`CM_HOSTNAME` 與現有的連線方式不受影響。#361 目前只授權 ADR 草稿。

### 3. 路徑與權限

- **已定案**：服務以非 root（uid 1000）執行，不改動主機系統（#360）。
- **現在可評估的方案**：
  - 目標目錄要在 Docker Desktop 的分享範圍內；macOS 上預設的 `/opt/robot/config` 不在範圍內，要用 `CM_TARGET_ROOT` 指到 `/Users` 底下之類的路徑（README 第 3 步已寫明要在分享範圍內）。
  - Windows：容器內外同一條路徑的設計，與 Windows 的路徑形式不相容（推論）。選項是限定在 WSL 的路徑下使用，或讓掛載改成「主機路徑 → 固定的容器內路徑」。後者會改到白名單與清單檔記的路徑，影響範圍大，要另外決定。
- **需要實驗回答的問題**：
  - Docker Desktop 上，uid 1000 寫出的檔案在主機上的擁有者與權限。
  - Windows 上 `CM_TARGET_ROOT` 的路徑在容器裡的樣子，以及寫出能否成功。
- **相關決策**：無。

### 4. CI 的多平台檢查

- **已定案**：目前沒有使用 self-hosted runner 的打算（#356）。
- **現在可評估的方案**：
  - runner 的能力（讀 GitHub runner 映像的說明）：macOS 映像沒有列出任何容器工具；Windows 映像列出 Docker 29.7.2、Docker Compose 2.40.3，只啟用 WSLv1，快取的映像都是 Windows 容器。推論：兩者都不能直接跑這裡的 Linux 容器，說明頁沒有直接寫明。
  - 所以「服務在 Docker Desktop 上起不起得來」不能在 GitHub-hosted runner 上自動驗證；這一半靠實機（#348）。
  - 入口工具本身（第 1 項的 (b)）不需要容器也能在 `macos-latest`、`windows-latest` 上測：參數、找不到 docker 時的訊息、傳給 `docker compose` 的參數。
- **需要實驗回答的問題**：Windows runner 能否以任何方式跑 Linux 容器（例如另裝）；要不要做，取決於成本。
- **相關決策**：無。

## 哪些工作可以獨立進行、哪些依賴後端重構

依 11/13 提案（「哪些工作可獨立進行、哪些依賴後端重構，在評估文件中逐項列出」）。「依賴」只列維護者明定、或內容本身就以後端重構為前提的。

| 工作 | 可以獨立進行？ | 說明 |
|---|---|---|
| 入口工具改成 Rust 單一執行檔（第 1 項） | 可以 | 入口呼叫的是 `docker compose`，不呼叫後端的程式；維護者沒有明定它要等後端 |
| 入口的行為清單（第 1 項） | 可以 | 不論用什麼語言都要對得上 |
| Docker Desktop 的網路處理（第 2 項） | 可以 | 只有埠與「頁面怎麼找後端」與 #361 的單一入口有關 |
| 路徑與權限的實測與處理（第 3 項） | 可以 | 是 Docker 與主機的行為 |
| CI：入口工具在 macOS、Windows 上的測試（第 4 項） | 可以，但要等入口工具寫出來 | 測的對象是第 1 項的產物 |
| CI：服務在 Docker Desktop 上的檢查（第 4 項） | 可以，但 GitHub-hosted runner 做不到（推論） | 靠實機（#348） |
| #349（移除 `src/config_manager/` 包層） | 依賴後端重構 | 標題就以「後端改用 Rust 或其他語言」為條件 |

## 結論：要不要擴展

| 範圍 | 判斷 | 條件 |
|---|---|---|
| Linux（`host`） | 維持為主要平台 | CI 全部跑在這裡；真人照 README 操作仍未驗證（#348） |
| macOS、Windows 透過 Docker Desktop | 要支援，以「照文件可用」為目標 | 依據：維護者在 #348 定案 README 的驗收要涵蓋 Docker Desktop 的 bridge 路徑。先走現有的 bridge 路徑並在 #348 實跑；路徑與權限照第 3 項處理；Windows 的路徑形式要另外決定 |
| 入口在 macOS、Windows 上原生執行（不需要 bash） | 要，依定案以 Rust 單一執行檔做 | 第 1 項的實驗；CI 依第 4 項只測入口本身 |
| 服務本體不經容器、直接在 macOS／Windows 上執行 | 不在這次評估的範圍 | 現行與已定案的方向都是容器部署 |

## 參考

- Docker：Compose 的環境變數（`COMPOSE_PATH_SEPARATOR`、`COMPOSE_FILE`）、Docker Desktop 的設定（File sharing、host networking）、host 網路驅動。
- GitHub：`actions/runner-images` 的 macOS 15 與 Windows Server 2022 映像說明。
