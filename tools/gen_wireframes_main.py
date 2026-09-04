#!/usr/bin/env python3
import os
from diagram import D
OUT = os.environ.get("FIG_OUT", "../figures")
os.makedirs(OUT, exist_ok=True)
GREY, DARK = "#7a8792", "#2c3e50"

def chrome(d,x,y,w,h,t):
    d.box("_w",x,y,w,h,"","layer")
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="32" rx="3" fill="#eceff1" stroke="#aab4bd" stroke-width="1.2"/>')
    d.txt(x+14,y+21,t,fs=11.5,col=DARK,bold=True)
def pill(d,x,y,t,c,w=54,h=17,fs=8.5):
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{c}"/>')
    d.txt(x+w/2,y+h/2+4,t,fs=fs,col="#fff",a="middle",bold=True)
def dot(d,x,y,c,r=4): d.p.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>')
def btn(d,x,y,w,h,t,pri=True,fs=10):
    f,st,tc=("#2c3e50","#2c3e50","#fff") if pri else ("#fff","#aab4bd",DARK)
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{f}" stroke="{st}" stroke-width="1.2"/>')
    d.txt(x+w/2,y+h/2+4,t,fs=fs,col=tc,a="middle",bold=True)
def field(d,x,y,w,h,v,err=False,mono=True):
    bg,bd=("#fdeded","#c0392b") if err else ("#fbfcfd","#c3cbd2")
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{bg}" stroke="{bd}" stroke-width="1.2"/>')
    d.txt(x+7,y+h/2+4,v,fs=9,col="#3a4a58",mono=mono)
def sel(d,x,y,w,h,v,hi=False):
    bd = "#2980b9" if hi else "#c3cbd2"
    d.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#fbfcfd" stroke="{bd}" stroke-width="1.2"/>')
    d.txt(x+7,y+h/2+4,v,fs=9,col="#3a4a58",mono=True); d.txt(x+w-8,y+h/2+4,"▾",fs=8,col=GREY,a="end")
def check(d,x,y,on):
    d.p.append(f'<rect x="{x}" y="{y}" width="14" height="14" rx="2" fill="{"#2c3e50" if on else "#fff"}" stroke="#aab4bd" stroke-width="1.2"/>')
    if on: d.txt(x+7,y+11,"✓",fs=9,col="#fff",a="middle",bold=True)

# ═══ 主畫面 ═══
d=D(880,530)
chrome(d,25,20,830,490,"Config Manager　—　cyc <cyc@example.com>　｜　🔧 開發者")
btn(d,762,26,80,20,"退出",False,8.5)
d.p.append('<rect x="25" y="52" width="830" height="40" fill="#f7f9fa" stroke="#aab4bd" stroke-width="1"/>')
sel(d,40,62,110,22,"全部",hi=True)
d.p.append('<rect x="156" y="62" width="270" height="22" rx="3" fill="#fff" stroke="#c3cbd2" stroke-width="1"/>')
d.txt(164,77,"搜尋…",fs=9,col="#a8b2ba",mono=True)
d.txt(40,102,"↑ 搜尋範圍：全部（預設）／config 名稱／目標路徑／參數名稱／參數值",fs=8.5,col="#2980b9",bold=True)
d.txt(736,102,"↑ 有 2 份草稿待進版",fs=8.5,col="#e67e22",bold=True,a="start")
btn(d,470,61,80,24,"檢查差異",False,9)
btn(d,556,61,80,24,"🔧 白名單",False,9)
btn(d,642,61,88,24,"＋ 納管",False,9)
btn(d,736,61,104,24,"進版 (2)",True,9)
# 左樹（以 group 為階層，無多車）
d.p.append('<rect x="25" y="112" width="205" height="398" fill="#fbfcfd" stroke="#aab4bd" stroke-width="1"/>')
d.txt(40,134,"依群組",fs=9.5,col=GREY,bold=True)
sel(d,100,124,110,16,"群組 ▾")
tree=[(0,"▾ navigation","#c0392b",True),(1,"nav2-params","#c0392b",False),
      (1,"costmap","#27ae60",False),(0,"▾ perception","#27ae60",True),
      (1,"lidar-merger","#27ae60",False),(1,"camera","#27ae60",False),
      (0,"▾ system","#e67e22",True),(1,"docker-daemon","#e67e22",False),
      (0,"▸ 未分群 (2)","#7a8792",True)]
