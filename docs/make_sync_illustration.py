"""Generate docs/intervals-sync.svg: python docs/make_sync_illustration.py docs/intervals-sync.svg"""

import math, sys

# (start_km, end_km, watts): blocks merged from the demo 40 km plan
BLOCKS = [(0, 2, 330), (2, 6, 290), (6, 8, 185), (8, 9.5, 300), (9.5, 12, 285), (12, 14, 275),
          (14, 18, 245), (18, 20, 305), (20, 22, 210), (22, 26, 290), (26, 30, 255), (30, 32, 125),
          (32, 34, 350), (34, 40, 255)]
CURRENT = 3  # 0-based: the climb block

def elev(km):
    out = min(km, 40 - km) * 1000
    climb = min(max(out - 8000, 0), 1500) * 0.06
    return 150 + 25 * math.sin(out / 1800) + 10 * math.sin(out / 430) + climb

INK, INK2, MUTED, LINE = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
SURF, CARD, BLUE, BLUE_L, TERRAIN = "#fcfcfb", "#ffffff", "#2a78d6", "#cde2fb", "#d6d5d0"
SCREEN, SCREEN_INK, SCREEN_INK2, BAND = "#16181c", "#f4f4f2", "#a9adb5", "#3987e5"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

o = []
def add(s): o.append(s)

def profile(x, y, w, h, fill, km0=0, km1=40, n=160):
    zs = [elev(km0 + (km1 - km0) * i / n) for i in range(n + 1)]
    lo, hi = 110, 260
    pts = [(x + w * i / n, y + h - h * (z - lo) / (hi - lo)) for i, z in enumerate(zs)]
    d = f"M{x},{y+h} " + " ".join(f"L{px:.1f},{py:.1f}" for px, py in pts) + f" L{x+w},{y+h} Z"
    add(f'<path d="{d}" fill="{fill}"/>')

def steps(x, y, w, h, color, pmax=380, width=2.5, highlight=None):
    """power blocks as a step line (y grows down)."""
    sx = lambda km: x + w * km / 40
    sy = lambda p: y + h - h * p / pmax
    if highlight is not None:
        a, b, p = BLOCKS[highlight]
        add(f'<rect x="{sx(a):.1f}" y="{y}" width="{sx(b)-sx(a):.1f}" height="{h}" fill="{BLUE_L}" opacity="0.6"/>')
    d = []
    for i, (a, b, p) in enumerate(BLOCKS):
        d.append(f"{'M' if i == 0 else 'L'}{sx(a):.1f},{sy(p):.1f} L{sx(b):.1f},{sy(p):.1f}")
    add(f'<path d="{" ".join(d)}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"/>')

def text(x, y, s, size=14, color=INK, weight=400, anchor="start", family=FONT, extra=""):
    add(f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" fill="{color}" '
        f'font-weight="{weight}" text-anchor="{anchor}" {extra}>{s}</text>')

def card(x, y, w, h, num, title, subtitle):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{CARD}" stroke="{LINE}" stroke-width="1.5"/>')
    add(f'<circle cx="{x+30}" cy="{y+34}" r="14" fill="{BLUE}"/>')
    text(x + 30, y + 39, num, 14, "#ffffff", 700, "middle")
    text(x + 54, y + 32, title, 17, INK, 650)
    text(x + 54, y + 51, subtitle, 13, INK2)

def arrow(x0, x1, y, label1, label2):
    add(f'<line x1="{x0}" y1="{y}" x2="{x1-10}" y2="{y}" stroke="{INK2}" stroke-width="2"/>')
    add(f'<path d="M{x1-12},{y-6} L{x1},{y} L{x1-12},{y+6} Z" fill="{INK2}"/>')
    text((x0 + x1) / 2, y - 26, label1, 12.5, INK, 600, "middle")
    text((x0 + x1) / 2, y - 10, label2, 12, INK2, 400, "middle")

W, H = 1280, 640
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" '
    f'aria-label="WattOptimizer plan synced through intervals.icu to a bike computer">')
add(f'<rect width="{W}" height="{H}" rx="18" fill="{SURF}"/>')
text(40, 52, "From pacing plan to bike computer", 24, INK, 700)
text(40, 78, "WattOptimizer pushes the plan to intervals.icu as a workout with distance-based steps; "
     "intervals.icu syncs it to your device, which advances each step by distance.", 14, INK2)

