# 領域文件

skill 在探索這個 codebase 之前該讀什麼、以及讀了要遵守什麼。

## 開始之前先讀

- **`CONTEXT.md`**（repo 根目錄）——領域用語。**用那裡的詞，不要另造同義詞。**
- **`doc/adr/`**——與你要動的區域相關的 ADR。

**注意路徑是 `doc/adr/`，不是 `docs/adr/`。** 這個 repo 的文件目錄是單數的 `doc/`
（`doc/PRD.md`、`doc/TEST-PLAN.md`、`doc/adr/`、`doc/test/`）。skill 的預設慣例寫的是
`docs/`，在這裡是錯的路徑——照那個去找會找不到，而且找不到時它會安靜地當作沒有 ADR。

這個 repo 的完整閱讀順序（見 `CLAUDE.md`）：

```
CONTEXT.md  →  doc/PRD.md（9 條不變式，不得違反）
            →  doc/adr/（為什麼長成這樣）
            →  doc/TEST-PLAN.md（已確認的測試介面）
```

## 佈局：單一 context

```
/
├── CONTEXT.md
├── doc/
│   ├── PRD.md              9 條不變式
│   ├── TEST-PLAN.md        已確認的測試介面
│   ├── adr/                00000001-*.md … （8 位數編號）
│   └── test/TEST.md        怎麼跑測試
└── src/config_manager/
```

沒有 `CONTEXT-MAP.md`，也不需要——這是單一 Python 套件，不是 monorepo。

## ADR 的形式由 lint 檢查

`script/lint_adr.sh` 會擋下不合格的 ADR，寫之前先知道它檢查什麼：

- 檔名 `NNNNNNNN-<slug>.md`（8 位數補零，slug 維持英文）
- 不得重號（跳號只警告）
- 必須有 `> 服務：` 回指某條不變式
- 必須有 `## Context` / `## Decision` / `## Consequences`（缺 `## Alternatives` 只警告）
- `Status` 須為 `Accepted` / `Rejected` / `Superseded by ADR-NNNNNNNN`

**段落標題與 `> 服務：` 是給 lint 讀的識別碼，不要翻譯。** 一句話標題與內文寫中文
（ADR-00000028）。

## 用 glossary 的詞彙

輸出裡出現領域概念時（issue 標題、重構提案、假設、測試名稱），用 `CONTEXT.md` 定義的詞。
**不要漂移到它明確避免的同義詞**——那份文件有一張「避免使用的說法」表，例如不說「登入」
說「身分輸入」、不說 `DRIFT` 說「偏離」。

概念還不在 glossary 裡是一個訊號：要嘛你正在發明這個專案不使用的語言（重新考慮），
要嘛那是真的缺口（記下來給 `/domain-modeling`）。

## 與 ADR 牴觸時要說出來

輸出若與既有 ADR 相牴觸，明講，不要默默覆蓋：

> _與 ADR-00000011（核心層不做 I/O）牴觸，但值得重開，因為……_
