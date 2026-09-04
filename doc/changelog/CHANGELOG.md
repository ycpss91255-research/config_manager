# 變更紀錄

格式：[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)。
版本依設計文件 §8 的里程碑階梯。

`Added` ／ `Changed` ／ `Fixed` ／ `Notes` 這幾個小節名維持英文——它們是 Keep a
Changelog 這個格式的識別碼，判準與 ADR-00000028 對 `type`／`scope` 的處理相同。
**其餘一律中文**（ADR-00000028：專案文件）。

> **這份檔案怎麼寫，以及它的已知弱點。**
>
> **粒度是能力，不是 commit。** 一條 bullet 可以涵蓋數個 commit（io 層那一條涵蓋
> 五個），也有 commit 不該進來——純重構、純格式、只改變「哪些行為有證據」的測試
> 工作放 `Notes`。bullet 說的是**那個變更防的是什麼失效形態**，不是它改了哪些檔案；
> 後者 `git log` 講得比這裡好。
>
> **寫的時機是 PR 合併時**，與該 PR 的 issue 編號一起。
>
> **沒有任何工具檢查這份檔案跟上了。** `git log` 與這裡的 bullet 之間沒有機器可讀的
> 關聯——粒度刻意不是一對一——所以算不出「缺口」；而「動到 `src/` 就要求動到本檔」
> 這種路徑檢查，檢查的是它有沒有被改、不是改得對不對，還會誘發為了轉綠而補一行
> 敷衍的紀錄。**這道缺口是真的，2026-09-05 補寫的那一次落後了 37 個 commit。**
> 依不變式 9，正確的處置可能是讓這份檔案不再需要手寫——**那是 owner 層級的決定，
> 記在 #142**，不在寫這份補寫的 PR 的權限內。

## [Unreleased]

### Added

- repo 骨架：多階段 `Dockerfile`（`sys` / `devel-base` / `devel` / `runtime` /
  `runtime-test`）、手寫的 `compose.yaml`、`script/` 的任務入口、以及 `just`
  的命令模型。
- `test/` 底下的三軸測試樹（靜態分析／層級／型別），保留的非功能性格位以有文件
  說明的空目錄留著。
- lint 閾值從散文搬進 `pyproject.toml`，搬到工具檢查得到的地方。
- CI：lint、帶覆蓋率的 pytest、以及 `runtime-test` 映像的建置。
- `script/prune.sh` 與它的 `just docker prune` recipe，補上與 §3.3.2 所列
  wrapper 集合之間的缺口。
- `doc/test/TEST.md`：怎麼跑測試，以及三軸的每一格涵蓋什麼，包含刻意留空的格位
  與其理由。
- `script/hooks/{pre,post}/` 七個動作全部建立，透過 `script/hooks/dispatch.sh`
  接到五支自建的 wrapper 上，所以放進去的 hook 真的會執行。§3.3.4 指名的兩處
  內容從 wrapper 裡搬進 `pre/run.sh` 與 `post/build.sh`。沒有呼叫者的擴充點
  在檔案裡自己說明（ADR-00000023）。
- `script/local/` 與它的 `cfg` 命令組，由根 justfile 註冊。它轉發的 CLI 隨
  v0.1.0 的 API 一起到；在那之前它以一則說清楚這件事的訊息失敗。
- `.setup.conf` 作為不帶任何服務值的佔位，加上導入模板時的順序步驟——過程中
  不會有兩份同時存活的服務定義（ADR-00000024）。
- ADR-00000023 與 ADR-00000024。
- shellcheck 掃過全部 23 支 shell 腳本，進 `just test lint shellcheck` 與 CI。
  它在引入它的那支 driver 裡找到一個真的字詞分割 bug。
- `ci-rollup` job：其餘每個 job 都通過它才通過，所以分支保護只需要指名一個檢查，
  日後加 job 不必回頭改保護規則。
