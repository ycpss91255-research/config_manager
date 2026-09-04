# 設計文件一致性審查 — `main` 全部 commit

> 審查範圍：`main` 至 `424e7c3` 的 78 個 commit。
> 對照基準：`config-manager-design.pdf` 0.23 版的 §0（不變式／設計原則／實作規範／ADR 規範／優先序）、
> §3.6（測試分類法）、§3.7.2（測試介面一覽，**權威表**）、§3.7.3（驗收層）、§3.7.4（覆蓋率審計）、§8.2（驗收檢查點），
> 以及 `doc/PRD.md`、`doc/TEST-PLAN.md`、`CONTEXT.md`。
>
> **本文件只記錄，不修。** 每個偏離各自進自己的 PR，各自帶紅燈與突變檢查——
> 混在一次大改裡，沒有一條修正能證明它真的修好了什麼。

---

## 執行證據

依 ADR-00000027，全部檢查在容器內執行（`./script/test.sh`）。主機不是證據。

| 執行 | 結束碼 | 結果 |
|---|---|---|
| `./script/test.sh` | **128** | 停在 `lint_paths`：`fatal: not a git repository`（#103）。**pytest 與 bats 因此完全沒跑到。不是綠燈。** |
| `./script/test.sh --lint ruff` / `mypy` / `pylint` | 0 | ruff 乾淨；mypy `src/config_manager/core` 7 檔無誤；pylint `src` 10.00/10 |
| `./script/test.sh --lint adr` | 0 | 28 份 ADR，0 failure、0 warning |
| `./script/test.sh --lint portability` | 0 | 27 支腳本，0 violation |
| `./script/test.sh --lint commit` | **0** | 畫面上有 `fatal: not a git repository`，卻以 0 結束 → **偏離 D2** |
| `./script/test.sh --lint paths` | 128 | 訊息只有 git 的 `fatal:`，不含三要素 |
| `./script/test.sh --level unit` | 0 | pytest 45 則 ＋ bats 44 則（T19 守門腳本規格） |
| `./script/test.sh --level integration` | 0 | pytest 29 則 ＋ bats 11 則（entrypoint） |
| `./script/test.sh --level system` | **5** | `no tests ran` — `test/pytest/system/` 是空的 |
| `./script/test.sh --level acceptance` | **5** | `no tests ran` — `test/pytest/acceptance/` 是空的 |
| 容器內 `pytest test/pytest --cov=src/config_manager/core` | 0 | 74 passed，`core/` 覆蓋率 **97.29%**（下限 85） |

未執行到的：`test/bats/runtime/`（T9／T10）——它不在 `script/test.sh` 的 `LEVELS` 裡，
只由 `docker build --target runtime-test` 執行（CI 的 `build` job）。本次審查未建置該階段。

---

## 一、測試介面對照（PDF §3.7.2 權威表）

「層級」欄一律以 **PDF §3.7.2** 為準；「實際落點」為 repo 現況。
`test/pytest/{unit,integration,system,acceptance}/` 是 §3.6.1 軸 2 唯一的四個層級。

