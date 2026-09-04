#!/usr/bin/env python3
import os
from diagram import D

OUT = os.environ.get("FIG_OUT", "../figures")
os.makedirs(OUT, exist_ok=True)
CX, BW, BH = 430, 300, 46
print("figures:")

# ============================================================
# F1 — 服務拓撲（多容器）
# ============================================================
d = D(880, 470)
d.box("br",   90, 40, 260, 58, "**瀏覽器", "box", fs=13)
d.box("fe",  500, 40, 300, 58, "**frontend container\n單一 HTML + 靜態資源", "act", fs=12)
d.box("be",  290, 170, 300, 68, "**backend container\nAPI + 業務邏輯 + git 操作", "act", fs=12)
d.box("tgt",  60, 310, 300, 62, "**目標位置\n主機檔案系統（掛載）", "box", fs=12)
d.box("repo",520, 310, 300, 62, "**config-repo\n（volume 掛載）", "store", fs=12)
d.box("note", 60, 400, 760, 50,
      "**backend 是唯一能寫入 config-repo 與目標位置的角色；frontend 不接觸檔案系統",
      "layer", fs=11)

d.link("br", "r", "fe", "l")
d.link("fe", "b", "be", "t", bf=0.7, label="HTTP（JSON）")
d.link("be", "b", "tgt", "t", af=0.25, bf=0.7, label="寫出（原子）")
d.link("be", "b", "repo", "t", af=0.75, bf=0.3, label="讀寫")
d.verify("f1_topology"); d.save(f"{OUT}/f1_topology.svg")

# ============================================================
# F2 — 後端模組依賴
# ============================================================
d = D(880, 400)
d.box("a1", 620, 60, 200, 46, "HTTP 端點", "git", fs=11.5)
d.box("a2", 620, 122, 200, 46, "使用者身分", "git", fs=11.5)
d.box("a3", 620, 184, 200, 46, "CLI（同一組邏輯）", "git", fs=11.5)
d.band(606, 42, 228, 204, "api（對外介面）", "#27ae60")

d.box("c1", 340, 60, 200, 46, "config 清單檔載入與驗證", "act", fs=11.5)
d.box("c2", 340, 122, 200, 46, "四種狀態判定", "act", fs=11.5)
d.box("c3", 340, 184, 200, 46, "三層驗證", "act", fs=11.5)
d.band(326, 42, 228, 204, "core（純邏輯，無 I/O）", "#2980b9")

d.box("i1", 60, 60, 200, 46, "各格式解析器", "store", fs=11.5)
d.box("i2", 60, 122, 200, 46, "git（CLI 包裝）", "store", fs=11.5)
d.box("i3", 60, 184, 200, 46, "寫出（原子寫入）", "store", fs=11.5)
d.band(46, 42, 228, 204, "io（外部互動）", "#7d3c98")

d.link("a1", "l", "c1", "r")
d.link("a2", "l", "c2", "r")
d.link("a3", "l", "c3", "r")
d.link("c1", "l", "i1", "r")
d.link("c2", "l", "i2", "r")
d.link("c3", "l", "i3", "r")
d.box("nt", 60, 296, 774, 62,
      "**依賴方向 api → core → io 單向\n"
      "core 不 import api；檔案系統與 git 操作集中於 io，測試時可替換為 fake，"
      "使 core 可在無檔案系統下測試", "layer", fs=11)
d.verify("f2_modules"); d.save(f"{OUT}/f2_modules.svg")

# ============================================================
# F3 — 納管流程
# ============================================================
d = D(880, 720)
y = 24
d.box("s", CX-140, y, 280, 40, "使用者於介面指定檔案路徑", "start", fs=12); y += 74
d.box("p1", CX-BW/2, y, BW, BH, "路徑檢查（白名單／逃逸防護）", "act"); y += 80
d.dia("q1", CX, y+30, 240, 60, "路徑合法？")
d.box("e1", 690, y+8, 150, 44, "拒絕\n顯示原因", "err", fs=11); y += 96
d.box("p2", CX-BW/2, y, BW, BH, "讀取檔案 + 依 format 解析", "act"); y += 80
d.dia("q2", CX, y+34, 240, 68, "解析成功且\n無歧義值？")
d.box("w1", 670, y+8, 170, 60, "列出歧義值\n要求使用者確認", "warn", fs=11); y += 104
d.box("p3", CX-BW/2, y, BW, BH, "記錄各欄位當前型別", "act"); y += 66
d.box("p4", CX-BW/2, y, BW, BH, "顯示解析結果供確認", "act"); y += 66
d.box("p5", CX-BW/2, y, BW, 50, "寫入 repo + 建立清單檔條目", "act", fs=11.5); y += 72
d.box("p6", CX-BW/2, y, BW, 40, "記錄變更（import）", "git", fs=11.5)

