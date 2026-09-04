#!/usr/bin/env python3
import os
from diagram import D
OUT = os.environ.get("FIG_OUT", "../figures")
os.makedirs(OUT, exist_ok=True)

# --- 文件分工 ---
d = D(880, 330)
d.box("p", 60, 40, 240, 76, "**本文件 §0\n不變式與原則\n只在產品目標改變時更新", "store", fs=11)
d.box("a", 340, 40, 240, 76, "**doc/adr/\n單一決策 + 理由\n只增不刪，可在原檔修訂", "git", fs=11)
d.box("c", 620, 40, 200, 76, "**CONTEXT.md\n領域詞彙表\n固定用語，避免重新造詞", "act", fs=11)
d.box("t", 240, 190, 400, 76,
      "**本文件 §1 以後（設計快照）\n意圖 / 理由 / 形狀的原因\n隨版本整份更新；可推導的內容不進來", "layer", fs=11)
d.box("k", 60, 190, 150, 76, "程式碼\n+ 自動生成", "box", fs=11)
d.box("i", 670, 190, 150, 76, "Issues\n+ Milestones", "box", fs=11)
d.link("p", "b", "t", "t", bf=0.15, label="不變式 9：可推導者不入文件")
d.link("a", "b", "t", "t", bf=0.55, label="決策回指原則")
d.link("c", "b", "t", "t", bf=0.9, label="用語一致")
d.txt(440, 300, "四份內容互不重複：原則 / 決策 / 詞彙 / 設計快照",
      fs=10.5, col="#5d6d7e", a="middle", bold=True)
d.verify("f0_docs"); d.save(f"{OUT}/f0_docs.svg")

# --- 三類決策 ---
d = D(880, 470)
cols = [
    (40, "#c0392b", "現在就對齊",
     ["ADR 格式（8 位數編號）", "Commit 前綴規範", "測試分類三軸", "測試目錄與鏡射規則",
      "腳本介面與參數規則", "容器 stage 命名", "檔名規則（.local 後綴）",
      "網路預設 host", "產物烘在 /opt"],
     "事後改很貴", "現在做幾乎零成本"),
    (313, "#e67e22", "自建最小版本",
     ["compose 設定（手寫，", "  不自建解析層）", "任務入口腳本（照命名，",
      "  內容自己寫）", "CI workflow（直接寫，", "  不做共用 worker）",
      "覆蓋率工具（pytest-cov）"],
     "導入時整份換掉", "保持最小，不加功能"),
    (586, "#7f8c8d", "現在不要做",
     ["設定解析 + 主機偵測 + TUI", "共用 CI worker", "測試工具映像檔",
      "subtree 升級與傳播機制", "來源／發布目錄切分",
      "容器 lifecycle（init／", "  restart／watchdog／log）"],
     "導入後由模板提供", "自建 = 之後要拆掉重寫"),
]
for x, col, title, items, f1, f2 in cols:
    d.box(f"c{x}", x, 50, 255, 250, "", "layer")
    d.p.append(f'<rect x="{x}" y="50" width="255" height="34" rx="4" fill="{col}"/>')
    d.txt(x+127, 72, title, fs=12, col="#fff", a="middle", bold=True)
    for i, t in enumerate(items):
        d.txt(x+16, 108+i*19, ("· " if not t.startswith("  ") else "")+t,
              fs=9.5, col="#3a4a58")
    d.txt(x+127, 272, f1, fs=10, col=col, a="middle", bold=True)
    d.txt(x+127, 288, f2, fs=10, col=col, a="middle", bold=True)
d.box("m", 40, 370, 801, 62,
      "**導入時機：本專案 v1.0.0 之後，或模板的 config 供裝模型需與本系統整合時\n"
      "屆時的工作 = 刪除中欄的自建版本 + 以 subtree 加入模板 + 任務入口改為轉發。左欄不需改動，右欄不存在",
      "layer", fs=11)
d.link("c313", "b", "m", "t")
d.verify("f0_align"); d.save(f"{OUT}/f0_align.svg")
