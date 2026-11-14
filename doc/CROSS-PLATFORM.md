# 跨平台擴展的評估

#362 第 2 條：評估並記錄是否要針對 macOS／Windows 進行跨平台擴展。這份寫的是 2026-11-14 依 main `e7dd278` 讀得到的現況、現在就能下的判斷，以及**真正要等 #334 的 spike 結果**才能定的部分。支援到哪裡、有什麼證據，看 `README.md` 的「支援平台」。

## 已定案的方向

- 維護者在 #362 定案：跨平台入口工具與後端重構統一使用 Rust，編成各平台的單一執行檔；不採用 Python、Go。
- 11/14 補充：能換 Rust 就換 Rust；換不成，就維持現行方式，或採用該領域的主流做法。順序依 #334 的 spike 結果調整。
- #334 定案：v0.9.0 驗收收尾前，只做格式層與檔案系統兩個 spike，不動正式程式碼；是否在 v0.10.0 正式切換，以 spike 結果為準。

## 現況：哪裡綁定 Linux

依程式與設定檔讀得到的事實；標「推論」的沒有實跑。

| 項目 | 現況 | 在 macOS／Windows 上的影響 |
|---|---|---|
| 服務本體 | 兩個容器（backend、frontend），以 `compose.yaml` 定義，映像內的使用者 uid 1000 | 只要有能跑 Linux 容器的 Docker，本體不分主機平台 |
| 網路 | 預設 `network_mode: host`；另有 `compose.bridge.yaml` 疊加檔 | Docker Desktop 上 `host` 網路不是主機的網路（`script/compose_smoke.sh` 的說明）；要改走 bridge，並給 `CM_HOSTNAME` |
| 入口 | `just` 的 recipe 轉給 `script/` 底下的 bash 腳本（都以 `#!/usr/bin/env bash` 開頭）；腳本需要 bash 4 以上 | macOS 內建 bash 3.2 跑不動；Windows 要另有 bash 環境 |
| 疊加檔的串接 | `script/justfile.docker` 以 `COMPOSE_FILE=compose.yaml:compose.dev.yaml`（冒號）串接 | 依 Docker 的文件，Windows 上 `COMPOSE_FILE` 的分隔符預設是分號；推論：照現在的寫法在 Windows 上串不起來 |
| 目標目錄的掛載 | `${CM_TARGET_ROOT}:${CM_TARGET_ROOT}`，容器內外同一條絕對路徑 | 推論：Windows 的路徑（例如 `C:\…`）不能原樣當成容器內的路徑；Docker Desktop 也只能掛它有分享的目錄 |
| CI | 全部 job 都跑在 `ubuntu-latest` | macOS、Windows 都沒有任何自動化檢查 |

## 逐項評估

「依賴 spike」只標真正要等 #334 結果才能定的部分；其餘現在就能判斷或動手，只是可能有別的前置條件，另外寫明。

### 1. 入口工具改成各平台的單一執行檔

- **依賴 spike：是。** 維護者的定案是入口工具與後端「統一」使用 Rust，而後端是否換成 Rust 要看 #334 的 spike。spike 不過時，「換不成就維持現行方式或採用主流做法」也適用在入口工具上。
- 一併依賴 spike 的：#365（發版時附各平台的單一執行檔）、#349（移除 `src/config_manager/` 包層）。
- 現在能做的：把入口工具必須提供的行為列成清單（建置、啟動、停止、備份、還原、啟動前的檢查），作為不論用什麼語言都要對得上的規格。

### 2. 網路模式在 Docker Desktop 上的處理

- **依賴 spike：否。** 要自動切到 bridge，還是維持「使用者明確選用」（`compose.bridge.yaml` 的說明：預設 host，bridge 要明確選用，不變式 4），是產品行為的決定，與入口工具用什麼語言無關。
- 另有前置：#361 的單一入口與 `/config_manager` 路徑前綴會改動埠與入口的設計；bridge 的埠對應要跟著它定。
- 現在能做的：照現行做法把 Docker Desktop 的 bridge 路徑寫清楚（README 已寫），並在 #348 實跑驗證。

### 3. 路徑與權限

- **依賴 spike：否。** Windows 路徑與容器內路徑的對應、Docker Desktop 的檔案分享範圍、uid 1000 寫出的檔案在主機上的擁有者，都是 Docker 與主機的行為，與入口工具的語言無關。
- 現在能做的：在 Docker Desktop 上實際量一次（目標目錄放在分享範圍內、寫出後在主機上看擁有者與權限）。需要一台可以使用的 Docker Desktop 主機，見 #348 的實跑方案。

### 4. 疊加檔串接的分隔符

- **依賴 spike：否。** 依 Docker 的文件，`COMPOSE_PATH_SEPARATOR` 可以指定分隔符，或改用多個 `-f`；做法與語言無關。
- 但只在入口能在 Windows 上執行時才有意義，所以實作的時機跟著第 1 項。

### 5. CI 的多平台檢查

- **依賴 spike：部分。**
  - 「入口工具在 macOS、Windows 上能不能執行」的檢查：要等第 1 項定了用什麼寫，才知道要測什麼。
  - 「服務在 Docker Desktop 上起不起得來」的檢查：與 spike 無關，但受 runner 限制。推論（依 GitHub-hosted runner 的映像說明，沒有在這個 repo 實測）：macOS runner 沒有內建 Docker，Windows runner 的 Docker 跑的是 Windows 容器，兩者都不能直接跑這裡的 Linux 容器。#356 已確認目前沒有使用 self-hosted runner 的打算。
- 現在能做的：Linux 上照 README 自動實跑一次（#348 的一次性 CI 驗證）；Docker Desktop 的實跑先用實機。

## 結論：要不要擴展

| 範圍 | 判斷 | 依據與條件 |
|---|---|---|
| Linux（`host`） | 維持為主要平台 | CI 全部跑在這裡；真人照 README 操作仍未驗證（#348） |
| macOS、Windows 透過 Docker Desktop（bridge） | 要支援，以「照文件可用」為目標 | 路徑已存在（`compose.bridge.yaml`）；要先在 #348 實跑，並處理第 3、4 項 |
| 入口工具在 macOS、Windows 上原生執行（不需要 bash） | 方向已定（Rust 單一執行檔），時程與做法等 #334 | 第 1、5 項 |
| 服務本體不經容器、直接在 macOS／Windows 上執行 | 不在這次評估的範圍 | 現行與已定案的方向都是容器部署 |

## 還沒定、要等的

- 依賴 #334 spike：第 1 項（入口工具的語言與時程，連同 #365、#349）、第 5 項的入口工具那一半。
- 依賴 #361：第 2 項的埠與入口。
- 依賴實機：第 3 項、#348 的 Docker Desktop 實跑。

spike 的結果出來之後，更新這份文件的第 1、5 項與結論表。