# ---------- 1. plan ----------
X1, Y, CW, CH = 40, 110, 350, 440
card(X1, Y, CW, CH, "1", "Plan", "WattOptimizer")
add(f'<rect x="{X1+20}" y="{Y+76}" width="{CW-40}" height="62" rx="8" fill="#f3f3f1"/>')
text(X1 + 32, Y + 101, "$ wattopt plan race.gpx \\", 12.5, INK, 400, family=MONO)
text(X1 + 46, Y + 123, "--weather --export intervals", 12.5, INK, 400, family=MONO)
text(X1 + 20, Y + 160, "elevation", 11.5, MUTED)
profile(X1 + 20, Y + 166, CW - 40, 40, TERRAIN)
text(X1 + 20, Y + 228, "target power, merged into 14 steps", 11.5, MUTED)
add(f'<line x1="{X1+20}" y1="{Y+304}" x2="{X1+CW-20}" y2="{Y+304}" stroke="{LINE}"/>')
steps(X1 + 20, Y + 234, CW - 40, 70, BLUE)
text(X1 + 20, Y + 322, "0 km", 11, MUTED); text(X1 + CW - 20, Y + 322, "40 km", 11, MUTED, anchor="end")
rows = [("km", "target", "length"), ("8.0", "300 W", "1.5 km"), ("9.5", "285 W", "2.5 km"), ("12.0", "275 W", "2.0 km")]
for i, (a, b, c) in enumerate(rows):
    yy = Y + 356 + 21 * i
    col = MUTED if i == 0 else INK
    text(X1 + 20, yy, a, 12.5, col, 600 if i == 0 else 400)
    text(X1 + 150, yy, b, 12.5, col, 600 if i == 0 else 400, "end")
    text(X1 + 250, yy, c, 12.5, col, 600 if i == 0 else 400, "end")

# ---------- 2. intervals.icu ----------
X2 = 465
card(X2, Y, CW, CH, "2", "Sync", "intervals.icu calendar")
add(f'<rect x="{X2+20}" y="{Y+76}" width="{CW-40}" height="232" rx="10" fill="#f3f3f1"/>')
text(X2 + 36, Y + 102, "SAT 12 JUL", 11.5, INK2, 700, extra='letter-spacing="1"')
add(f'<rect x="{X2+32}" y="{Y+114}" width="{CW-64}" height="180" rx="8" fill="{CARD}" stroke="{LINE}"/>')
add(f'<rect x="{X2+32}" y="{Y+114}" width="5" height="180" rx="2" fill="{BLUE}"/>')
text(X2 + 50, Y + 139, "Race: bike leg, 40 km TT", 14, INK, 650)
text(X2 + 50, Y + 158, "Planned 1:00:32 · 267 W avg · 14 steps", 12, INK2)
# workout chart: bar width = distance, height = power
bx, by, bw, bh = X2 + 50, Y + 172, CW - 100, 70
for i, (a, b, p) in enumerate(BLOCKS):
    x0 = bx + bw * a / 40 + 1; x1 = bx + bw * b / 40 - 1
    hh = bh * p / 380
    add(f'<rect x="{x0:.1f}" y="{by+bh-hh:.1f}" width="{x1-x0:.1f}" height="{hh:.1f}" rx="2" fill="{BLUE}" opacity="{1 if i == CURRENT else 0.55}"/>')
text(bx, by + bh + 20, "- 1.5km 295-305W   climb, sit up", 11.5, INK, family=MONO)
text(bx, by + bh + 38, "- 2.5km 280-290W", 11.5, INK2, family=MONO)
# sync targets
text(X2 + 20, Y + 340, "auto-syncs planned workouts to", 12.5, INK2)
for i, name in enumerate(["Garmin Connect", "Wahoo", "COROS*"]):
    px = X2 + 20 + [0, 132, 214][i]; pw = [120, 70, 76][i]
    add(f'<rect x="{px}" y="{Y+354}" width="{pw}" height="30" rx="15" fill="{CARD}" stroke="{LINE}" stroke-width="1.5"/>')
    text(px + pw / 2, Y + 374, name, 12.5, INK, 600, "middle")
text(X2 + 20, Y + 412, "Distance steps keep the targets tied to the", 12, INK2)
text(X2 + 20, Y + 429, "course, not the clock.", 12, INK2)

