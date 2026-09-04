#!/usr/bin/env python3
import os
from diagram import D
OUT = os.environ.get("FIG_OUT", "../figures")

# 原則優先序 — 掛回 base invariant
d = D(880, 400)
levels = [
    ("P1　正確性與可追溯性", "資料不遺失、每次變更可追溯到人與時間", "不變式 2 / 9", "#c0392b", "#fdeded"),
    ("P2　失敗明顯", "寧可擋下合法操作，不可放行錯誤且無聲", "不變式 2", "#e67e22", "#fef5e7"),
    ("P3　安全優先於方便", "預設落向安全，較危險的選項需 opt-in", "不變式 4 / 5", "#b9770e", "#fef9e7"),
    ("P4　嚴謹度", "格式與規則選擇以消除歧義為準", "不變式 2（經 N-1）", "#2980b9", "#eaf2f8"),
    ("P5　易用性", "在不犧牲上述前提下，讓操作盡量順手", "—", "#27ae60", "#e8f6ef"),
]
for i, (t, sub, inv, sc, fc) in enumerate(levels):
    y = 30 + i*68
    d.p.append(f'<rect x="120" y="{y}" width="700" height="56" rx="4" '
               f'fill="{fc}" stroke="{sc}" stroke-width="1.8"/>')
    d.txt(140, y+22, t, fs=12.5, col=sc, bold=True)
    d.txt(140, y+41, sub, fs=10.5, col="#3a4a58")
    d.txt(806, y+34, inv, fs=9.5, col=sc, a="end", bold=True)
    if i < len(levels)-1:
        d.p.append(f'<path d="M 100 {y+56} L 100 {y+68}" stroke="#aab4bd" '
                   f'stroke-width="1.6"/>')
d.p.append('<path d="M 96 44 L 96 366" stroke="#5d6d7e" stroke-width="2" '
           'marker-end="url(#ah)"/>')
d.txt(80, 205, "衝突時", fs=10.5, col="#5d6d7e", a="end", bold=True)
d.txt(80, 220, "上位優先", fs=10.5, col="#5d6d7e", a="end", bold=True)
d.save(f"{OUT}/f0_priority.svg")