- actionlint 掃過 `.github/workflows/`，進 `just test lint actionlint` 與 CI。
  **workflow 的表達式不是 YAML，沒有任何 YAML parser 會檢查它**：`${{ }}` 裡
  一個雙引號字串字面值是合法的 YAML、卻是非法的表達式，於是 GitHub 拒收整個
  檔案、跑零個 job、回報一次沒有 job 可以打開的失敗。`ci-rollup` 第一次推上去
  就是這樣壞的。
- `docker/Dockerfile.test-tools`：一份帶齊每一支檢查工具的映像（Python 3.11、
  ruff、mypy、pylint、pytest、shellcheck、hadolint、actionlint、bats、git）。
  `script/test.sh` 偵測到自己不在裡面時就轉進去重跑，CI 只呼叫同一支腳本、
  不安裝任何東西，所以一項檢查不可能在一邊過、在另一邊失敗
  （ADR-00000027，closes #63）。
- `CM_TEST_LOCAL=1` 先盤點主機，一次指名**全部**缺少的檢查工具，而不是撞到第一個
  就中止。撞到第一個就中止會把一次盤點變成裝一個、重跑、撞下一個，而且那次執行
  從頭到尾沒有說出它沒檢查什麼——那正是這道守衛存在的理由。這是逐條驗證 #63 的
  驗收條件而非假設它們成立時發現的；CLAUDE.md 在修好之前就已經宣稱了修好的行為。
- **io 層落地。** 原子寫出（`io/writer.py`，目標要嘛完整寫入要嘛完全不動，
  寫暫存檔 → 落盤 → 設權限 → 改名，#85）、變更紀錄（`io/git.py`，記得住、查得到、
  退得回，退版以反向變更實作、不改寫歷史，#86）、啟動前置檢查（`io/preflight.py`，
  清單檔與來源內容在服務起來之前就驗過，#91）、內容雜湊（`io/digest.py`，偏離偵測
  要讀檔算雜湊，期望值取自 coreutils `sha256sum` 而非 `hashlib`，#93）、
  差異掃描（`io/scan.py`，清單的每一筆帶得出一致／偏離／未部署，#96）。
- **api 層落地。** 服務起得來，`GET /api/configs` 走真實 HTTP 回得了話（#95）；
  身分輸入成為變更紀錄的作者，角色是自我宣告（`api/session.py`，#104）。
- **web 的第一個畫面。** 瀏覽器打得開清單，四種狀態看得出來，而 CLI 看到的是
  同一份（#101）。
- `script/lint_paths.sh`：擋下大小寫不敏感的檔案系統上會撞名的路徑。它是把檢查
  映像搬到 `docker/` 時一併加的——那次撞名讓根目錄的 `Dockerfile` 簽不出來（#81）。
- `script/lint_portability.sh`：擋下只有 GNU 版本吃得下的指令選項，讓腳本在 BSD
  與 GNU 兩邊都成立（#83）。
- `script/lint_messages.sh`：面向使用者的錯誤訊息缺三要素（發生什麼、在哪裡、
  該怎麼改）會被擋下。設計 §0.4 要求那三要素，同一節又寫著「無法自動檢查的規範
  等同不存在」——在這支腳本之前沒有工具檢查它，所以依它自己的規則，那一條先前
  並不存在。同一批把 `core/config_list` 的七則訊息各補上「下一步：」（#128）。
- `script/lint_checkpoints.sh`：驗收條件沒有勾完、勾了卻沒有記 commit、記的東西
  在 git 歷史裡找不到，三種情況的 PR 都過不了 CI。它需要 GitHub API，所以不進
  `script/test.sh`（那支在容器裡跑、沒有 token），而是 CI 的獨立 job（#111）。
- 層級測試把 bats 規格一起跑，並補上第一支 lint 規格（#82）。

### Changed

- **紀錄一律以中文書寫**，只有機器讀的 `type` 與 `scope` 維持英文
  （ADR-00000028，#75）。ADR 標題與程式碼註解隨後跟上（#105）。
  這份檔案自己的轉換排在本次補寫的前一個 commit。