d.link("s", "b", "p1", "t")
d.link("p1", "b", "q1", "t")
d.link("q1", "r", "e1", "l", label="否")
d.link("q1", "b", "p2", "t", label="是")
d.link("p2", "b", "q2", "t")
d.link("q2", "r", "w1", "l", label="否")
d.link("w1", "t", "p2", "r", col="#e67e22", label="修正後重試")
d.link("q2", "b", "p3", "t", label="是")
d.link("p3", "b", "p4", "t")
d.link("p4", "b", "p5", "t")
d.link("p5", "b", "p6", "t")
d.verify("f3_import"); d.save(f"{OUT}/f3_import.svg")

# ============================================================
# F4 — 修改與進板
# ============================================================
d = D(880, 790)
y = 24
d.box("s", CX-140, y, 280, 40, "使用者於介面編輯內容", "start", fs=12); y += 70
d.box("v1", CX-BW/2, y, BW, 42, "第 1 層｜語法與正規形式", "act"); y += 74
d.dia("q1", CX, y+28, 210, 56, "可解析？")
d.box("e1", 690, y+6, 150, 44, "拒絕\n標示錯誤行號", "err", fs=11); y += 90
d.box("v2", CX-BW/2, y, BW, 42, "第 2 層｜結構描述驗證", "act"); y += 74
d.dia("q2", CX, y+28, 210, 56, "型別／範圍符合？", fs=11)
d.box("e2", 690, y+6, 150, 44, "拒絕\n標示欄位", "err", fs=11); y += 90
d.box("v3", CX-BW/2, y, BW, 42, "第 3 層｜跨欄位規則", "act"); y += 74
d.dia("q3", CX, y+28, 210, 56, "規則通過？")
d.box("w1", 680, y+2, 160, 60, "警告\n可 override\n（需填理由）", "warn", fs=11); y += 92
d.box("p1", CX-BW/2, y, BW, 44, "寫入 repo（保留註解與格式）", "act", fs=11.5); y += 70
d.box("p2", CX-BW/2, y, BW, 40, "記錄變更（cfg，附作者）", "git", fs=11.5); y += 64
d.box("p3", CX-BW/2, y, BW, 44, "**寫出至目標位置 + 設定權限", "git", fs=11.5)

d.link("s", "b", "v1", "t")
d.link("v1", "b", "q1", "t")
d.link("q1", "r", "e1", "l", label="否")
d.link("q1", "b", "v2", "t", label="是")
d.link("v2", "b", "q2", "t")
d.link("q2", "r", "e2", "l", label="否")
d.link("q2", "b", "v3", "t", label="是")
d.link("v3", "b", "q3", "t")
d.link("q3", "r", "w1", "l", label="否")
d.link("w1", "b", "p1", "r", col="#e67e22")
d.link("q3", "b", "p1", "t", label="是")
d.link("p1", "b", "p2", "t")
d.link("p2", "b", "p3", "t")
d.verify("f4_edit"); d.save(f"{OUT}/f4_edit.svg")

# ============================================================
# F5 — 退板
# ============================================================
d = D(880, 555)
y = 24
d.box("s", CX-140, y, 280, 40, "使用者選擇目標 config", "start", fs=12); y += 72
d.box("p1", CX-BW/2, y, BW, 44, "列出歷史（依前綴過濾）", "git", fs=11.5); y += 68
d.box("p2", CX-BW/2, y, BW, 44, "選擇欲回復的版本", "act"); y += 68
d.box("p3", CX-BW/2, y, BW, 44, "顯示差異供確認", "git", fs=11.5); y += 80
d.dia("q1", CX, y+30, 230, 60, "使用者確認？")
d.box("e1", 700, y+8, 140, 44, "取消", "end", fs=12); y += 96
d.box("p4", CX-BW/2-20, y, BW+40, 48,
      "**產生反向變更紀錄（revert）\n保留完整歷史，不改寫", "git", fs=11); y += 74
d.box("p5", CX-BW/2-20, y, BW+40, 44, "**寫出至目標位置", "git", fs=11.5)

d.link("s", "b", "p1", "t")
d.link("p1", "b", "p2", "t")
d.link("p2", "b", "p3", "t")
d.link("p3", "b", "q1", "t")
d.link("q1", "r", "e1", "l", label="否")
d.link("q1", "b", "p4", "t", label="是")
d.link("p4", "b", "p5", "t")
d.verify("f5_rollback"); d.save(f"{OUT}/f5_rollback.svg")

# ============================================================
# F6 — 差異偵測
# ============================================================
d = D(910, 610)
y = 24
d.box("s", CX-165, y, 330, 40, "介面開啟／使用者按下「檢查差異」", "start", fs=12); y += 70
d.box("p1", CX-BW/2, y, BW, 42, "載入清單檔", "act"); y += 66
d.box("p2", CX-BW/2, y, BW, 42, "逐筆取出來源與目標", "act"); y += 76
d.dia("q1", CX, y+32, 240, 64, "目標檔案\n存在？")
d.box("r1", 690, y+10, 150, 44, "**未部署\n提供寫出修復", "warn", fs=11); y += 100
d.dia("q2", CX, y+32, 240, 64, "內容雜湊\n相符？")
d.box("r2", 690, y+10, 150, 44, "**一致\n正常", "git", fs=11); y += 100
d.box("r3", CX-215, y, 430, 52,
      "**偏離 — 有人繞過介面修改\n顯示差異，提供「以來源覆蓋」或「納入來源」", "err", fs=11); y += 78