for i,(lv,t,c,f) in enumerate(tree):
    y=162+i*28
    if t=="nav2-params": d.p.append(f'<rect x="27" y="{y-12}" width="201" height="24" fill="#eaf2f8"/>')
    dot(d,42+lv*14,y,c); d.txt(54+lv*14,y+4,t,fs=9.5,col=DARK,bold=f)
    if t in ("nav2-params","camera"): d.txt(216,y+4,"●",fs=10,col="#e67e22",a="end")
d.txt(40,432,"● 偏離　● 未部署　● 一致",fs=8.5,col=GREY)
d.txt(40,448,"右側 ● ＝有未進版的草稿",fs=8.5,col="#e67e22",bold=True)
d.txt(40,470,"群組由 config 屬性決定，可編輯",fs=8.5,col="#2980b9",bold=True)
d.txt(40,486,"單擊＝選取　雙擊＝展開",fs=8.5,col=GREY)
# 右工作區
d.p.append('<rect x="230" y="112" width="625" height="398" fill="#fff" stroke="#aab4bd" stroke-width="1"/>')
d.p.append('<rect x="244" y="126" width="597" height="28" fill="#eceff1" stroke="#c3cbd2" stroke-width="1"/>')
d.txt(256,145,"▾  nav2-params",fs=10.5,col=DARK,bold=True,mono=True)
pill(d,400,130,"偏離","#c0392b")
btn(d,556,129,68,22,"歷史",False,8.5)
btn(d,630,129,68,22,"退版",False,8.5)
btn(d,704,129,68,22,"🔧 屬性",False,8.5)
btn(d,778,129,58,22,"儲存",True,8.5)
d.txt(486,144,"● 草稿",fs=8.5,col="#e67e22",bold=True)
# 屬性面板（展開）
d.p.append('<rect x="244" y="160" width="597" height="86" fill="#f4f8fc" stroke="#2980b9" stroke-width="1.2"/>')
d.txt(256,178,"config 屬性（可編輯後儲存，變更以 meta 紀錄）",fs=9,col="#1a3a5a",bold=True)
for i,(lbl,val,w) in enumerate([("名稱","nav2-params",150),("群組","navigation, safety",180),("主機（選填）","amr01",110)]):
    x=256+i*195
    d.txt(x,200,lbl,fs=8.5,col=GREY)
    field(d,x,206,w,20,val)
d.txt(256,240,"群組改變後，左側樹立即依新群組重建",fs=8.5,col="#2980b9")
# 參數列
rows=[("max_vel_x","double","0.8"),("use_sim_time","bool","false"),("controller","enum","DWB")]
for i,(n,t,v) in enumerate(rows):
    y=262+i*30
    d.line(244,y+22,841,y+22,"#eef1f3")
    d.txt(262,y+14,n,fs=9.5,col=DARK,mono=True)
    d.txt(440,y+14,t,fs=8.5,col="#7d3c98",mono=True)
    if t=="bool": check(d,540,y+3,False)
    elif t=="enum": sel(d,540,y+2,120,20,v)
    else: field(d,540,y+2,120,20,v)
d.p.append('<rect x="244" y="366" width="597" height="28" fill="#eceff1" stroke="#c3cbd2" stroke-width="1"/>')
d.txt(256,385,"▸  lidar-merger",fs=10.5,col=DARK,bold=True,mono=True)
pill(d,400,370,"一致","#27ae60")
d.p.append('<rect x="244" y="410" width="597" height="56" rx="3" fill="#fef8f1" stroke="#e67e22" stroke-width="1.2"/>')
d.txt(256,428,"區塊「儲存」＝存為草稿，不記錄也不寫出。工具列「進版」＝一次把全部草稿",fs=8.5,col="#7e4a11",bold=True)
d.txt(256,443,"驗證、記錄、寫出。「退版」針對單一 config，與草稿無關。",fs=8.5,col="#7e4a11",bold=True)
d.txt(256,458,"🔧 標記＝僅開發者可見／可用",fs=8.5,col="#7d3c98",bold=True)
d.verify("w2_main"); d.save(f"{OUT}/w2_main.svg")