| 代號 | 測試介面 | PDF 層級 | 有測試？ | 實際落點 | 層級對？ | 核心行為逐項相符？ |
|---|---|---|---|---|---|---|
| T1 | 清單檔載入與寫回 | Unit | ✅ | `test/pytest/unit/test_config_list.py`（560 行） | ✅ | 六種完整性錯誤全部具名並斷言；清單檔原樣寫回逐位元組斷言。**「寫回」只支援未改動與新增**，改動／移除既有條目丟 `DumpMismatch`（刻意的大聲失敗，見 D14） |
| T2 | 狀態判定 | Unit | ✅ | `test/pytest/unit/test_state.py` | ✅ | 三種狀態＋順序皆斷言。PDF 寫「四種狀態」，實作只判三種（「未納管」由清單成員判定）——TEST-PLAN 與程式碼註解都寫下了理由，屬刻意 |
| T3 | 驗證 | Unit | ❌ | — | — | 未落地（#16）。TEST-PLAN 標「未落地」，一致 |
| T4 | 白名單判定 | Unit | ❌ | — | — | 未落地（#11） |
| T5 | 身分推導 | Unit | ✅ | `test/pytest/unit/test_identity.py` | ✅ | 名稱推導四條、uid 由注入時刻決定（期望值手算寫死）、嚴格遞增、同毫秒遞增 1 皆斷言 |
| T12 | 型別推斷與人工指定 | Unit | ❌ | — | — | 未落地（#9） |
| T13 | 編輯階段生命週期 | Unit | ⚠️ 部分 | `test/pytest/unit/test_session.py` | ⚠️ | 只有**身分**那一半（8 則）。階段的 `acquire`／`renew`／`release`／`sweep`、注入時鐘、開發／部署模式差異全部未落地（#33）。而測試檔放在 `test/pytest/unit/`，該目錄在 §3.6.2 定義為「`core/` 的隔離測試」，被測模組卻是 `api/session.py`（PDF 圖 4 把「使用者身分」畫在 api，故分層本身合規；問題是這塊純邏輯因此落在 `mypy --strict` 與覆蓋率下限的範圍之外，#97） |
| T14 | 參數索引與搜尋 | Unit | ✅ | `test/pytest/unit/test_index.py` | ✅ | 展平、陣列索引、三種搜尋範圍、範圍互斥、`reindex`、`unindex`、查無結果回空清單皆斷言。**未知的 `scope` 靜默回空清單，且該行未被覆蓋**（見 D6） |
| T16 | config 屬性與群組 | Unit | ❌ | — | — | 未落地 |
| T18 | 草稿與進版 | Unit | ❌ | — | — | 未落地（#18） |
| T17 | 角色權限 | Unit | ❌ | — | — | 未落地。`api/session.ROLES` 只有角色集合，沒有 `permits(角色, 動作)` |
| T6 | 解析與原樣寫回 | Integration | ❌ | — | — | 未落地（#17） |
| T7 | 變更紀錄 | Integration | ✅ | `test/pytest/integration/test_git.py` | ✅ | 記錄／作者／訊息格式／類型過濾／退版不截斷歷史／退版內容還原，全部透過 `history()` 觀察，沒有直接跑 `git log`（§3.7.6 反模式一避開） |
| T8 | 原子寫出 | Integration | ✅ | `test/pytest/integration/test_writer.py` | ✅ | 內容、mode、owner／group、覆蓋、中斷不留半殘檔、realpath 逃逸被拒且未寫出、不可寫目錄訊息含三要素——逐項相符 |
| T15 | 啟動前置檢查 | Integration | ✅ | `test/pytest/integration/test_preflight.py` ＋ `test/bats/integration/entrypoint.bats` | ✅ | 11 條 bats ＋ 5 則 pytest。TEST-PLAN 的 11 條行為中，**「白名單設定檔缺失」** 隨 v0.2.0（#11）尚未加入，已在 #66 寫明 |
| T9 | HTTP 端點 | **System** | ⚠️ | `test/bats/runtime/api.bats` | **❌** | `test/bats/runtime/` 不是 §3.6.1 的層級；`test/pytest/system/` 是空的。規格只由 `docker build --target runtime-test` 執行，`./script/test.sh` 跑不到（見 D3）。行為涵蓋 3 條（空清單、三狀態、session 往返＋422），TEST-PLAN 列的「進版原子性」「批次回滾」「第二個編輯階段被拒」「逾時釋放」皆未落地 |
| T10 | CLI | **System** | ⚠️ | `test/bats/runtime/api.bats` | **❌** | 同上。兩條：`list` 走 `--api`、服務未起時非零碼且訊息可行動 |
| T11 | 瀏覽器端到端 | **System** | **❌** | — | — | **零覆蓋。** `web/index.html` 的 `data-testid` 沒有任何測試在用（#99） |
| T19 | 守門腳本的規則 | （TEST-PLAN 新增，非 PDF） | ✅ | `test/bats/unit/*.bats` | ✅ | 5 支腳本各有規格，44 則。PDF §3.7.2 只有 T1–T18；T19 是本 repo 新增的工具層 |
| T20 | 內容雜湊 | （TEST-PLAN 新增） | ✅ | `test/pytest/integration/test_digest.py` | ✅ | 期望值以 coreutils `sha256sum` 取得，不用 `hashlib` 重算（避開 §3.7.6 反模式二） |
| T21 | 差異掃描 | （TEST-PLAN 新增） | ✅ | `test/pytest/integration/test_scan.py` | ✅ | 六條逐項相符 |

### 驗收層（PDF §3.7.3）

| 代號 | 旅程 | 狀態 |
|---|---|---|
| A1 | 把一份新 config 納入管理 | ❌ 零覆蓋（納管流程 #12／#14 未落地） |
| A2 | 調整一個參數並確認生效 | ❌ 零覆蓋 |
| A3 | 發現並處置一次偏離 | ❌ 零覆蓋 |
| A4 | 出錯後能自行修正 | ❌ 零覆蓋。**訊息可行動性目前只在 unit／integration 層逐則斷言，沒有任何驗收層測試** |
| A6 | 開發者整理一份新納管的 config | ❌ 零覆蓋 |
| A5 | 未受訓者可自行操作（人工） | — v0.9.0 的條件，尚未到期 |

`test/pytest/acceptance/` 只有 `.gitkeep`。`./script/test.sh --level acceptance` 結束碼 5。

### 目錄結構對照（PDF §3.6.2）

| PDF 要求 | 現況 | 判定 |
|---|---|---|
| `test/pytest/{unit,integration,system,acceptance}/` | 四個都在 | ✅（`system` 與 `acceptance` 是空的） |
| `test/lint/{ruff,mypy,pylint,hadolint}/` | 四個都在，皆只有 `.gitkeep` | ✅ |
| `test/fixtures/` | 在，只有 `.gitkeep` | ✅ |
| `test/reserved/{performance,security,usability,reliability}/` | 四個都在 | ✅ |
| （PDF §3.6.2 的樹裡沒有 `test/bats/`；§3.3.6 的內文寫了 `test/bats/smoke/`） | `test/bats/{unit,integration,smoke,runtime}/` | ⚠️ `smoke` 與 `runtime` 都不是層級（見 D3） |

---

## 二、§0 逐條核對

### §0.2 產品不變式

