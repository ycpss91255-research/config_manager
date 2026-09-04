#!/usr/bin/env python3
import os
from diagram import D

OUT = os.environ.get("FIG_OUT", "../figures")
os.makedirs(OUT, exist_ok=True)
print("generating wireframes...")

GREY = "#7a8792"
DARK = "#2c3e50"


def chrome(d, x, y, w, h, title):
    d.box("_win", x, y, w, h, "", "wf")
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="34" rx="3" fill="#eceff1" stroke="#aab4bd" stroke-width="1.2"/>')
    d.txt(x+14, y+22, title, fs=11.5, col=DARK, bold=True)


def field(d, x, y, w, label, placeholder, h=32):
    d.txt(x, y-6, label, fs=10, col=GREY, bold=True)
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
               f'fill="#fbfcfd" stroke="#c3cbd2" stroke-width="1.2"/>')
    d.txt(x+10, y+h/2+4, placeholder, fs=10.5, col="#a8b2ba", mono=True)


def btn(d, x, y, w, h, label, primary=True, fs=10.5):
    f, s, t = ("#2c3e50", "#2c3e50", "#ffffff") if primary else ("#ffffff", "#aab4bd", DARK)
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" '
               f'fill="{f}" stroke="{s}" stroke-width="1.2"/>')
    d.txt(x+w/2, y+h/2+4, label, fs=fs, col=t, a="middle", bold=True)


def pill(d, x, y, label, color, w=76, h=19):
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" '
               f'fill="{color}" stroke="none"/>')
    d.txt(x+w/2, y+h/2+4, label, fs=9, col="#ffffff", a="middle", bold=True)


# ============================================================
# WF1 — 身分輸入頁（含角色切換）
# ============================================================
d = D(880, 430)
chrome(d, 170, 24, 540, 380, "Config Manager")
d.txt(440, 92, "Config Manager", fs=19, col=DARK, a="middle", bold=True)
d.txt(440, 114, "請輸入身分資訊，將作為變更紀錄的作者", fs=10, col=GREY, a="middle")
d.txt(440, 130, "此處沒有密碼驗證，不是登入", fs=9, col="#c0392b", a="middle", bold=True)
field(d, 230, 158, 420, "姓名", "cyc")
field(d, 230, 222, 420, "Email", "cyc@example.com")
d.txt(230, 280, "角色", fs=10, col=GREY, bold=True)
d.p.append('<rect x="230" y="288" width="210" height="30" rx="4" fill="#2c3e50" stroke="#2c3e50" stroke-width="1.2"/>')
d.txt(335, 307, "一般使用者", fs=10.5, col="#ffffff", a="middle", bold=True)
d.p.append('<rect x="440" y="288" width="210" height="30" rx="4" fill="#ffffff" stroke="#aab4bd" stroke-width="1.2"/>')
d.txt(545, 307, "\U0001F527 開發者", fs=10.5, col=DARK, a="middle", bold=True)
d.txt(230, 336, "開發者可修改參數型別、維護白名單、編輯 config 屬性", fs=8.5, col="#7d3c98", bold=True)
d.txt(230, 350, "v0.10.0 前為自我宣告，無驗證。目的是讓你清楚知道目前處於哪個模式", fs=8.5, col=GREY)
d.p.append('<rect x="230" y="366" width="13" height="13" rx="2" fill="#ffffff" stroke="#aab4bd" stroke-width="1.2"/>')
d.txt(252, 377, "記住此裝置（僅開發環境顯示，不記住角色）", fs=9, col=DARK)
btn(d, 540, 362, 110, 26, "進入")
d.box("n1", 30, 200, 120, 70, "非認證機制\n見 §7.8", "layer", fs=9.5)
d.box("n2", 730, 274, 120, 70, "切換明顯可見，\n不藏在選單裡", "layer", fs=9.5)
d.save(f"{OUT}/w1_login.svg")
print("  ok w1_login")
# ============================================================
# WF2 與 WF3 由 gen_wireframes_main.py 產生

# WF4 — 歷史 / 退板
# ============================================================
d = D(880, 500)
chrome(d, 30, 20, 820, 450, "歷史　amr01-nav2")

d.p.append('<rect x="30" y="54" width="820" height="40" fill="#f7f9fa" '
           'stroke="#aab4bd" stroke-width="1"/>')
d.txt(46, 79, "篩選：", fs=10, col=GREY, bold=True)
for i, (t, on) in enumerate([("只看內容變更", True), ("全部", False)]):
    xx = 92 + i*128
    f = "#2c3e50" if on else "#ffffff"
    tc = "#ffffff" if on else DARK
    d.p.append(f'<rect x="{xx}" y="{64}" width="118" height="22" rx="11" fill="{f}" '
               f'stroke="#aab4bd" stroke-width="1"/>')
    d.txt(xx+59, 79, t, fs=9.5, col=tc, a="middle", bold=True)

# commit 列表
d.p.append('<rect x="46" y="108" width="330" height="300" rx="3" fill="#ffffff" '
           'stroke="#c3cbd2" stroke-width="1.2"/>')
