# Architecture Decision Records

一筆決策一個檔案。**檔案系統就是註冊表**——不另外維護號碼清單。

## 檔名

`NNNNNNNN-<kebab-slug>.md` —— 8 位數補零編號 + kebab-case slug。
`README.md` 是唯一豁免的非 ADR 檔。

## Lint

掛在 `just test`，檢查兩層：

**編號與檔名**
- 檔名不符 `^[0-9]{8}-.+\.md$` → **fail**
- 重號 → **fail**
- 跳號 → **warn**（跳號可能是刻意的）

**結構**
- 缺 `> 服務：` → **fail**
- 缺 `## Context` / `## Decision` / `## Consequences` → **fail**
- `Status` 不在 `Accepted` / `Rejected` / `Superseded by ADR-NNNNNNNN` 內 → **fail**
- 缺 `## Alternatives` → **warn**

訊息須指名是哪個檔案／哪一項。

> **為何結構也要 lint。** 抽查組織共用容器模板的 ADR 目錄：27 份全部有 Serves 回指、
> 全部有 Context/Decision/Consequences、25 份有 Alternatives——一致性極高。但其編號
> lint（128 行）只檢查檔名與重號，完全不碰結構；20 個檢查驅動裡沒有任何一支驗證
> ADR 段落。**那個 27/27 是靠自律維持的，沒有東西擋下第 28 份漏掉 Serves 的 ADR。**
> CI 綠燈但契約已破，正是不變式 2 要防的形態。成本是在編號檢查裡多幾行 grep。

> **lint 檢查回指「存在」，不檢查回指「成立」。** `script/lint_adr.sh` 對這一欄做的是
> `grep -q '^> 服務'`——那一行在，就過。要判斷回指成不成立需要理解決策內容，那不是 lint
> 做得到的事，設計 §0.5 的方塊也只承諾結構檢查。**所以這是一道 lint 擋不住的缺口，
> 由寫的人與 review 補。** 它不是假設性的：ADR-00000028 的回指寫著不成立的
> 「不變式 8」，而 `lint_adr` 對那 28 份的執行結果是 0 failure、0 warning——
> 是人讀出來的，不是門擋下來的（#125）。
>
> 寫下一份 ADR 時，回指欄自問一次：**把這條不變式拿掉，這個決定還有理由嗎？**
> 還有，就是指錯了；只剩一部分理由，就是涵蓋不全——後者比指錯難發現，因為它
> 讀起來像是對的。九條都不貼切時，「機制，無對應不變式」是正確答案，不是逃生口
> （ADR-00000001／00000007／00000010／00000028 都是這樣寫的）。
> 28 份的逐份核對結果見 `doc/review/2026-09-05-adr-serves-backrefs.md`。

> **為何 Alternatives 只 warn。** 對照組有 2/27 缺此段落，硬擋會讓既有內容無法通過，
> 且確實存在「只有一種做法」的決策。但缺少它是一個訊號——沒有比較過的決策通常只是
> 偏好——所以要出現在輸出裡，只是不阻擋合併。與既有 lint 對跳號的處理一致。

## 結構

```markdown
# 句子式標題

> 服務：不變式 N ／ 產品目標 ／ 機制，無對應不變式

- **Date:** YYYY-MM-DD
- **Status:** Accepted | Rejected | Superseded by ADR-NNNNNNNN
- **Relates to:** ADR-NNNNNNNN（選填）

## Context
## Decision
## Alternatives
## Consequences
```

`> 服務：` **必填**。回指所服務的不變式編號、產品目標，或明寫
「機制，無對應不變式」。這使每個決策必須掛在產品目標上，而非掛在
「當時覺得這樣比較好」。

## 修訂

**可在原檔內修訂**，以 `**Amendment (#issue, YYYY-MM-DD):** …` 段落記在原檔。
只有**推翻**才開新 ADR 並將舊者標 superseded。

區分準則：**決策沒變 → 原檔加註；決策被推翻 → 新 ADR + superseded。**

常見的「一經接受即不可修改」在實務上會失真：當決策本身沒變、但它當初論證
所用的案例已經不存在時，強制開新 ADR 會讓讀者必須自行拼湊兩份文件才知道
舊的哪一段還算數。

## 撰寫時機

決策做成當下，不事後補。事後補寫的 ADR 幾乎必然遺漏當時真正的驅動因素與
被否決的選項。
