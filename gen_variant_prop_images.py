"""Generate illustration PNGs for the variant-prop dedup blog post.

Palette aligned with the risotto solarized-light theme. Output -> static/images/.
"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "static", "images")
os.makedirs(OUT, exist_ok=True)

# --- palette (solarized-light family) ---
PAPER   = (251, 247, 238)
CARD    = (255, 255, 255)
INK     = (43, 58, 66)
MUTED   = (124, 142, 148)
LINE    = (224, 216, 199)
BLUE    = (38, 139, 210)
BLUE_BG = (224, 240, 250)
GREEN   = (133, 153, 0)
GREEN_BG= (235, 240, 214)
TEAL    = (42, 161, 152)
RED     = (220, 50, 47)
RED_BG  = (250, 226, 224)
ORANGE  = (203, 75, 22)

FONTS = "C:/Windows/Fonts"
def font(name, size):
    return ImageFont.truetype(os.path.join(FONTS, name), size)

H1   = font("segoeuib.ttf", 40)
H2   = font("segoeuib.ttf", 30)
B    = font("segoeui.ttf", 24)
BB   = font("segoeuib.ttf", 24)
SM   = font("segoeui.ttf", 20)
SMB  = font("segoeuib.ttf", 20)
MONO = font("consola.ttf", 21)
MONOB= font("consolab.ttf", 21)
TINY = font("segoeui.ttf", 17)

def canvas(w, h, bg=PAPER):
    img = Image.new("RGB", (w, h), bg)
    return img, ImageDraw.Draw(img)

def rrect(d, box, r, fill=None, outline=None, width=2):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

def center(d, cx, y, text, fnt, fill=INK):
    w = d.textlength(text, font=fnt)
    d.text((cx - w / 2, y), text, font=fnt, fill=fill)

def chip(d, x, y, text, fnt, fg, bg, padx=14, pady=7):
    w = d.textlength(text, font=fnt)
    rrect(d, [x, y, x + w + 2 * padx, y + fnt.size + 2 * pady], 10, fill=bg)
    d.text((x + padx, y + pady), text, font=fnt, fill=fg)
    return x + w + 2 * padx

def arrow(d, x1, y1, x2, y2, color=MUTED, width=3, head=11):
    d.line([(x1, y1), (x2, y2)], fill=color, width=width)
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    for s in (-0.5, 0.5):
        d.line([(x2, y2),
                (x2 - head * math.cos(ang + s), y2 - head * math.sin(ang + s))],
               fill=color, width=width)


# ============================================================
# 1. COVER — one CRUD engine, two dashboards
# ============================================================
def cover():
    W, H = 1280, 640
    img, d = canvas(W, H)
    d.rectangle([0, 0, W, 6], fill=BLUE)
    center(d, W / 2, 44, "One CRUD engine, two dashboards", H1, INK)
    center(d, W / 2, 100, "A single component, switched by a  variant  prop", B, MUTED)

    # central engine box
    ex, ey, ew, eh = W/2 - 220, 175, 440, 120
    rrect(d, [ex, ey, ex+ew, ey+eh], 16, fill=CARD, outline=BLUE, width=3)
    center(d, W/2, ey+22, "DashboardPostinganModule", BB, INK)
    center(d, W/2, ey+58, "list · create · edit · delete · detail", SM, MUTED)
    chip(d, W/2-118, ey+eh-6, "variant: 'card' | 'widget'", MONO, BLUE, BLUE_BG)

    # two outputs
    ly, lh = 400, 180
    # left: admin card
    lx, lw = 110, 470
    rrect(d, [lx, ly, lx+lw, ly+lh], 14, fill=CARD, outline=LINE, width=2)
    d.text((lx+24, ly+20), "Admin dashboard", font=BB, fill=INK)
    chip(d, lx+24, ly+58, "variant = 'card'", MONO, GREEN, GREEN_BG)
    rrect(d, [lx+24, ly+108, lx+lw-24, ly+lh-22], 10, fill=PAPER, outline=LINE, width=2)
    d.text((lx+40, ly+122), "rounded-2xl  border  shadow-sm", font=MONO, fill=MUTED)
    # right: GB widget
    rx = W - 110 - lw
    rrect(d, [rx, ly, rx+lw, ly+lh], 14, fill=CARD, outline=LINE, width=2)
    d.text((rx+24, ly+20), "Guru Besar dashboard", font=BB, fill=INK)
    chip(d, rx+24, ly+58, "variant = 'widget'", MONO, BLUE, BLUE_BG)
    rrect(d, [rx+24, ly+108, rx+lw-24, ly+lh-22], 10, fill=PAPER, outline=LINE, width=2)
    d.text((rx+40, ly+122), "WidgetShell  (icon + action slot)", font=MONO, fill=MUTED)

    arrow(d, W/2-70, ey+eh+8, lx+lw/2, ly-8, color=BLUE, width=3)
    arrow(d, W/2+70, ey+eh+8, rx+lw/2, ly-8, color=BLUE, width=3)
    img.save(os.path.join(OUT, "variant-prop-cover.png"))


# ============================================================
# 2. BEFORE / AFTER lines of code
# ============================================================
def before_after():
    W, H = 1280, 540
    img, d = canvas(W, H)
    center(d, W/2, 34, "PostinganPanelWidget.tsx — before vs after", H2, INK)

    colw = 560
    lx, rx, top = 70, W-70-colw, 110
    # before
    rrect(d, [lx, top, lx+colw, top+360], 14, fill=CARD, outline=RED, width=2)
    chip(d, lx+22, top+20, "BEFORE", SMB, RED, RED_BG)
    d.text((lx+colw-150, top+22), "681 lines", font=BB, fill=RED)
    bar_y = top+70
    rrect(d, [lx+22, bar_y, lx+colw-22, bar_y+26], 7, fill=RED_BG)
    rrect(d, [lx+22, bar_y, lx+colw-22, bar_y+26], 7, fill=RED, outline=None)
    d.text((lx+22, bar_y+44),
           "A full copy of the CRUD logic, restyled.", font=SM, fill=MUTED)
    snippet = [
        "export default function PostinganPanelWidget() {",
        "  const [page, setPage] = useState(1)",
        "  const [response, setResponse] = useState()",
        "  // ...640 more lines duplicated from",
        "  //    the admin DashboardPostinganModule",
        "  return (<section> ... </section>)",
        "}",
    ]
    yy = bar_y+82
    for ln in snippet:
        d.text((lx+24, yy), ln, font=MONO, fill=INK if not ln.strip().startswith("//") else MUTED)
        yy += 30

    # after
    rrect(d, [rx, top, rx+colw, top+360], 14, fill=CARD, outline=GREEN, width=2)
    chip(d, rx+22, top+20, "AFTER", SMB, GREEN, GREEN_BG)
    d.text((rx+colw-130, top+22), "4 lines", font=BB, fill=GREEN)
    rrect(d, [rx+22, bar_y, rx+colw-22, bar_y+26], 7, fill=GREEN_BG)
    rrect(d, [rx+22, bar_y, rx+22+int((colw-44)*0.018), bar_y+26], 7, fill=GREEN)
    d.text((rx+22, bar_y+44),
           "A thin wrapper that configures the engine.", font=SM, fill=MUTED)
    after = [
        "import DashboardPostinganModule from",
        "  '@/src/features/postingan/components/...'",
        "",
        "export default function PostinganPanelWidget() {",
        "  return <DashboardPostinganModule",
        "    role=\"GURU_BESAR\" variant=\"widget\" />",
        "}",
    ]
    yy = bar_y+82
    for ln in after:
        d.text((rx+24, yy), ln, font=MONO, fill=INK)
        yy += 30

    center(d, W/2, 500, "-680 deletions · +99 insertions · 100% coverage kept", BB, ORANGE)
    img.save(os.path.join(OUT, "variant-prop-before-after.png"))


# ============================================================
# 3. WidgetShell composition slots
# ============================================================
def widgetshell():
    W, H = 1280, 520
    img, d = canvas(W, H)
    center(d, W/2, 34, "WidgetShell — composition through slots", H2, INK)
    center(d, W/2, 84, "The module passes ReactNode into named props; WidgetShell owns the chrome.", SM, MUTED)

    sx, sy, sw, sh = 150, 140, W-300, 320
    rrect(d, [sx, sy, sx+sw, sy+sh], 16, fill=CARD, outline=BLUE, width=3)
    d.text((sx+24, sy+16), "<WidgetShell aria-label=\"Postingan Saya\">", font=MONOB, fill=BLUE)

    # header row slots
    hy = sy+66
    # icon slot
    rrect(d, [sx+24, hy, sx+24+70, hy+70], 12, fill=BLUE_BG, outline=BLUE, width=2)
    center(d, sx+24+35, hy+24, "icon", SMB, BLUE)
    # title block
    tx = sx+24+90
    d.text((tx, hy+6), "title  /  subtitle", font=BB, fill=INK)
    d.text((tx, hy+40), "\"Postingan Saya\"  ·  \"Kelola artikel...\"", font=SM, fill=MUTED)
    # action slot
    aw = 300
    ax = sx+sw-24-aw
    rrect(d, [ax, hy, ax+aw, hy+70], 12, fill=GREEN_BG, outline=GREEN, width=2)
    center(d, ax+aw/2, hy+12, "action slot", SMB, GREEN)
    center(d, ax+aw/2, hy+40, "DashboardPostinganHeaderAction", TINY, MUTED)

    # children slot
    cy = hy+96
    rrect(d, [sx+24, cy, sx+sw-24, sy+sh-20], 12, fill=PAPER, outline=LINE, width=2)
    center(d, W/2, cy+18, "children", SMB, INK)
    center(d, W/2, cy+52, "list  +  pagination   (the shared CRUD body)", SM, MUTED)

    center(d, W/2, 480, "Same body reused by the admin card via a plain <section> wrapper.", SM, MUTED)
    img.save(os.path.join(OUT, "variant-prop-widgetshell.png"))


# ============================================================
# 4. SonarQube duplication gate
# ============================================================
def sonar_gate():
    W, H = 1280, 420
    img, d = canvas(W, H)
    center(d, W/2, 34, "The duplication gate that forced the refactor", H2, INK)

    bw, bh, top = 480, 230, 110
    lx = 90
    rx = W-90-bw
    # fail
    rrect(d, [lx, top, lx+bw, top+bh], 14, fill=CARD, outline=RED, width=3)
    d.text((lx+24, top+20), "Quality Gate", font=BB, fill=INK)
    chip(d, lx+bw-130, top+22, "FAILED", SMB, CARD, RED)
    d.text((lx+24, top+76), "Duplicated Lines", font=B, fill=INK)
    d.text((lx+bw-150, top+76), "≈ 11.4%", font=BB, fill=RED)
    rrect(d, [lx+24, top+120, lx+bw-24, top+144], 7, fill=RED_BG)
    rrect(d, [lx+24, top+120, lx+24+int((bw-48)*0.38), top+144], 7, fill=RED)
    d.text((lx+24, top+160), "Two near-identical Postingan panels", font=SM, fill=MUTED)
    d.text((lx+24, top+186), "tripped the > 3% threshold.", font=SM, fill=MUTED)

    arrow(d, lx+bw+18, top+bh/2, rx-18, top+bh/2, color=BLUE, width=4, head=16)
    center(d, W/2, top+bh/2-46, "variant", SMB, BLUE)
    center(d, W/2, top+bh/2-22, "prop", SMB, BLUE)

    # pass
    rrect(d, [rx, top, rx+bw, top+bh], 14, fill=CARD, outline=GREEN, width=3)
    d.text((rx+24, top+20), "Quality Gate", font=BB, fill=INK)
    chip(d, rx+bw-120, top+22, "PASSED", SMB, CARD, GREEN)
    d.text((rx+24, top+76), "Duplicated Lines", font=B, fill=INK)
    d.text((rx+bw-130, top+76), "0.0%", font=BB, fill=GREEN)
    rrect(d, [rx+24, top+120, rx+bw-24, top+144], 7, fill=GREEN_BG)
    rrect(d, [rx+24, top+120, rx+24+10, top+144], 7, fill=GREEN)
    d.text((rx+24, top+160), "One engine, one source of truth,", font=SM, fill=MUTED)
    d.text((rx+24, top+186), "behaviour unchanged.", font=SM, fill=MUTED)
    img.save(os.path.join(OUT, "variant-prop-sonar-gate.png"))


cover()
before_after()
widgetshell()
sonar_gate()
print("done ->", OUT)
for f in ["variant-prop-cover.png", "variant-prop-before-after.png",
          "variant-prop-widgetshell.png", "variant-prop-sonar-gate.png"]:
    p = os.path.join(OUT, f)
    print(f, os.path.getsize(p), "bytes")
