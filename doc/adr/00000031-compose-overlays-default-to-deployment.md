# compose.yaml 的預設給現場部署，其餘用法是疊在它上面的檔案

> 服務：不變式 4（預設值落向安全）、不變式 6（一個真實來源）

- **Date:** 2026-10-12
- **Status:** Accepted
- **Relates to:** ADR-00000024（compose.yaml 是唯一的服務定義）、ADR-00000020（身分是自我宣告）、`#51`（本決定的來由）

## Context

`compose.yaml` 原本的預設值是開發用的：`devel` 階段的映像、開發模式。現場的人照著
`docker compose up` 起服務，忘了改 `CM_STAGE` 與 `CM_MODE`，就會跑成一套沒有閒置逾時、
身分輸入頁還有「記住此裝置」的開發模式——而且沒有任何東西會提醒他。

同一份檔案還有三件沒被決定的事，都在 `#51` 動工前提出、由維護者於 2026-10-12 定案：

- bridge 網路沒有選用的方法（兩個服務寫死 `host`）；
- 後端只聽本機，前端的 `http.server` 卻聽所有網卡——區網上別台機器打得開頁面、連不到後端；
- 機器重開後服務不會自己起來。

## Decision

**`compose.yaml` 是現場部署用的那一份，而且單獨就能用。** 預設：`runtime` 階段、部署模式、
`host` 網路、前後端都只聽 `127.0.0.1`、`restart: unless-stopped`。

**其餘用法各是一份疊加檔**，只寫與 `compose.yaml` 不同的地方，不能單獨用：

| 檔案 | 換掉什麼 | 入口 |
|---|---|---|
| `compose.dev.yaml` | `devel` 階段、開發模式、主機的 `src` 掛進去、不自動重啟 | `just docker dev <動作>` |
| `compose.bridge.yaml` | `bridge` 網路、埠號對應（主機端只綁 `127.0.0.1`）、`CM_HOSTNAME` 必填 | `just docker bridge <動作>` |

**不提供開放區網的開關。** 這個服務沒有密碼，身分與角色是自己填的（ADR-00000020）；聽在
區網上，等於區網上任何人都能改這台機器的 config。要從別台機器操作，走 SSH 通道
（`doc/DEPLOYMENT.md`）——通道的那一端有 SSH 自己的認證。四種組合（預設、加開發、加 bridge、
兩個都加）解出來的設定都只有本機連得到，由 `test/bats/integration/compose.bats` 擋。

**bridge 下 `CM_HOSTNAME` 必填**，沒給就在 compose 解析時失敗。bridge 的容器主機名是容器 ID、
每次重建都換，而納管會把主機名寫進紀錄且之後不再讀（`#178`）；讓它靜默地記下一個下次就
不存在的名字，是不變式 2 禁止的那種失敗。

**疊加檔不是第二份服務定義。** ADR-00000024 要防的是「同一件事寫在兩個地方、其中一份沒人讀」。
疊加檔裡沒有任何一件事與 `compose.yaml` 重覆——服務名、掛載、啟動指令的主體都只在那一份；
疊加檔拿掉之後服務仍然完整，反過來不成立。

## Consequences

- 不帶參數的 `docker compose up`／`just docker run` 起的是現場那一套。開發者要多帶疊加檔，
  或在自己的 `.env`（不進版控）寫一行 `COMPOSE_FILE=compose.yaml:compose.dev.yaml`。
- `script/build.sh` 不再自己寫死預設階段；沒給 `--stage` 時問 compose。
- 只能在那台機器上、或經 SSH 通道操作。這是**刻意的限制**，不是還沒做完的功能：
  要開放區網，前提是先有認證，而那是另一個決定。
- 啟動檢查沒過的服務會被 `unless-stopped` 一直重試。怎麼看原因、怎麼停止重試，寫在
  `doc/DEPLOYMENT.md` 的「起不來的時候」。
- compose 的接線從此有測試介面（`doc/TEST-PLAN.md` 的 T25）：設定解析在工具映像裡跑，
  服務實測在 CI 的 Linux runner 上跑，兩種結果分開報。

## Alternatives

- **維持開發預設，現場用 `.env` 切換。** 忘了切的失敗方式是安靜的，而且落在較寬鬆的那一邊。
- **用環境變數切換 bridge。** bridge 不只換一個值：還要埠號對應、容器內的監聽位址、強制
  `CM_HOSTNAME`。compose 的變數替換做不出「多一段 `ports`」，做得出來的寫法會把預設那條路
  也弄得難讀。
- **提供 `CM_BIND_HOST` 之類的開關，預設關。** 開關存在本身就是一個會被打開的東西，而打開它
  不需要理解「沒有密碼」這件事。等有了認證再談。
- **bridge 用 compose 自己的專案網路（拿掉 `network_mode`）。** 隔離比預設的 `docker0` 好，
  但疊加檔要用 `!reset` 才拿得掉基底的 `network_mode: host`，那要較新的 compose；
  `network_mode: bridge` 每一版都認得。同一台機器上其他掛在 `docker0` 的容器連得到這兩個
  服務——那仍然是「本機」，記在這裡。
- **前端加 `init: true` 讓 tini 轉送 SIGTERM。** 停得掉，但 PID 1 就不再是服務本身；
  改停止訊號（`stop_signal: SIGINT`）一行就夠，而且不多一個程序。
