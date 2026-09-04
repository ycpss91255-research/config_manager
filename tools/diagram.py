#!/usr/bin/env python3
"""SVG 生成器 v3 — 連線一律以 (節點, 邊) 指定，端點由框的幾何計算，不手寫座標。"""

F = "Noto Sans CJK TC, WenQuanYi Zen Hei, sans-serif"
M = "DejaVu Sans Mono, monospace"

ST = {
    "box":   ("#ffffff", "#34495e", 4,  "#1a1a1a"),
    "start": ("#2c3e50", "#2c3e50", 18, "#ffffff"),
    "end":   ("#7f8c8d", "#7f8c8d", 18, "#ffffff"),
    "act":   ("#eaf2f8", "#2980b9", 4,  "#1a3a5a"),
    "git":   ("#e8f6ef", "#27ae60", 4,  "#14512f"),
    "err":   ("#fdeded", "#c0392b", 4,  "#7b241c"),
    "warn":  ("#fef5e7", "#e67e22", 4,  "#7e4a11"),
    "store": ("#f4ecf7", "#7d3c98", 4,  "#4a235a"),
    "layer": ("#f7f9fa", "#95a5a6", 4,  "#2c3e50"),
    "wf":    ("#ffffff", "#aab4bd", 3,  "#3a4a58"),
    "wfin":  ("#fbfcfd", "#c3cbd2", 3,  "#7a8792"),
    "wfbtn": ("#2c3e50", "#2c3e50", 3,  "#ffffff"),
}