# ---------- 3. bike computer ----------
X3 = 890
card(X3, Y, CW, CH, "3", "Ride", "bike computer, workout screen")
DX, DY, DW, DH = X3 + 70, Y + 72, 210, 352
add(f'<rect x="{DX}" y="{DY}" width="{DW}" height="{DH}" rx="26" fill="#2b2e33"/>')
add(f'<rect x="{DX+DW}" y="{DY+60}" width="4" height="36" rx="2" fill="#2b2e33"/>')
add(f'<rect x="{DX-4}" y="{DY+60}" width="4" height="36" rx="2" fill="#2b2e33"/>')
SX, SY, SW, SH = DX + 14, DY + 18, DW - 28, DH - 50
add(f'<rect x="{SX}" y="{SY}" width="{SW}" height="{SH}" rx="10" fill="{SCREEN}"/>')
cx = SX + SW / 2
text(SX + 12, SY + 22, "STEP 4/14", 11, SCREEN_INK2, 700, extra='letter-spacing="0.8"')
text(SX + SW - 12, SY + 22, "0.9 km left", 11, SCREEN_INK2, 600, "end")
add(f'<line x1="{SX+10}" y1="{SY+32}" x2="{SX+SW-10}" y2="{SY+32}" stroke="#2c3036"/>')
text(cx, SY + 56, "TARGET", 10.5, SCREEN_INK2, 700, "middle", extra='letter-spacing="1.2"')
text(cx, SY + 78, "295–305 W", 18, SCREEN_INK, 650, "middle")
text(cx, SY + 136, "298", 54, SCREEN_INK, 700, "middle")
text(cx, SY + 156, "POWER (3s) W", 10.5, SCREEN_INK2, 700, "middle", extra='letter-spacing="1"')
# gauge: 200..400 W, band 295-305
gx, gy, gw = SX + 14, SY + 172, SW - 28
gpos = lambda p: gx + gw * (p - 200) / 200
add(f'<rect x="{gx}" y="{gy}" width="{gw}" height="10" rx="5" fill="#2c3036"/>')
add(f'<rect x="{gpos(295):.1f}" y="{gy}" width="{gpos(305)-gpos(295):.1f}" height="10" fill="{BAND}"/>')
add(f'<path d="M{gpos(298)-6:.1f},{gy-8} L{gpos(298)+6:.1f},{gy-8} L{gpos(298):.1f},{gy-1} Z" fill="{SCREEN_INK}"/>')
add(f'<line x1="{SX+10}" y1="{SY+198}" x2="{SX+SW-10}" y2="{SY+198}" stroke="#2c3036"/>')
text(SX + 12, SY + 218, "NEXT", 10.5, SCREEN_INK2, 700, extra='letter-spacing="1"')
text(SX + SW - 12, SY + 218, "285 W · 2.5 km", 12.5, SCREEN_INK, 600, "end")
# course profile with position marker
profile(SX + 12, SY + 232, SW - 24, 34, "#3a3f46")
mk = SX + 12 + (SW - 24) * 8.6 / 40
add(f'<line x1="{mk:.1f}" y1="{SY+228}" x2="{mk:.1f}" y2="{SY+266}" stroke="{BAND}" stroke-width="2"/>')
add(f'<circle cx="{mk:.1f}" cy="{SY+228}" r="3.5" fill="{BAND}"/>')
text(SX + 12, SY + 286, "km 8.6", 10.5, SCREEN_INK2, 600)
text(SX + SW - 12, SY + 286, "seated upright", 10.5, SCREEN_INK2, 600, "end")
add(f'<rect x="{DX+DW/2-22}" y="{DY+DH-22}" width="44" height="6" rx="3" fill="#3d4148"/>')

arrow(X1 + CW + 6, X2 - 6, Y + 230, "API upload", "workout steps")
arrow(X2 + CW + 6, X3 - 6, Y + 230, "device sync", "Garmin / Wahoo")

text(40, 590, "Planned feature: the --export intervals flag is on the roadmap and not implemented yet. "
     "Screen is an illustration, not any vendor's actual layout.", 12.5, INK2)
text(40, 610, "* Check COROS support for distance-based steps before race day. "
     "Multisport (triathlon) workouts on watches vary by model.", 12.5, INK2)
add("</svg>")
open(sys.argv[1], "w").write("\n".join(o))