- 檢查用映像從根目錄的 `Dockerfile` 移到 `docker/Dockerfile.test-tools`（#81）。
- **測試層級只有設計 §3.6.1 的四個**：`test/bats/runtime/` 與 `test/bats/smoke/`
  都不是層級——`runtime` 講的是誰執行它，`smoke` 是軸 3 的型別，型別寫在檔名裡。
  T9 與 T10 的規格改用 pytest 落到 `test/pytest/system/`，`api/` 的覆蓋率因此
  才量得到（先前 `api/routes.py` 與 `api/cli.py` 都是 0%，不是沒測，是測它們的
  規格不由 pytest 執行）。`script/test.sh` 每次執行都把沒跑到的規格與層級逐條
  列出來（#137）。
- CLAUDE.md 寫清楚 force push 的禁令範圍只到「別人手上有的分支」；自己的 feature
  branch 在 rebase 之後以 `--force-with-lease` 推回去是正常且必要的。先前寫成
  「不得 force push」，兩個獨立的工作流各自把它讀成連自己的分支都不能推，
  於是把該 rebase 的 PR 留在落後狀態——**規則的範圍沒寫清楚，等於規則沒寫**（#139）。
- 共用的 agent skills 改為 vendor 進 repo，所有人拿到同一組（#68）；
  skill 讀得到這個 repo 的 issue tracker、triage 標籤與領域文件（#109）。

### Fixed

- `just docker *` 與 `just test *` 每一次呼叫都是壞的：module recipe 以該 module
  的目錄為 cwd，所以 `./script/<name>.sh` 這些路徑從來沒有解析成功過。改走
  `{{justfile_directory()}}`——不論 recipe 住在哪個 module，那都是根 justfile 的目錄。
- `script/lint_commit.sh` 與 `just test lint commit`：本 repo 的 commit 訊息沿用
  ycpss91255-docker/base，而規則是從 base 的 200 筆 commit 取樣得出的，不是憑記憶
  複述。未知的 type 或格式錯誤的前綴 fail，缺 scope 或大寫主旨 warn，而長度與
  issue 回指**刻意都不檢查**——傳統的 50 字上限會擋掉它想對齊的那個 repo 的絕大多數
  commit。範圍限於 `origin/main..HEAD`，既有歷史不動（ADR-00000025）。
- CI 連續六次全紅而沒有任何東西說出來：lint job 把每支工具各自列成一個 YAML step，
  於是 `just test lint` 與 CI 是兩組不同的檢查，而 `just test lint` 還額外把
  「hadolint 不在」當成靜默通過。lint job 改為呼叫 `./script/test.sh --lint`
  ——與 `just test lint` 同一個入口——而工具缺席改為中止，不再安靜地通過
  （`CM_LINT_ALLOW_MISSING=1` 在本機把它降級為大聲的警告）。base 在
  `script/test/drivers/hadolint.sh` 裡就記著這個盲點；本 repo 一天之內重現了它。
- `FROM ${BASE_IMAGE}` 的 `DL3006`：hadolint 解析不了 build-ARG 的映像參照，
  因此假設它沒有 tag。以明寫的補償控制忽略——上面兩行的 ARG 預設值有明確的 tag。
- **四支守門在自己跑不了的時候大聲失敗，不再回報通過。** `lint_commit` 在不是 git
  repo 的地方印一句 `fatal:` 然後回 0——一次什麼都沒檢查的執行被讀成綠燈，正是
  `test.sh` 檔頭記著的 hadolint 教訓，這次出現在守門腳本自己身上。實測後發現不只
  一支：`lint_adr` 與 `lint_portability` 在要掃的目錄不存在時同樣回 0，`lint_paths`
  以 git 的 128 中止但訊息是 git 的原文。四支都是把「檢查不了」當成「沒有東西要
  檢查」。四支現在都以非零碼結束，訊息含三要素（#131）。