| # | 不變式 | 判定 | 證據 |
|---|---|---|---|
| 1 | 每個容器只跑一個服務 | ✅ | `compose.yaml` 兩個服務各一個程序；`runtime-test` 與工具映像是工作階段容器，無 restart 策略 |
| 2 | 絕不靜默失敗 | ⚠️ **四處違反** | D2（lint 跑不了卻回 0）、D6（未知搜尋範圍回空清單）、D7（任何例外被重貼為「清單檔無法解析」）、D8（`suppress(OSError)`）。其餘處置良好：`digest` 分「不存在」與「讀不出來」、`scan` 對來源消失丟具名例外、`dump` 對不支援的改動大聲失敗 |
| 3 | 可擴充為多實例 | ✅ | `groups: list[str]` 是多對多 tag 非樹狀；身分拆 `uid`／`name`／`hostname` 三層（`core/models.py`） |
| 4 | 預設值落向安全 | ✅ | CORS 預設只放行 compose 的前端而非 `*`（`routes.py:37`）；`SessionInput.role` 預設一般使用者；`network_mode: host` 的理由寫在 compose 內；解除納管保留 target（尚未實作，設計已定） |
| 5 | 預設值由兩個問題決定 | ✅ | 「開啟畫面時掃描差異」預設開（`index.html` 載入即 `load()`）；沒有定期背景掃描 |
| 6 | 一個真實來源 | ✅ | `routes.list_configs` 每次請求重新掃描、不快取；`api/cli list` 走 HTTP 不自己讀清單檔；`preflight.read_config_list` 是清單檔位置的唯一知情者 |
| 7 | 維持測試門檻 | ⚠️ | `core/` 97.29% ≥ 85 ✅；但 System 與 Acceptance 兩層是空的、T11 零覆蓋、`io/`／`api/` 無任何覆蓋率或 `--strict` 數字（#97） |
| 8 | 身分與命名來自檔案 | ✅ | `format` 來自清單檔不由副檔名推斷（`ALLOWED_FORMATS` 只做白名單比對）；`permissions` 明文；`hostname` 是欄位。`CM_CONFIG_REPO` 是**掛載位置**不是命名，不落在本條 |
| 9 | 文件由程式碼推導 | ❌ **違反** | D4（覆蓋率審計表宣稱 17 個 `.py` 實際 20、`api/errors` 缺列、`api/session` 標「未落地」但已落地）、D11（CHANGELOG 停在 #67） |

### §0.3 設計原則

| 原則 | 判定 | 說明 |
|---|---|---|
| N-1 Raw 檔案的讀者是程式 | ✅ | 清單檔 TOML；`digest` 比對原始位元組而非解析後資料 |
| N-2 偵測與提示，不做自動修正 | ⚠️ | 主體正確（`session._checked` 拒絕 `<` 而非清洗，並寫下理由）。**但 D6 的「無法辨識的範圍靜默降級」正是 N-2 推論禁止的形態** |
| N-3 保證來自後端 | ✅ | frontend 容器無任何掛載、無檔案系統與 git 存取；驗證全在後端 |
| N-4 核心邏輯不依賴 I/O | ✅ | `core/` 五個模組零 I/O import；`state.decide` 收算好的雜湊；`identity.new_uid` 收注入的時刻。`mypy --strict` 對 `core/` 全覆蓋 |
| N-5 每個介面操作都有 CLI 對等 | ✅（目前範圍） | `list` 走與瀏覽器相同的 `GET /api/configs`，並以 `--api` 斷言它真的走了 HTTP |
| N-6 可逆性優先 | ✅ | `git.revert` 以 `checkout <version> -- <source>` ＋ 新紀錄實作，不 `reset`、不改寫歷史 |

### §0.4 實作規範

工具檢查得到的六條：

| 規範 | 閾值 | 設定在哪 | 判定 |
|---|---|---|---|
| 巢狀深度 | ≤ 3 | `pyproject.toml` `[tool.pylint.design] max-nested-blocks = 3` | ✅ pylint 10.00/10 |
| 循環複雜度 | ≤ 10 | `[tool.ruff.lint.mccabe] max-complexity = 10` | ✅ |
| 函式長度 | ≤ 50 | `[tool.ruff.lint.pylint] max-statements = 50` | ✅ |
| 參數個數 | ≤ 5 | `max-args = 5` | ✅（`writer.write` 4 個） |
| 型別註解 | `core/` 全覆蓋 | `[tool.mypy] files = ["src/config_manager/core"], strict = true` | ✅ 但範圍只到 `core/`（#97） |
| 禁止吞錯誤 | 無裸 `except:` | ruff `select` 含 `BLE`、`E` | ✅ 無裸 except。**但 `BLE001` 對「捕捉後 re-raise」不觸發，因此 D7 通過了 lint** |
| 覆蓋率 | `core/` ≥ 85% | `fail_under = 85` | ✅ 97.29% |

工具檢查不到的三條——**逐則核對 `src/config_manager/` 每一則丟給使用者看的訊息**：

#### (a) 不得捕捉後僅 `pass` 或僅 `log.debug`

全 repo 只有一處：`src/config_manager/io/writer.py:118` `with contextlib.suppress(OSError): os.unlink(temporary)`。
`contextlib.suppress` 就是 `except OSError: pass`。→ **D8**。其餘 8 個 `except` 全部轉為具名例外向上拋。

#### (b) 面向使用者的錯誤訊息必須含三要素（發生什麼／在哪裡／該怎麼改）

