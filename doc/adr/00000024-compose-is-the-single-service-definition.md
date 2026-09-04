# Keep compose.yaml as the only service definition; .setup.conf is a placeholder with no values

> 服務：不變式 6（一個真實來源，向外傳播，而非平行維護 N 份副本）

- **Date:** 2026-09-04
- **Status:** Accepted
- **Relates to:** ADR-00000023（擴充點佔位）、ADR-00000016（對齊模板 main）

## Context

共用容器模板以 `.setup.conf` 作為**唯一**的服務定義：模板的 setup 步驟讀它、
偵測主機、**渲染出** `.env` 與 `compose.yaml`。附錄 A 把那個渲染器列在
「現在不要做」，因此本專案的 `compose.yaml` 是手寫的，也是現在的權威。

ADR-00000023 決定擴充點要佔位。`.setup.conf` 是同一類的位置，但性質不同：
hooks 是**空的執行點**，佔位後是空的；`.setup.conf` 是**資料**，佔位時很容易
順手把 compose.yaml 的內容抄一份進去——服務名、網路模式、掛載、啟動指令。

那就是不變式 6 禁止的形態。兩份都寫著服務定義，只有一份被讀，
**沒被讀的那份會靜默過期**，而且是在最不該過期的地方：下一個人導入模板時，
會拿那份過期的定義去渲染。

## Decision

`.setup.conf` **建立，但不放任何服務值**。檔案內容是：

1. 明說**今天沒有東西讀這個檔案**，`compose.yaml` 才是權威；
2. 為何不放值——不變式 6 的推論，寫在檔案裡而非只在此 ADR；
3. 導入模板時的三步順序：填值 → 渲染並與手寫版 diff → **兩者一致後刪掉手寫的
   `compose.yaml`**，並把產生的那份加入 `.gitignore`；
4. 明寫「不要只做第 1 步而不做第 3 步」——兩份同時存活正是本檔要避免的狀態；
5. 欄位名以註解形式草擬，並註明**導入時要照模板自己的 schema 填，不要照這份註解**——
   這份註解到那時必然已經漂移。

## Alternatives

- **完整填寫 `.setup.conf`，與 compose.yaml 並存。** 直接違反不變式 6。
  「反正兩份會一起改」是每一份漂移的副本被建立時的說法。
- **完全不建 `.setup.conf`。** 先前的做法，不違反任何不變式，但導入時沒有任何東西
  提示「順序很重要」——而先填值後刪 compose 與先刪後填，中間的窗口差很多。
  檔案本身就是那個提示。
- **把導入步驟寫在 README。** README 是給讀者看的，導入模板的人會打開的是
  `.setup.conf`。說明放在被打開的那個檔案裡。

## Consequences

- repo 根目錄多一個沒有作用的檔案。這是刻意的成本：它的作用是承載一份
  在正確時刻會被讀到的說明。
- `.setup.conf.local`（操作者覆蓋）不建立——沒有值可覆蓋。`.gitignore` 的 `*.local`
  規則已涵蓋它日後出現的情況。
- 導入模板時本 ADR 的 Status 改為 `Superseded by ADR-NNNNNNNN`，
  由那份記錄實際的導入決策。
