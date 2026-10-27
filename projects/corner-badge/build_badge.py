# 角标生成器（参数化）
# 用法: python build_badge.py <直角圆角> <输出名> [配色]
import math, random, sys, os

# 输出始终写到本脚本所在目录，与当前工作目录无关
OUTDIR = os.path.dirname(os.path.abspath(__file__))

W, H, PAD = 500, 190, 32
R, R_OUT, HALO = 32, 35, 76
XOFF, NOTE = 42, 22
T1, T2, T2LS = 18, 7.5, 2.2

CR = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
OUT = sys.argv[2] if len(sys.argv) > 2 else "A8-corner30"
PAL = sys.argv[3] if len(sys.argv) > 3 else "cinnabar"

PALETTES = {
    "cinnabar": dict(acc="#E8503F", c1="#E8503F", c2="#9B2E8F", c3="#241558",
                     w1="#241828", w2="#100C16", w3="#06060B", txt="#F7F3EC", rim="#FFB3A0"),
    "cyan":     dict(acc="#22E1FF", c1="#22E1FF", c2="#1A6FD4", c3="#061A3A",
                     w1="#0E2833", w2="#08161F", w3="#03070C", txt="#EAF9FF", rim="#A7F0FF"),
    "magenta":  dict(acc="#FF2D95", c1="#FF2D95", c2="#8A2BE2", c3="#26063F",
                     w1="#2A0F28", w2="#150817", w3="#06030A", txt="#FFF0F7", rim="#FFA8D2"),
    "acid":     dict(acc="#7CFF5A", c1="#7CFF5A", c2="#17A87A", c3="#04241C",
                     w1="#0F2A1C", w2="#081710", w3="#030806", txt="#F2FFF0", rim="#C8FFB8"),
}
P = PALETTES[PAL]
ACC, TXT = P["acc"], P["txt"]

A = (0.0, 18.0); B = (500.0, 190.0)
DX, DY = B[0] - A[0], B[1] - A[1]
L = math.hypot(DX, DY)
UX, UY = DX / L, DY / L
NX, NY = -UY, UX

TRI = f"M0,18 L0,{190-CR} Q0,190 {CR},190 L500,190 Z"
LEGS = f"M0,18 L0,{190-CR} Q0,190 {CR},190 L500,190"


def spectrum_bars():
    rnd = random.Random(20261025)
    out, d = [], 3
    while d < L - 3:
        px, py = A[0] + UX * d, A[1] + UY * d
        tt = d / L
        env = min(1, min(tt, 1 - tt) * 14 + 0.12)
        h = (2 + rnd.random() * 28) * env
        x2, y2 = px - NX * h, py - NY * h
        out.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                   f'stroke="#000000" stroke-width="3.8" opacity="0.5" stroke-linecap="round"/>')
        out.append(f'<line x1="{px:.1f}" y1="{py:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                   f'stroke="{ACC}" stroke-width="2" opacity="{0.34+0.58*(h/30):.2f}" stroke-linecap="round"/>')
        d += 4.6
    return "".join(out)


def timeline(playhead=0.36, elapsed="01:42", total="04:08"):
    """斜边内侧的胶囊形时间轴：圆角矩形轨道 + 已播放段 + 时间码"""
    inset = 7.5        # 轨道中心距斜边 —— 上沿离边框仍约 6px
    thick = 3          # 胶囊厚度
    d0, d1 = 12, 480   # 起止（保证整条胶囊都在形状内）

    ax = A[0] + UX * d0 + NX * inset
    ay = A[1] + UY * d0 + NY * inset
    bx = A[0] + UX * d1 + NX * inset
    by = A[1] + UY * d1 + NY * inset

    span = d1 - d0
    ex = ax + UX * span * playhead
    ey = ay + UY * span * playhead

    p = []
    # 轨道（胶囊）
    p.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
             f'stroke="#FFFFFF" stroke-opacity="0.16" stroke-width="{thick}" stroke-linecap="round"/>')
    # 已播放段（同为胶囊，端头自然形成圆角）
    p.append(f'<line x1="{ax:.1f}" y1="{ay:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" '
             f'stroke="{ACC}" stroke-width="{thick}" stroke-linecap="round" opacity="0.95"/>')
    # 播放位置微光
    p.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="7" fill="{ACC}" opacity="0.18" filter="url(#soft)"/>')

    # 时间码：贴在胶囊下方，沿斜边平行
    d = d1 * playhead + 14
    tx = A[0] + UX * d + NX * 26
    ty = A[1] + UY * d + NY * 26
    p.append(
        f'<g transform="translate({tx:.1f},{ty:.1f}) rotate(18.98)">'
        f'<text font-family="Segoe UI, Microsoft YaHei, sans-serif" font-size="6.6" '
        f'font-weight="600" letter-spacing="0.7">'
        f'<tspan fill="{ACC}">{elapsed}</tspan>'
        f'<tspan fill="#FFFFFF" fill-opacity="0.42">  /  {total}</tspan>'
        f'</text></g>'
    )
    return "".join(p)