- **帳本改記 commit 主旨，不記 SHA。** SHA 不是穩定識別碼：rebase 改寫它，squash
  合併讓它從歷史裡消失，而這個 repo 三者都規定了——每個 PR 都必然經歷至少一次
  SHA 改寫，實際效果是每次合併前手動重填一輪帳本。issue 的辨識同時改為只認
  GitHub 的關閉關鍵字，裸的 `#NNN` 是引用不是交付承諾（#132、closes #129）。
- 容器以呼叫者的身分執行，並補回被它清掉的中文主旨檢查（#77）。
- `devel` stage 修好並納入 CI，套件漂移改為記錄而非否認（#78）。
- 清單檔附加新條目時保留 `schema`、`requires_privilege` 與 `permissions`——
  先前 round-trip 寫回會把這三個選填欄位掉掉（#79）。
- 中文檢查改用位元組比對，在 BSD 與 GNU 兩種 grep 上都成立（#80）；
  句尾檢查連中文句號一起擋（#84）。
- entrypoint：非空的掛載目錄不再被當成首次啟動而自動初始化（#89）；
  首次啟動失敗的三則訊息帶出原因、位置與下一步（#134）。
- **會執行的文字不再宣稱它沒有的規則與能力。** 四處人會讀到的字與實際行為不符，
  都是同一種形狀——介面宣稱了它沒有的東西：`lint_commit` 的錯誤訊息還在教人寫
  lowercase 主旨（中文沒有大小寫），`prune.sh --all` 說移除本專案建置的映像但只刪
  兩個（實際有四個），`cfg.sh` 的守衛檢查一個自 #95 起就存在的檔案所以恆真通過，
  `justfile.cfg` 列著四個 CLI 一個都沒實作的 verb（#107）。
- `core/index`：搜尋範圍不在允許集合內時丟具名例外，不再回空清單——「查無結果」
  與「你問錯了」是兩件事（#135）。
- `io/preflight`：只有清單檔真的有問題才被說成無法解析，其餘帶 traceback 炸開，
  不再被裸的 except 吞掉（#136）。
- `io/writer`：暫存檔清不掉時大聲說，而且在同一則訊息裡帶著原本的失敗——
  清理失敗不該把它要回報的那個錯誤蓋掉（#138）。

### Notes

- 組織共用的容器模板（`ycpss91255-docker/base`）在 v0.10.0 **不導入**，這是一個
  決定（設計附錄 A）。現在就採用的是那些反悔代價很高的選擇：stage 名稱、
  烤進映像的產物放 `/opt`、`host` 網路、`.local` 覆蓋後綴、`just` 命令模型、
  ADR 格式、Conventional Commits。
- 里程碑 v0.1.0–v0.10.0 帶著設計文件 §8.2 的驗收檢查點，以及依 §8.1 能力矩陣
  切出的 54 張 issue，每張各自帶驗收條件。§0.7 把驗收條件放在 issue 而不是文件裡，
  所以文件的 §8 成為一個指向 GitHub 的指標，而不是第二份會過期的副本。
- **測試與文件（不改變行為，但改變了「哪些行為有證據」）。** 四支守門腳本補完
  規格並修掉它找到的中文句號漏洞（#84）；六條從未被執行過的守門規則各自有了會觸發
  它的規格（#87）；`uid` 的值被釘在注入的時刻上，參照以完整形式斷言（#88）；
  「缺工具不得靜默通過」的每條保證都有一個會觸發它的案例（#102）；
  `doc/TEST-PLAN.md` 的覆蓋率審計表涵蓋每個模組與腳本，量測缺口明白寫下（#100）。
- `doc/review/2026-09-04-pdf-conformance.md`：`main` 的每個 commit 對照設計文件
  逐條核過，偏離記成 D1–D12，各自進自己的 issue 與 PR（#130）。**本次補寫就是
  其中的 D11。**
