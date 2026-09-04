# 變更紀錄

格式：[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)。
版本依設計文件 §8 的里程碑階梯。

`Added` ／ `Changed` ／ `Fixed` ／ `Notes` 這幾個小節名維持英文——它們是 Keep a
Changelog 這個格式的識別碼，判準與 ADR-00000028 對 `type`／`scope` 的處理相同。
**其餘一律中文**（ADR-00000028：專案文件）。

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

### Notes

- 組織共用的容器模板（`ycpss91255-docker/base`）在 v0.10.0 **不導入**，這是一個
  決定（設計附錄 A）。現在就採用的是那些反悔代價很高的選擇：stage 名稱、
  烤進映像的產物放 `/opt`、`host` 網路、`.local` 覆蓋後綴、`just` 命令模型、
  ADR 格式、Conventional Commits。
- 里程碑 v0.1.0–v0.10.0 帶著設計文件 §8.2 的驗收檢查點，以及依 §8.1 能力矩陣
  切出的 54 張 issue，每張各自帶驗收條件。§0.7 把驗收條件放在 issue 而不是文件裡，
  所以文件的 §8 成為一個指向 GitHub 的指標，而不是第二份會過期的副本。