d.box("p3", CX-215, y, 430, 40, "彙整結果 → 狀態面板", "act")

d.link("s", "b", "p1", "t")
d.link("p1", "b", "p2", "t")
d.link("p2", "b", "q1", "t")
d.link("q1", "r", "r1", "l", label="否")
d.link("q1", "b", "q2", "t", label="是")
d.link("q2", "r", "r2", "l", label="是")
d.link("q2", "b", "r3", "t", label="否")
d.link("r3", "b", "p3", "t")
d.link("r1", "r", "p3", "r", bf=0.30, via=885, col="#e67e22", dash=True)
d.link("r2", "r", "p3", "r", bf=0.70, via=866, col="#27ae60", dash=True)
d.verify("f6_drift"); d.save(f"{OUT}/f6_drift.svg")

# ============================================================
# F7 — 狀態機
# ============================================================
d = D(880, 400)
d.box("un", 55, 140, 185, 62, "**未納管\n不在清單檔中", "box", fs=12)
d.box("sy", 345, 140, 200, 62, "**一致\n目標 == 來源", "git", fs=12)
d.box("ms", 650, 140, 185, 62, "**未部署\n目標不存在", "warn", fs=12)
d.box("dr", 345, 300, 200, 58, "**偏離\n目標 != 來源", "err", fs=12)

d.link("un", "r", "sy", "l", af=0.3, bf=0.3, label="納管")
d.link("sy", "l", "un", "r", af=0.75, bf=0.75, col="#7f8c8d", label="解除納管")
d.link("sy", "r", "ms", "l", af=0.3, bf=0.3, col="#e67e22", label="目標被刪除")
d.link("ms", "l", "sy", "r", af=0.75, bf=0.75, col="#27ae60", label="寫出修復")
d.link("sy", "b", "dr", "t", af=0.28, bf=0.28, col="#c0392b", label="外部修改目標")
d.link("dr", "t", "sy", "b", af=0.75, bf=0.75, col="#27ae60", label="覆蓋或納入")

d.box("lg", 55, 296, 240, 66,
      "**唯一的異常狀態是「偏離」\n其餘三種皆為正常流程中的合法狀態", "layer", fs=10.5)
d.verify("f7_states"); d.save(f"{OUT}/f7_states.svg")

# ============================================================
# F8 — 導航模型
# ============================================================
d = D(880, 430)
d.box("l", 55, 168, 175, 56, "**身分輸入\n姓名 + email + 角色", "start", fs=11)
d.box("m", 330, 158, 205, 76, "**主畫面\n左側樹 + 工作區\n（核心，恆常可見）", "act", fs=11.5)
d.box("i", 665, 30, 175, 42, "納管精靈", "act", fs=11)
d.box("e", 665, 108, 175, 42, "參數編輯", "act", fs=11)
d.box("h", 665, 250, 175, 42, "歷史與退版", "act", fs=11)
d.box("f", 665, 328, 175, 42, "差異檢視", "act", fs=11)

# 身分輸入 ⇄ 主畫面
d.link("l", "r", "m", "l", af=0.3, bf=0.3, label="進入")
d.link("m", "l", "l", "r", af=0.75, bf=0.75, col="#7f8c8d", label="退出")

# 主畫面 ⇄ 四個子視圖：去程實線、回程虛線
for t, af, bf, via in (("i", 0.10, 0.3, 600), ("e", 0.32, 0.3, 620)):
    d.link("m", "r", t, "l", af=af, bf=bf, via=via)
    d.link(t, "l", "m", "r", af=0.72, bf=af + 0.08, via=via - 20,
           col="#7f8c8d", dash=True)
for t, af, bf, via in (("h", 0.66, 0.3, 620), ("f", 0.90, 0.3, 600)):
    d.link("m", "r", t, "l", af=af, bf=bf, via=via)
    d.link(t, "l", "m", "r", af=0.72, bf=af + 0.08, via=via - 20,
           col="#7f8c8d", dash=True)

d.txt(752, 400, "實線＝進入　　虛線＝完成後回到主畫面", fs=10.5, col="#5d6d7e",
      a="middle", bold=True)
d.box("nb", 55, 270, 230, 100,
      "**主畫面是核心，不是起點\n"
      "所有子視圖完成後一律回到主畫面。\n"
      "**子視圖之間沒有直接跳轉**——\n"
      "要換一份 config 就先回主畫面選。", "layer", fs=10)
d.verify("f8_nav"); d.save(f"{OUT}/f8_nav.svg")

print("done")