def svg():
    cx, cy = 74, 118
    return f'''
<svg class="badge" width="{W+PAD*2}" height="{H+PAD*2}" viewBox="{-PAD} {-PAD} {W+PAD*2} {H+PAD*2}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="ink" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{P['w1']}"/><stop offset="0.42" stop-color="{P['w2']}"/><stop offset="1" stop-color="{P['w3']}"/>
    </linearGradient>
    <linearGradient id="cover" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{P['c1']}"/><stop offset="0.52" stop-color="{P['c2']}"/><stop offset="1" stop-color="{P['c3']}"/>
    </linearGradient>
    <radialGradient id="gloss" cx="0.3" cy="0.22" r="0.8">
      <stop offset="0" stop-color="#FFF" stop-opacity="0.38"/><stop offset="0.55" stop-color="#FFF" stop-opacity="0.03"/><stop offset="1" stop-color="#FFF" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{ACC}" stop-opacity="0.18"/><stop offset="1" stop-color="{ACC}" stop-opacity="0"/>
    </radialGradient>
    <filter id="sh" x="-15%" y="-15%" width="140%" height="140%"><feGaussianBlur stdDeviation="6"/></filter>
    <filter id="gl" x="-25%" y="-25%" width="150%" height="150%">
      <feGaussianBlur stdDeviation="1.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="soft" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="6"/></filter>
    <filter id="rim" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="9"/></filter>
    <clipPath id="clip"><path d="{TRI}"/></clipPath>
  </defs>

  <path d="{TRI}" fill="#000" opacity="0.55" filter="url(#sh)" transform="translate(4,5)"/>
  <path d="{TRI}" fill="url(#ink)" opacity="0.97"/>

  <g clip-path="url(#clip)">
    <path d="M{A[0]},{A[1]} L{B[0]},{B[1]}" stroke="{TXT}" stroke-width="52" opacity="0.07" filter="url(#rim)"/>
    <path d="M{A[0]},{A[1]} L{B[0]},{B[1]}" stroke="{P['rim']}" stroke-width="18" opacity="0.055" filter="url(#rim)"/>

    <circle cx="{cx}" cy="{cy}" r="{HALO}" fill="url(#halo)"/>
    <circle cx="{cx}" cy="{cy}" r="{R}" fill="url(#cover)"/>
    <circle cx="{cx}" cy="{cy}" r="{R}" fill="url(#gloss)"/>
    <circle cx="{cx}" cy="{cy}" r="{R_OUT}" fill="none" stroke="{ACC}" stroke-width="1" opacity="0.45"/>
    <circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{TXT}" stroke-width="1.4" opacity="0.88"/>
    <text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="central"
          font-family="Segoe UI Symbol, Segoe UI, sans-serif" font-size="{NOTE}" fill="#FFF" opacity="0.92">&#9834;</text>
    <g transform="translate({cx},{cy}) rotate(18.98)">
      <text class="t1" x="{XOFF}" y="-8">Neon Tokyo</text>
      <text class="t2" x="{XOFF+1}" y="10">AMANE · 雨音</text>
    </g>
    <path d="{LEGS}" fill="none" stroke="#FFFFFF" stroke-opacity="0.18" stroke-width="2.6"/>
    <path d="M{A[0]},{A[1]} L{B[0]},{B[1]}" stroke="{ACC}" stroke-width="1.6" opacity="0.8" fill="none"/>
  </g>

  <g filter="url(#gl)">{spectrum_bars()}</g>
  <g clip-path="url(#clip)">{timeline()}</g>
</svg>'''


bg = "radial-gradient(1000px 700px at 68% 26%, #33404F 0%, #1D2632 45%, #0B1017 100%)"

html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
  html,body{{margin:0;padding:0;width:1920px;height:1080px;overflow:hidden}}
  body{{background:{bg};}}
  .badge{{position:absolute;left:0;bottom:0;display:block}}
  .t1{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-size:{T1}px;font-weight:700;fill:{TXT};letter-spacing:.2px}}
  .t2{{font-family:"Segoe UI","Microsoft YaHei",sans-serif;font-size:{T2}px;font-weight:700;fill:{ACC};letter-spacing:{T2LS}px}}
</style></head><body>{svg()}</body></html>'''

with open(os.path.join(OUTDIR, OUT + ".html"), "w", encoding="utf-8") as f:
    f.write(html)
print("ok", OUT, CR, PAL, "->", OUTDIR)
