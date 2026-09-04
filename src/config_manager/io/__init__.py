"""io 層：程序之外的一切。

git CLI 的 wrapper、原子寫出、內容雜湊、啟動前置檢查、差異掃描。解析不在這裡
——清單檔的解析是純邏輯，留在 core.config_list（ADR-00000011）。依賴方向是
api → core → io 單向；core 永不 import api。

這個檔案存在本身就是 ADR-00000026 的重點：同一批模組放在 src/io/ 的話，會讓
直譯器起不來。
"""