| 位置 | 訊息（摘） | 發生什麼 | 在哪裡 | 該怎麼改 | 判定 |
|---|---|---|---|---|---|
| `core/config_list.py:62` | `dump 尚不支援改動既有條目：{ref} 與原始清單檔內容不符（目前只支援未改動與新增）` | ✅ | ✅ ref | ⚠️ 只說目前支援什麼，沒說使用者該做什麼 | 邊緣（內部契約錯誤，非終端使用者路徑） |
| `core/config_list.py:70` | `dump 尚不支援移除既有條目：uid {removed} 已從清單移除` | ✅ | ✅ uid | ⚠️ 同上 | 邊緣 |
| `core/config_list.py:93` | `無法辨識的欄位「{key}」（第 N 行）；{where} 不接受此欄位` | ✅ | ✅ **行號** | ❌ 沒給允許的欄位集合，也沒說「移除它」 | **不合格** |
| `core/config_list.py:150` | `目標路徑含 ..（逃逸風險）：{ref} 的目標「{target}」` | ✅ | ✅ ref＋值 | ❌ 沒說改成絕對路徑 | **不合格** |
| `core/config_list.py:156` | `format 非允許值：{ref} 的 format「{x}」，允許值為 yaml／json／toml／ini／raw` | ✅ | ✅ | ✅ 列出允許值 | ✅ |
| `core/config_list.py:163` | `uid 重複：{A.ref} 與 {B.ref} 共用 uid「{uid}」` | ✅ | ✅ 兩筆都指名 | ❌ 沒說改哪一筆 | 邊緣（T1 只要求「指出是哪兩筆」，已達成） |
| `core/config_list.py:170` | `目標位置重複：{A.ref} 與 {B.ref} 共用目標「{t}」` | ✅ | ✅ | ❌ | 邊緣（同上） |
| `io/digest.py:39` | `內容讀不出來：{path}（{strerror}）。下一步：確認它是一般檔案、且執行身分有讀取權限` | ✅ | ✅ | ✅ | ✅ |
| `io/preflight.py:47` | `config-repo 裡沒有清單檔：{path}。下一步：確認 CM_CONFIG_REPO 指向正確的掛載，或從備份還原該檔` | ✅ | ✅ | ✅ | ✅ |
| `io/preflight.py:56` | `清單檔讀不出來：{path}（{strerror}）。下一步：檢查該檔的權限與編碼` | ✅ | ✅ | ✅ | ✅ |
| `io/preflight.py:60` | `清單檔無法解析：{path}——{error}。下一步：依訊息指出的位置修正該檔` | ⚠️ 可能不是真的 | ✅ | ⚠️ | **不合格**：捕捉 `Exception`，所以 `load()` 自身的 bug 也會被說成「你的清單檔無法解析」→ **D7** |
| `io/preflight.py:73` | `清單檔引用的來源內容不存在：{ref} 的來源「{source}」不在 {repo} 裡。下一步：還原該檔，或從清單檔移除這筆條目` | ✅ | ✅ | ✅ | ✅ |
| `io/preflight.py:87` | `usage: python -m config_manager.io.preflight <config-repo>` | ✅ | — | ✅ | ✅（全行是指令形式，屬 ADR-00000028 的識別碼例外） |
| `io/git.py:53` | `不是允許的變更類型：{kind}。允許的是 import／cfg／…。下一步：改用其中一個` | ✅ | ✅ | ✅ | ✅ |
| `io/scan.py:36` | `掃描時來源內容不見了：{ref} 的來源「{s}」不在 {repo} 裡。下一步：還原該檔，或從清單檔移除這筆條目` | ✅ | ✅ | ✅ | ✅ |
| `io/writer.py:38` | `找不到使用者：{owner}。下一步：確認這是本機存在的使用者，或改用數字 uid。` | ✅ | ✅ | ✅ | ✅ |
| `io/writer.py:51` | `找不到群組：{group}。下一步：…或改用數字 gid。` | ✅ | ✅ | ✅ | ✅ |
| `io/writer.py:73` | `目標解析後落在允許範圍之外：{target} → {resolved}。下一步：確認該路徑或其父目錄不是指向範圍外的符號連結，或把該位置納入允許的根目錄。` | ✅ | ✅ 原路徑＋解析後 | ✅ | ✅ |
| `io/writer.py:89` | `目標目錄無法寫入：{dir}（{strerror}）。下一步：確認該目錄的權限與擁有者，或以有權限的身分執行。` | ✅ | ✅ | ✅ | ✅ |
| `io/writer.py:106` | `沒有權限把 {target} 設為 {owner}:{group}（{strerror}）。下一步：這份 config 若真的需要別的擁有者，標記 requires_privilege 走提權路徑；否則把 owner/group 改成服務的執行身分。` | ✅ | ✅ | ✅ | ✅ |
| `api/session.py:45` | `角色「{role}」不在允許的值裡：user、developer。下一步：在身分輸入頁選擇其中一個` | ✅ | ✅ | ✅ | ✅ |
| `api/session.py:62` | `{field}是必填的。下一步：在身分輸入頁填寫它` | ✅ | ✅ 欄位 | ✅ | ✅ |
| `api/session.py:67` | `{field}不能含「{c}」——那會破壞變更紀錄的作者欄位…。下一步：移除該字元` | ✅ | ✅ | ✅ | ✅ |
| `api/cli.py:68`（stderr） | `config_manager: 讀不到 {api}/api/configs（{error}）。下一步：確認 backend 已啟動，或以 --api 指定它的位址` | ✅ | ✅ | ✅ | ✅ |
| `api/cli.py:92`（stderr） | `config_manager: CM_CONFIG_REPO 未設定，服務沒有 config-repo 可服務。下一步：設定它指向掛載進來的 config-repo` | ✅ | ✅ | ✅ | ✅ |
| `api/cli.py:76`（stdout） | `還沒有納管任何 config。` | — | — | — | ✅ 非錯誤，是合法空狀態的明說 |