commits = [
    ("修改參數", "#2980b9", "調整 max_vel_x 至 0.8", "cyc · 2h 前", True),
    ("修改參數", "#2980b9", "放寬 inflation_radius", "cyc · 1d 前", False),
    ("退回舊版本", "#e67e22", "退回到 3 天前的版本", "wei · 3d 前", False),
    ("採納現場調整", "#27ae60", "納入現場的速度調整", "wei · 4d 前", False),
    ("納入管理", "#7d3c98", "初次納管", "cyc · 2w 前", False),
]
for i, (kind, col, msg, meta, sel) in enumerate(commits):
    yy = 116 + i*58
    if sel:
        d.p.append(f'<rect x="48" y="{yy}" width="326" height="54" fill="#eaf2f8"/>')
    d.line(46, yy+54, 376, yy+54)
    pill(d, 58, yy+10, kind, col, w=76, h=17)
    d.txt(144, yy+23, msg, fs=10, col=DARK, bold=True)
    d.txt(144, yy+40, meta, fs=9, col=GREY)
    d.txt(364, yy+23, "a3f19c2"[:7], fs=9, col="#b4bcc4", a="end", mono=True)

# diff
d.p.append('<rect x="394" y="108" width="442" height="300" rx="3" fill="#ffffff" '
           'stroke="#c3cbd2" stroke-width="1.2"/>')
d.p.append('<rect x="394" y="108" width="442" height="26" fill="#eceff1"/>')
d.txt(408, 126, "與目前版本的差異", fs=10.5, col=DARK, bold=True)
diff = [
    ("  controller_server:", "#5d6d7e", None),
    ("    ros__parameters:", "#5d6d7e", None),
    ("-     max_vel_x: 0.5", "#7b241c", "#fdeded"),
    ("+     max_vel_x: 0.8", "#14512f", "#f4faf6"),
    ("      min_vel_x: 0.0", "#5d6d7e", None),
]
for i, (ln, col, bg) in enumerate(diff):
    yy = 148 + i*24
    if bg:
        d.p.append(f'<rect x="396" y="{yy-15}" width="438" height="22" fill="{bg}"/>')
    d.txt(410, yy, ln, fs=9.5, col=col, mono=True)

btn(d, 560, 424, 130, 28, "檢視完整檔案", primary=False, fs=10)
btn(d, 700, 424, 136, 28, "退回此版本", fs=10)
d.txt(46, 440, "退版產生新紀錄，不改寫歷史",
      fs=9.5, col=GREY)
d.save(f"{OUT}/w4_history.svg")

# ============================================================
# WF5 — 差異檢視（偏離處置）
# ============================================================
d = D(880, 440)
chrome(d, 30, 20, 820, 390, "差異檢視　amr01-lidar")

d.p.append('<rect x="30" y="54" width="820" height="52" fill="#fdf6f5" '
           'stroke="#c0392b" stroke-width="1.2"/>')
pill(d, 46, 70, "偏離", "#c0392b", w=64)
d.txt(124, 78, "目標檔案內容與 repo 不一致", fs=11.5, col="#7b241c", bold=True)
d.txt(124, 95, "此修改未經介面進行，無對應的 commit 紀錄與作者資訊",
      fs=9.5, col="#7b241c")

d.p.append('<rect x="46" y="124" width="390" height="196" rx="3" fill="#ffffff" '
           'stroke="#c3cbd2" stroke-width="1.2"/>')
d.p.append('<rect x="46" y="124" width="390" height="26" fill="#eceff1"/>')
d.txt(60, 142, "repo（唯一真實來源）", fs=10, col=DARK, bold=True)
d.p.append('<rect x="446" y="124" width="390" height="196" rx="3" fill="#ffffff" '
           'stroke="#c3cbd2" stroke-width="1.2"/>')
d.p.append('<rect x="446" y="124" width="390" height="26" fill="#eceff1"/>')
d.txt(460, 142, "target（磁碟現況）", fs=10, col=DARK, bold=True)

left = [("  angle_min: -1.57", None), ("  angle_max: 1.57", None),
        ("  range_max: 25.0", "#fdeded"), ("  scan_topic: /scan", None)]
right = [("  angle_min: -1.57", None), ("  angle_max: 1.57", None),
         ("  range_max: 12.0", "#f4faf6"), ("  scan_topic: /scan", None)]
for base, rows in ((60, left), (460, right)):
    for i, (ln, bg) in enumerate(rows):
        yy = 172 + i*24
        if bg:
            d.p.append(f'<rect x="{base-12}" y="{yy-15}" width="386" height="22" '
                       f'fill="{bg}"/>')
        d.txt(base, yy, ln, fs=9.5, col="#3a4a58", mono=True)

btn(d, 380, 340, 210, 32, "以 repo 覆蓋 target", primary=False)
btn(d, 606, 340, 230, 32, "將 target 現況納入 repo")
d.txt(46, 360, "兩種處置皆會產生", fs=9.5, col=GREY)
d.txt(46, 374, "commit 紀錄", fs=9.5, col=GREY)
d.save(f"{OUT}/w5_drift.svg")

print("wireframes done")
