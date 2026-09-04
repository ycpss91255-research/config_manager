# Put every module under one top-level package, so no top-level name can shadow a standard-library one

> 服務：不變式 2（絕不靜默失效）

- **Date:** 2026-09-04
- **Status:** Accepted
- **Relates to:** ADR-00000011（核心層不做 I/O）、ADR-00000009（CLI 是 API client）

## Context

設計 §3.3.2 的 `src/` 佈局是四個頂層目錄：`core/`、`io/`、`api/`、`web/`，
以 `PYTHONPATH=src` 匯入。實作 IO 層時發現 `io` 無法使用（#56）。

`io` 是直譯器啟動過程中就必須載入的內建模組——`sys.stdout` 靠它建立——
因此 `sys.path` 上任何叫 `io` 的套件都會與它相撞。症狀依目錄內容而異：

| `src/io/` 內容 | 結果 |
|---|---|
| 只有 `.gitkeep` | 正常。**問題在此狀態下完全不可見** |
| 有 `.py`、無 `__init__.py` | 直譯器可啟動，但 `import io.writer` 失敗 |
| 有 `__init__.py` | **直譯器完全無法啟動**：`Fatal Python error: init_sys_streams` |

在 `python:3.11`、`3.13`、`3.14` 三個官方映像實測，結果完全相同——**與版本無關**，
是 import 機制的固有性質。

這條在骨架建立時沒有被任何東西擋下：目錄裡只有 `.gitkeep`，所以 CI 全綠，
測試全過，而第一個真正實作 io 模組的人才會撞牆。

## Decision

`src/` 底下只有一個頂層套件 `config_manager/`，四層放在它裡面：
`config_manager.core` / `.io` / `.api` / `.web`。

## Alternatives

- **只把 `io/` 改名為 `adapters/`。** 改動最小，但**修的是一個名字，不是一類問題**。
  失效模式是「頂層模組名與 stdlib 撞名」；`io` 只是第一個踩到的，下一個
  （`types`、`json`、`logging`、`index`⋯）會用同樣的方式被發現——也就是在有人
  實作它的時候。頂層套件前綴一次免疫整棵樹。
- **`adapters/` 這個名字另有問題。** 設計 §3.5.1 把系統描述為 Hexagonal
  （Ports and Adapters），其中 **HTTP 也是 adapter**，即 `api/` 也是。
  只把 IO 層叫 `adapters/` 會讓同一個詞指兩件事，正是 `CONTEXT.md` 要防止的。
- **保留 `io/` 但永不加 `__init__.py`。** 不可行：那是最嚴重的一種症狀的迴避，
  換來的是次嚴重的一種（模組不可匯入），且這個約束無法被任何工具檢查。

## Consequences

- 匯入路徑變長一層。這是換來「頂層命名空間不再是雷區」的代價。
- 可 `pip install -e .`；`PYTHONPATH` 從必要變成便利。
- 設計 §3.3.2 的目錄樹與本決策不一致。該樹屬 §0.7 的「推導內容」，
  以 README 的結構表為準，PDF 下次整份更新時一併修。
- **此決策在成本最低的時刻做成**：當時 `src/` 只有 6 個模組、4 個測試檔。
  再晚就是每個檔案都要動。