# ═══ 參數表（各型態 + 開發者型別編輯） ═══
d=D(880,560)
chrome(d,25,20,830,520,"nav2-params　｜　參數編輯　｜　🔧 開發者模式")
d.p.append('<rect x="25" y="52" width="830" height="34" fill="#f7f9fa" stroke="#aab4bd" stroke-width="1"/>')
d.txt(40,68,"/opt/robot/config/nav2_params.yaml",fs=9.5,col=DARK,mono=True,bold=True)
d.txt(40,81,"群組 navigation, safety　｜　權限 root:root 0644　｜　型別來源：推斷 + 3 項人工指定",fs=8.5,col=GREY)
pill(d,760,58,"一致","#27ae60",w=64)
d.p.append('<rect x="40" y="92" width="800" height="24" fill="#eceff1" stroke="#c3cbd2" stroke-width="1"/>')
for x,t in ((52,"參數"),(250,"型別 🔧"),(380,"值"),(590,"來源值"),(680,"驗證")):
    d.txt(x,108,t,fs=8.5,col=GREY,bold=True)
rows=[("max_vel_x","double","num","0.8","0.5",False,None,False),
      ("max_accel","int","num","3","3",True,None,False),
      ("use_sim_time","bool","chk","false","false",False,None,False),
      ("controller","enum","sel","DWB","DWB",False,None,False),
      ("robot_frame","string","txt","base_link","base_link",True,None,False),
      ("inflation_r","double","num","0.15","0.55",False,"小於 robot_radius 0.3",True)]
y=116
for n,t,k,v,src,ovr,err,bad in rows:
    if bad: d.p.append(f'<rect x="40" y="{y}" width="800" height="34" fill="#fdf6f5"/>')
    d.line(40,y+34,840,y+34,"#eef1f3")
    d.txt(52,y+22,n,fs=9.5,col=DARK,mono=True)
    sel(d,250,y+7,90,20,t,hi=ovr)
    if ovr:
        d.txt(346,y+22,"已指定",fs=7.5,col="#7d3c98",bold=True)
        d.txt(346,y+31,"✕ 清除",fs=7.5,col="#c0392b")
    if k=="num": field(d,380,y+7,150,20,v,bad)
    elif k=="chk": check(d,380,y+9,v=="true")
    elif k=="sel": sel(d,380,y+7,150,20,v)
    else: field(d,380,y+7,150,20,v,mono=False)
    d.txt(590,y+22,src,fs=8.5,col="#e67e22" if v!=src else GREY,mono=True)
    d.txt(680,y+22,("✕ "+err) if err else "✓",fs=8.5,col="#c0392b" if err else "#27ae60",bold=True)
    y+=34
# list
d.p.append(f'<rect x="40" y="{y}" width="800" height="28" fill="#f7f9fa"/>')
d.txt(52,y+19,"▾ recovery_behaviors",fs=9.5,col=DARK,mono=True,bold=True)
sel(d,250,y+5,90,18,"list")
btn(d,380,y+5,56,18,"＋ 新增",False,8)
d.line(40,y+28,840,y+28,"#eef1f3"); y+=28
for i,v in enumerate(["spin","backup","wait"]):
    d.txt(70,y+18,f"[{i}]",fs=8.5,col=GREY,mono=True)
    field(d,380,y+5,150,18,v,mono=False)
    d.txt(546,y+18,"↑",fs=11,col="#2980b9",bold=True)
    d.txt(566,y+18,"↓",fs=11,col="#2980b9",bold=True)
    d.txt(590,y+18,"✕ 移除",fs=8,col="#c0392b")
    d.line(40,y+26,840,y+26,"#f4f6f7"); y+=26
d.txt(546,y+14,"↑↓ 交換順序",fs=8.5,col="#2980b9",bold=True)
# nested
d.p.append(f'<rect x="40" y="{y+22}" width="800" height="26" fill="#f7f9fa"/>')
d.txt(52,y+40,"▾ costmap（巢狀物件）",fs=9.5,col=DARK,mono=True,bold=True)
sel(d,250,y+25,90,18,"object")
d.txt(380,y+40,"內部遞迴套用同一套規則",fs=8.5,col=GREY)
# 底部
d.txt(40,506,"「儲存」＝存為草稿；工具列的「進版」才會記錄與寫出　｜　🔧 開發者模式才能修改型別欄；一般使用者只看得到型別，不能改",fs=9,col="#7d3c98",bold=True)
d.txt(40,524,"人工指定的型別存入 schema，可個別清除回到推斷值",fs=9,col="#7d3c98")
btn(d,600,500,110,24,"取消",False)
btn(d,724,500,116,24,"儲存")
d.verify("w3_params"); d.save(f"{OUT}/w3_params.svg")
print("done")
