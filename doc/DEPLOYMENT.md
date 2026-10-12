# 部署

把 config_manager 起在一台機器上要知道的事。決策的理由在 ADR-00000031；這份只講怎麼做。

## 起服務（現場）

`compose.yaml` 單獨就是現場用的那一套：正式映像、部署模式、只有本機連得到、當掉會自己起來。

1. 決定這台機器上 config 所在的根目錄，寫進 repo 根目錄的 `.env`（不進版控）：

   ```
   CM_TARGET_ROOT=/opt/robot/config
   ```

   這個目錄要先存在。它同時決定「掛進容器的是什麼」與「白名單放行什麼」。

2. 建置並啟動：

   ```bash
   docker compose build
   docker compose up -d
   ```

   （`just docker build`、`just docker run` 是同一件事。）

3. 在**這台機器上**開 `http://localhost:8081`。

## 只有本機連得到

前端（8081）與後端（8080）都只聽 `127.0.0.1`。從區網上別台機器連不到，這是刻意的：
這個服務**沒有密碼**，身分與角色是自己填的——聽在區網上，等於區網上任何人都能改這台機器的
config。所以沒有「開放區網」的設定可以開。

### 從別台機器操作：SSH 通道

在**你自己的電腦**上開一條通道，把兩個埠都帶過來（頁面在 8081，它會去連同一個主機名的 8080）：

```bash
ssh -N -L 8081:127.0.0.1:8081 -L 8080:127.0.0.1:8080 <使用者>@<那台機器>
```

通道開著的時候，在你自己的電腦上開 `http://localhost:8081`。誰連得進來由 SSH 的認證決定。
關掉那個 `ssh` 就斷線。

## bridge 網路

預設是 `host` 網路，因為 bridge 會讓同一台機器上的 ROS 2 discovery 靜默失效。確定不受影響、
而且需要容器之間隔離時，疊上 `compose.bridge.yaml`：

```bash
CM_HOSTNAME=<這台機器的名稱> docker compose -f compose.yaml -f compose.bridge.yaml up -d
```

（或把 `CM_HOSTNAME` 寫進 `.env` 後 `just docker bridge run`。）

- **`CM_HOSTNAME` 非給不可。** bridge 下容器的主機名每次重建都換，而納管會把主機名寫進紀錄。
  沒給的話 compose 在啟動前就停下，訊息會指名這個變數。
- 埠號對應的主機端一樣只綁 `127.0.0.1`，所以「只有本機連得到」不變，遠端操作同樣走 SSH 通道。
- 同一台機器上其他掛在 docker 預設網路的容器連得到這兩個服務。

## 開發

疊上 `compose.dev.yaml`：開發映像、開發模式、主機的 `src/` 掛進容器、不自動重啟。

```bash
just docker dev build
just docker dev run
```

想讓這台機器上每一道 `docker compose` 與 `just docker …` 都自動帶上它，在 `.env` 寫
`COMPOSE_FILE=compose.yaml:compose.dev.yaml`。

## 重啟之後什麼還在

容器重啟、移除重建、機器重開，結果相同：

| 還在 | 不在 |
|---|---|
| 納管清單 | **還沒進版的草稿** |
| 已進版的來源內容 | 輸入過的身分（要重新輸入） |
| 變更歷史 | 編輯權（沒有人持有，誰先編輯誰取得） |
| 白名單 | |

目標檔案的狀態（一致／偏離／未部署）在重啟後當下重新比對，不是沿用重啟前的結果。

**草稿只留在服務的記憶體裡。** 改到一半要停服務之前，先在頁面上按「進版」；沒進版的改動
重啟後就沒了，救不回來。頁面在儲存草稿時也會說這件事。

留得下來的東西住在 `config_repo` 這個 volume。`docker compose down`（或 `just docker stop`）
**不會**刪它；`docker compose down --volumes` 會——那是唯一真實來源，刪之前先備份。

## 起不來的時候

現場那一套設了 `restart: unless-stopped`：服務當掉、或啟動檢查沒過，docker 會一直重試。
頁面打不開時照這個順序：

1. **看狀態。** `Restarting` 就是起不來、正在重試：

   ```bash
   docker compose ps
   ```

2. **看原因。** 啟動檢查的訊息以 `entrypoint:` 開頭，會說哪裡不對與下一步：

   ```bash
   docker compose logs --tail 50 backend
   ```

3. **停止重試。** 手動停掉的服務不會再被拉起來，日誌也不會繼續被洗：

   ```bash
   docker compose stop
   ```

4. 照第 2 步的訊息修正（最常見：`CM_TARGET_ROOT` 指的目錄不存在、白名單指到容器看不到的路徑），
   再 `docker compose up -d`。

`docker compose stop` 之後服務會一直停著，連機器重開也不會自己起來，直到再 `up` 一次。