**結論：`src/config_manager/` 的 26 則使用者可見訊息中，20 則合格、3 則不合格（`config_list.py:93`、`:150`、`io/preflight.py:60`）、3 則邊緣。**
`io/` 與 `api/` 兩層品質一致且高（每則都有「下一步」）；不合格的三則全部落在 `core/config_list`
與被 `except Exception` 汙染的那一則。

**`script/` 另計**：`entrypoint.sh` 的 12 則 `die`／`printf` 中，
`could not write ${list}`、`could not commit the initial ${list}`、`git init failed at ${repo}`
三則只有「發生什麼」，沒有原因也沒有下一步 → **D5**。
`lint_paths.sh` 在 git 不可用時完全不印自己的訊息，只留 git 的 `fatal:` → **D2**。
另外**全部 27 支腳本的執行期輸出仍是英文**，違反 ADR-00000028 明訂的範圍——已由 #106／#108 追蹤，不另開。

#### (c) 不得將驗證失敗轉為警告後繼續

| 位置 | 判定 |
|---|---|
| `config_list._check_integrity` 的六項硬錯誤 | ✅ 全部 `raise`，無一降級 |
| `(name, hostname)` 重複 → `warnings` | ✅ 這是 T1 與 PDF §4.3 明定的「警示而非例外」（uid 已保證唯一），屬設計 |
| `ConfigList.warnings` 為 `PrivateAttr` | ✅ 防止由清單檔注入警示 |
| `core/index.search` 未知範圍 → `[]` | ❌ **這是把「輸入不合法」降級成「查無結果」** → D6 |
| `preflight.main` 只接 `PreflightError`，其餘帶 traceback 炸開 | ✅ 刻意，且寫下理由 |

### §0.5 ADR 規範

28 份 ADR，`lint_adr` 0 failure 0 warning。逐項：檔名格式 ✅、無重號 ✅、無跳號 ✅、
`> 服務：` 全有 ✅、三個必要段落全有 ✅、`Status` 全合法 ✅、`Alternatives` 全有 ✅。

**但 lint 只檢查「有沒有寫」，不檢查「寫得對不對」**：
`ADR-00000028`（紀錄一律以中文書寫）回指「不變式 8（身分與命名一次解析，來自檔案）」，
而不變式 8 講的是 `uid`／`name`／`hostname`／`target`／`format`／`permissions` 的來源，
與紀錄語言無關。§0.5 設這一欄的理由正是「使每個決策必須掛在產品目標上」，
掛錯等於這一欄沒有發揮作用 → **D12**。其餘 27 份回指皆成立。

### §0.6 優先序

未發現以 P5（易用性）壓過 P1–P4 的取捨。反例俱在：
`compose.yaml` 選 `host` 網路並寫下理由（P3 勝 P5）、
`dump` 對不支援的編輯大聲失敗而非靜默丟欄位（P2 勝 P5）、
`session._checked` 拒絕而非清洗（P1 勝 P5）。

---

## 三、每個 commit 的判定

「相符」＝ commit 訊息宣稱的與 diff 實際做的一致。

### 設計文件與計畫（3）

| commit | 宣稱 | 實際 | 相符 | 落點 | 測試／層級 | §0 |
|---|---|---|---|---|---|---|
| `195e985` | Add config-manager design package | 加入 `pkg/` 全部設計文件、22 份 ADR、圖表與產生腳本 | ✅ | 設計基準 | — | 訊息為英文祈使句（規則後立） |
| `5765793` | Move design package to repository root | 純 rename | ✅ | — | — | 同上 |
| `581b711` | Confirm and revise test plan after item-by-item review | TEST-PLAN ＋ UI-ELEMENTS 修訂 | ✅ | ADR-00000018 | — | 同上 |

### T1 清單檔（10）：`f46a2c9` `798e98e` `2fcd7e6` `b92d961` `9b01676` `e8b47b5` `39db3a8` `5822197` `bfc577b` `7d7ddb8`

全部相符，每片一個測試＋最小實作，落在 **T1（Unit）**，測試在 `test/pytest/unit/test_config_list.py`，
層級正確。`7d7ddb8` 同時修 TEST-PLAN 與實作（新增「無法辨識的欄位」行為），屬 §3.7 允許的
「議定介面後再實作」。§0 無違反；訊息為英文（規則後立）。

### T1 補強（4）：`02d483e` `c4e75a4` `8352868` `39f616b`

全部相符。`8352868`／`39f616b` 把 dump 的靜默丟失改為 `DumpMismatch` 大聲失敗——**直接服務不變式 2**，
且先改 TEST-PLAN 再寫測試。`02d483e` 是 regression（防止由清單檔注入 `warnings`），
符合 §3.6.1 軸 3 的 Regression 型別。

### T5 身分（7）：`d0c1f3e` `00ca87d` `cf7b74d` `ee9c51d` `17c2c5e` `d7d0064` `8dd189c`

全部相符，落在 **T5（Unit）**。`8dd189c` 只加測試不加實作（釘住字串可排序），
符合「一次一片」。**`derive_name` 對只有一段的路徑會 `IndexError`**（`parts[-2]`），
沒有任何 commit 涵蓋這條 → **D13**。

### 骨架與擴充點（4）：`fa3c9dc` `60e1a07` `0faf167` `a252bbc`