class D:
    def __init__(self, w, h):
        self.w, self.h, self.p, self.n = w, h, [], {}

    # ---------- 節點 ----------
    def box(self, nid, x, y, w, h, text="", st="box", fs=12, mono=False, pad=12,
            align="c"):
        f, s, r, t = ST[st]
        self.n[nid] = (x, y, w, h)
        self.p.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
                      f'fill="{f}" stroke="{s}" stroke-width="1.6"/>')
        if text:
            self._t(x, y, w, h, text, t, fs, mono, pad, align)
        return nid

    def dia(self, nid, cx, cy, w, h, text, fs=11.5):
        self.n[nid] = (cx-w/2, cy-h/2, w, h)
        self.p.append(f'<polygon points="{cx},{cy-h/2} {cx+w/2},{cy} {cx},{cy+h/2} '
                      f'{cx-w/2},{cy}" fill="#fef9e7" stroke="#b9770e" stroke-width="1.6"/>')
        self._t(cx-w/2, cy-h/2, w, h, text, "#6e4a08", fs, False, 0, "c")
        return nid

    def _t(self, x, y, w, h, text, col, fs, mono, pad, align):
        ls = text.split("\n")
        lh = fs*1.42
        ty = y + h/2 - len(ls)*lh/2 + lh*0.76
        fam = M if mono else F
        ax = x+w/2 if align == "c" else x+pad
        ta = "middle" if align == "c" else "start"
        for i, ln in enumerate(ls):
            b = ln.startswith("**")
            ln = ln.replace("**", "")
            e = ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            self.p.append(f'<text x="{ax}" y="{ty+i*lh:.1f}" font-family="{fam}" '
                          f'font-size="{fs}" font-weight="{"bold" if b else "normal"}" '
                          f'fill="{col}" text-anchor="{ta}">{e}</text>')

    def txt(self, x, y, t, fs=10.5, col="#5d6d7e", a="start", bold=False, mono=False):
        e = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.p.append(f'<text x="{x}" y="{y}" font-family="{M if mono else F}" '
                      f'font-size="{fs}" font-weight="{"bold" if bold else "normal"}" '
                      f'fill="{col}" text-anchor="{a}">{e}</text>')

    def band(self, x, y, w, h, title, col="#95a5a6"):
        self.p.insert(0, f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" '
                         f'fill="none" stroke="{col}" stroke-width="1.2" '
                         f'stroke-dasharray="5,4"/>')
        self.p.insert(1, f'<text x="{x+10}" y="{y+16}" font-family="{F}" font-size="10.5" '
                         f'font-weight="bold" fill="{col}">{title}</text>')

    # ---------- 錨點：一律由框幾何計算 ----------
    def A(self, nid, side, f=0.5):
        x, y, w, h = self.n[nid]
        return {"t": (x+w*f, y), "b": (x+w*f, y+h),
                "l": (x, y+h*f), "r": (x+w, y+h*f)}[side]

    # ---------- 連線 ----------
    def link(self, a, asd, b, bsd, af=0.5, bf=0.5, label=None, col="#5d6d7e",
             dash=False, via=None, lpos=None):
        """
        a/b 為節點 id，asd/bsd 為邊（t/b/l/r）。路徑自動正交繞行，
        端點必定落在框邊上。via 可指定中間的 x 或 y（依方向決定）。
        """
        p1, p2 = self.A(a, asd, af), self.A(b, bsd, bf)
        pts = self._route(p1, asd, p2, bsd, via)
        d = "M " + " L ".join(f"{round(x,1)} {round(y,1)}" for x, y in pts)
        da = ' stroke-dasharray="6,4"' if dash else ""
        self.p.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.6"'
                      f'{da} marker-end="url(#ah)"/>')
        if label:
            lx, ly, la = lpos if lpos else self._lab(pts)
            self.txt(lx, ly, label, fs=10, col=col, a=la, bold=True)
        return pts

    @staticmethod
    def _route(p1, s1, p2, s2, via):
        x1, y1 = p1
        x2, y2 = p2
        V, H = "tb", "lr"
        if s1 in V and s2 in V:
            if abs(x1-x2) < 0.6:
                return [p1, p2]
            my = via if via is not None else (y1+y2)/2
            return [p1, (x1, my), (x2, my), p2]
        if s1 in H and s2 in H:
            if abs(y1-y2) < 0.6:
                return [p1, p2]
            mx = via if via is not None else (x1+x2)/2
            return [p1, (mx, y1), (mx, y2), p2]
        if s1 in V and s2 in H:      # 先垂直後水平
            return [p1, (x1, y2), p2]
        return [p1, (x2, y1), p2]    # 先水平後垂直

    @staticmethod
    def _lab(pts):
        # 取最長的線段中點
        best, bl = None, -1
        for i in range(len(pts)-1):
            (ax, ay), (bx, by) = pts[i], pts[i+1]
            L = abs(bx-ax) + abs(by-ay)
            if L > bl:
                bl, best = L, ((ax+bx)/2, (ay+by)/2, abs(bx-ax) > abs(by-ay))
        cx, cy, horiz = best
        return (cx, cy-7, "middle") if horiz else (cx+7, cy+4, "start")

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
                f'width="{self.w}" height="{self.h}">'
                '<defs><marker id="ah" viewBox="0 0 10 10" refX="9.5" refY="5" '
                'markerWidth="6" markerHeight="6" orient="auto">'
                '<path d="M 0 0 L 10 5 L 0 10 z" fill="#5d6d7e"/></marker></defs>'
                + "".join(self.p) + "</svg>")

    def save(self, path):
        open(path, "w").write(self.render())

    # ---------- 自我檢查 ----------
    def verify(self, name):
        """檢查每個節點是否超出畫布、是否互相重疊。"""
        errs = []
        items = list(self.n.items())
        for nid, (x, y, w, h) in items:
            if x < 0 or y < 0 or x+w > self.w or y+h > self.h:
                errs.append(f"{nid} 超出畫布 ({x},{y},{w},{h}) vs {self.w}x{self.h}")
        for i in range(len(items)):
            for j in range(i+1, len(items)):
                a, (ax, ay, aw, ah) = items[i]
                b, (bx, by, bw, bh) = items[j]
                if ax < bx+bw and bx < ax+aw and ay < by+bh and by < ay+ah:
                    errs.append(f"{a} 與 {b} 重疊")
        if errs:
            print(f"  !! {name}: " + "; ".join(errs))
        else:
            print(f"  ok {name}")
        return not errs

    def link_raw(self, pts, col="#5d6d7e", dash=False):
        """以明確座標畫箭頭。僅供 wireframe 使用；流程圖一律用 link()。"""
        d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
        da = ' stroke-dasharray="6,4"' if dash else ""
        self.p.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.6"'
                      f'{da} marker-end="url(#ah)"/>')

    def line(self, x1, y1, x2, y2, col="#c3cbd2", w=1):
        self.p.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                      f'stroke="{col}" stroke-width="{w}"/>')