`fa3c9dc` 宣稱「對齊共用容器模板的骨架」，實際建出 Dockerfile／compose／CI／script／
`test/{pytest,lint,fixtures,reserved}` 全部空目錄——與 PDF §3.6.2 的目錄樹逐項相符 ✅。
`0faf167` 補完 §3.3.2 的缺口 ✅。`a252bbc` 宣稱「有呼叫者的就真的接線」，
實際 14 支 hook 中 12 支是空殼——與訊息相符（訊息本身就說了「先佔位」），
並開 ADR-00000023／00000024 記錄 ✅。

### T14 索引（9）：`2280f56` `d3b4067` `27fc4bc` `b221e02` `e474da9` `eb8a841` `5dc3cb5` `84acf64` `571a2da`

全部相符，落在 **T14（Unit）**，層級正確，九片各一個斷言。
`571a2da`「查無結果回空清單而非例外」——**這一片同時讓「範圍打錯」也回空清單**，
而那不是它宣稱的行為 → **D6**。覆蓋率報告顯示 `index.py:53` 從未被執行，佐證。

### 工具收斂（2）：`34fb086` `d3fb3f0` — ruff 修正，相符 ✅

### CI 與 commit lint（5）：`6147dd8` `458b221` `74157f6` `91a0ebe` `2d924aa`

全部相符。`458b221`「缺工具會失敗」直接服務不變式 2 ✅。
`6147dd8` 建立 `lint_commit.sh` 時就內建了 **「base ref 不存在 → 印訊息並回 0」** 的路徑 → **D2 的起點**。

### v0.1.0 主線（20）

| commit | 宣稱 | 相符 | 測試介面／層級 | 判定 |
|---|---|---|---|---|
| `b2ab886` | TEST-PLAN 對齊已交付的簽章 | ✅ | — | 不變式 9 的正確作法：文件跟著程式碼走 |
| `06a3803` | ADR lint 檢查檔名、編號與結構（closes #2） | ✅ | T19（但當時尚無規格，#82／#87 才補） | 見「issue 驗收對照」#2 |
| `82119d9` | 由存在性與內容雜湊判定狀態 | ✅ | **T2 / Unit** ✅ | ✅ |
| `e063ece` | 符合本 repo 佈局的 CLAUDE.md | ✅ | — | ✅ |
| `e7c2bfb` | 單一頂層套件，模組名不再遮蔽 stdlib | ✅ | 全套件 rename ＋ ADR-00000026 | ✅ 服務不變式 2（#56 的靜默失效） |
| `93183f2` | 一份映像跑所有檢查 | ✅ | ADR-00000027 | ✅ |
| `c1387b5` | 檢查在容器內跑，主機不是證據 | ✅ | — | ✅ |
| `c667f31` | 本機執行列出每一支缺席的檢查工具 | ✅ | T19（規格於 `d04f4ce` 補上） | ✅ |
| `235d793` | vendor 共用 agent skills | ✅ | — | ✅ |
| `af4f9c9` | 紀錄一律中文，只有 type／scope 英文 | ✅ | ADR-00000028 | ⚠️ 回指不成立 → D12 |
| `d52c47e` | 容器以呼叫者身分執行＋補回中文主旨檢查 | ✅ | T19 | ✅ 服務不變式 2 |
| `f84269e` | devel stage 修好並納入 CI | ✅ | CI `build` job | ✅ |
| `956a2ec` | 附加的新條目保留三個選填欄位 | ✅ | **T1 / Unit** ✅ | ✅ |
| `9e9beb7` | 中文檢查改用位元組比對 | ✅ | T19 | ✅ 服務不變式 2（`grep -P` 在 BSD 上給錯答案） |
| `51f5d1b` | 檢查映像移到 `docker/` ＋ 路徑 lint | ✅ | T19 | ✅ |
| `0373ef6` | 層級測試把 bats 一起跑 | ✅ | T19 | ⚠️ **`LEVELS` 只列四個層級，`test/bats/runtime/` 與 `smoke/` 因此永遠不被 `test.sh` 跑到** → D3 |
| `6c142fd` | 可攜性守門 | ✅ | T19 | ✅ |
| `bdd651b` | 補完四支守門的規格 | ✅ | T19 | ✅ |
| `deb10c3` | 目標要嘛完整寫入、要嘛完全不動 | ✅ | **T8 / Integration** ✅ | ⚠️ 含 D8（`suppress(OSError)`） |
| `768d183` | 變更紀錄記得住、查得到、退得回 | ✅ | **T7 / Integration** ✅ | ✅ |

### v0.1.0 收尾（11）

| commit | 宣稱 | 相符 | 測試介面／層級 | 判定 |
|---|---|---|---|---|
| `d2ab095` | 六條守門規則各有會觸發它的規格 | ✅ | T19 | ⚠️ T19 與其規格同一 commit 落地 → D10 |
| `8d8c38f` | uid 值釘在注入時刻、參照以完整形式斷言 | ✅ | T5 / Unit ✅ | ✅ 避開 §3.7.6 反模式二 |
| `4e05f1e` | 非空掛載不再被當成首次啟動 | ✅ | T15 / Integration ✅ | ✅ 服務不變式 2 |
| `d5c6d95` | 清單檔與來源內容在服務起來前驗過 | ✅ | T15 / Integration ✅ | ⚠️ 含 D7（`except Exception`）與 D5（entrypoint 三則訊息） |
| `5915043` | 偏離偵測的雜湊有自己的模組與測試介面 | ✅ | T20（新增） / Integration | ⚠️ T20 與其測試同一 commit → D10 |
| `aaec168` | 服務起得來，`GET /api/configs` 走真實 HTTP | ✅ | T9 → `test/bats/runtime/` | ❌ **層級落點不符 §3.7.2（System）** → D3 |
| `4d283b2` | 清單每筆帶得出三種狀態 | ✅ | T21（新增） / Integration | ⚠️ T21 同一 commit → D10 |
| `2beb36b` | 瀏覽器打得開清單，四種狀態看得出來，CLI 看到的是同一份 | ⚠️ **部分不符** | T11 → **無** | 「CLI 看到的是同一份」有 bats 斷言 ✅；「瀏覽器打得開」**零自動化證據**（#99）。訊息說「四種狀態」，頁面只呈現三種——`index.html:212` 自己註明「未納管不會出現在這份清單裡」，故訊息與程式碼註解互相牴觸 |
| `e59e217` | 覆蓋率審計表涵蓋每個模組與腳本 | ⚠️ **當下相符，其後失效** | — | `dfad7f2` 之後 `api/errors` 缺列、`api/session` 標「未落地」→ D4 |
| `d04f4ce` | 缺工具不得靜默通過的每條保證各有案例 | ✅ | T19 / Unit ✅ | ✅ 六條全覆蓋 |
| `dfad7f2` | **身分輸入成為變更紀錄的作者**，角色是自我宣告 | ❌ **不符** | T13 部分 / Unit | 「角色是自我宣告」✅；**「成為變更紀錄的作者」未成立**——沒有任何程式碼把 `Identity.git_author` 交給 `io.git.record` → **D1** |

### 語言與文字（3）

| commit | 宣稱 | 相符 | 判定 |
|---|---|---|---|
| `b6bcbb2` | `docs:` ADR 標題與程式碼註解改以中文書寫 | ⚠️ | 動了 Dockerfile／justfile／pyproject／CI／27 支腳本共 72 檔。若真的只有註解則相符，但 `2b92335` 隨後證明那一批裡有**會被人讀到、且與行為不符**的文字。以 `docs` 為 type 讓那批變更看起來沒有風險 → 記錄，不開 issue |
| `2b92335` | 會執行的文字不再宣稱它沒有的規則與能力 | ✅ | 四處逐一修正，且先補規格再改訊息（紅燈先行）✅。同時把「執行期輸出」補進 ADR-00000028 適用範圍 |
| `424e7c3` | skill 讀得到 issue tracker、標籤與領域文件 | ✅ | ✅ |

### commit 訊息格式

前 **40** 個 commit（`195e985`…`d3fb3f0`）不符 `type(scope): 中文陳述句`：多為英文祈使句或
`T1: …` 前綴。ADR-00000028 的 Consequences 明寫「既有的英文紀錄不回頭改寫 commit 歷史」，
CI 也只檢查 `origin/main..HEAD`。**屬刻意接受，記錄不開 issue。**
`a252bbc` 缺 scope（lint 只 warn）。`b6bcbb2` 的 type 見上。

---

## 四、偏離清單（依嚴重度排序）

| 代號 | 偏離 | 嚴重度 | issue | 依據 | 證據 |
|---|---|---|---|---|---|
| **D1** | `#6` 的驗收條件「輸入的身分成為後續 commit 的 author」未完成，issue 已關閉且 `dfad7f2` 的訊息宣稱它成立 | **高** | #114 | 不變式 2；CLAUDE.md「證據附在 issue 上，勾選框實際勾起來」 | `grep -rn git_author src/` 只有 `session.py:34` 定義與 `routes.py:97` 回傳；`io.git.record` 的呼叫端只有測試 |
| **D2** | `lint_commit.sh` 在 base ref 不存在／git 不可用時**以 0 結束**並宣告 `nothing to check`；`lint_paths.sh` 在同一情況下以 128 中止但只留 git 的 `fatal:` | **高** | #115 | 不變式 2；`test.sh` 自己檔頭寫的 hadolint 教訓 | 容器內 `./script/test.sh --lint commit` → `EXIT=0` 且畫面有 `fatal: not a git repository` |
| **D3** | T9／T10 的規格放在 `test/bats/runtime/`——不是 §3.6.1 軸 2 的任何層級；`test/pytest/system/` 與 `acceptance/` 是空的；`script/test.sh` 的 `LEVELS` 不含 `runtime` 與 `smoke`，所以 `just test` 全綠不代表 T9／T10 跑過 | **高** | #116 | PDF §3.6.1、§3.6.2、§3.7.2（T9／T10／T11 層級＝System） | `script/test.sh:171`；`--level system` 與 `--level acceptance` 實測 EXIT=5 |
| **D4** | `doc/TEST-PLAN.md` 覆蓋率審計表在 `#104` 之後失準：`api/errors.py` **無對應列**、`api/session` 標「未落地」但已落地、`api/routes` 說明漏了 session 端點、宣稱涵蓋 `find src -name '*.py'` 的 **17** 個檔案而實際是 **20** | **高** | #117 | 不變式 9；該表自己寫的「新增一個模組時這裡要一起加一列」 | `doc/TEST-PLAN.md:688-690`、`:704`；`find src -name '*.py' \| wc -l` → 20 |
| **D5** | `script/entrypoint.sh` 三則錯誤訊息不含三要素：`could not write ${list}`（:39）、`could not commit the initial ${list}`（:56）、`git init failed at ${repo}`（:84） | **中** | #118 | §0.4「錯誤訊息必須包含三要素」；§7.1 原則 3；A4 失敗判準 | 同左 |
| **D6** | `core/index.search()` 對未知的 `scope` 回空清單，使「範圍打錯」與「真的查無結果」不可分辨 | **中** | #119 | 不變式 2；N-2「絕不靜默降級為較寬鬆的行為」 | `src/config_manager/core/index.py:53`；覆蓋率報告顯示該行 **Missing** |
| **D7** | `io/preflight.read_config_list` 以 `except Exception` 把任何例外重貼為 `ConfigListUnparsable`，`load()` 自身的 bug 會被說成「你的清單檔無法解析」並把人指去修一個沒壞的檔案 | **中** | #120 | §0.4「禁止吞錯誤」；不變式 2 | `src/config_manager/io/preflight.py:59-62`。ruff `BLE001` 因 handler 內有 `raise` 而不觸發 |
| **D8** | `io/writer.py:118` `contextlib.suppress(OSError)` ＝ 捕捉後僅 `pass`：暫存檔刪不掉時目標目錄留下 `.config_manager-*.tmp`，沒有人被告知 | **中** | #121 | §0.4「不得捕捉後僅 `pass`」（無例外條款） | 同左 |
| **D9** | `GET /api/session` 不在設計 §3.5.3 的端點表裡，而 `routes.py:3` 與 TEST-PLAN T9 都寫著「端點集合由 §3.5.3 的表決定，不在實作時發明」 | **中** | #122 | PDF §3.5.3；ADR-00000010 | `src/config_manager/api/routes.py:71` |
| **D10** | T19／T20／T21 三個測試介面與其第一批測試在**同一個 commit** 落地，ADR-00000018「測試只寫在事先議定的介面上」退化為自我確認 | **中** | #123 | PDF §3.7「沒有經過確認的測試介面，不寫測試」；ADR-00000018 | `d2ab095`／`5915043`／`4d283b2` 的 diff 皆同時含 `doc/TEST-PLAN.md` 與新規格 |
| **D11** | `doc/changelog/CHANGELOG.md` 內容停在 `#67`，其後 18 個 commit（writer／git／preflight／digest／scan／api／web／session）全部沒有進去，且全文英文 | **中** | #124 | 不變式 9「一份看起來權威但已過期的文件，就是不變式 2 的靜默失敗被寫進文件裡」；ADR-00000028 | `git log --oneline -3 -- doc/changelog/CHANGELOG.md` → 最後一次實質更新在 `c667f31` |
| **D12** | `ADR-00000028` 的 `> 服務：不變式 8` 回指不成立——紀錄語言與「身分與命名一次解析，來自檔案」無關 | **低** | #125 | §0.5「`> 服務：` 使每個決策必須掛在產品目標上」 | `doc/adr/00000028-records-are-written-in-chinese.md:3`。`lint_adr` 只檢查該行存在 |
| **D13** | `core/identity.derive_name` 對只有一段的路徑（如 `params.yaml`）丟未捕捉的 `IndexError`，不是具名例外 | **低** | #126 | 不變式 2；T5 | `src/config_manager/core/identity.py:16` `parts[-2]` |
| **D14** | §8.2 v0.1.0 檢查點「修改清單檔後寫出，註解與欄位順序完整保留」尚未成立：`dump` 對改動既有條目丟 `DumpMismatch` | **低** | #127 | PDF §8.2 v0.1.0 | `src/config_manager/core/config_list.py:62`。刻意的大聲失敗（優於靜默丟失），但**檢查點未達成**，而 v0.1.0 的 issue 正在被關閉 |

### 已由既有 issue 追蹤，不重複開單

| 觀察 | 既有 issue |
|---|---|
| `script/` 27 支腳本的執行期輸出仍是英文（ADR-00000028 明訂範圍） | #106、#108 |
| T11 零覆蓋、`web/` 的 `data-testid` 沒有測試在用 | #99 |
| 覆蓋率與 `mypy --strict` 只涵蓋 `core/`，`io/`／`api/` 兩者皆無 | #97 |
| worktree 內 `.git` 是檔案，容器解不開 | #103（但 **D2** 是規則本身的缺陷，不只是環境） |
| README 的「目前狀態」已過期 | #98 |
| 前 40 個 commit 不符中文陳述句 | ADR-00000028 Consequences 明寫不回頭改寫 |

---

## 五、已關閉 issue 的驗收對照

七張帶 `- [ ]` 驗收條件的已關閉 issue，逐條核對結果各自留言於該 issue。摘要：

| issue | 條件數 | 真的完成 | 未完成 |
|---|---|---|---|
| #2 ADR 編號與結構 lint | 9 | 9 | — |
| #3 清單檔讀寫與完整性檢查 | 11 | 11 | — |
| #4 git 操作 | 5 | 5 | — |
| #5 原子寫出＋權限 | 8 | 8 | — |
| #6 身分輸入 | 4 | **3** | **「輸入的身分成為後續 commit 的 author」→ D1 / #114** |
| #63 共用測試映像 | 5 | 5 | — |
| #66 啟動前置檢查（T15） | 6 | 6 | — |

`#2`／`#4`／`#5`／`#6` 關閉時勾選框全部留空，違反 CLAUDE.md「關閉帶驗收條件的 issue 時，
證據附在 issue 上，勾選框實際勾起來」。本次審查已補上對照留言；**`#6` 的第四條不勾**。
