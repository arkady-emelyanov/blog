#!/usr/bin/env python3
"""Generates the overview picture of part 1: everything the lab runs, inside a
single Linux machine.

    .venv/bin/python tools/overview.py

Same look as the social cards (tools/social-cards.py: colours, fonts,
background); written to content/ai-lab/01-intro/overview.png at 2x.
"""
import importlib.util
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True   # no tools/__pycache__ from importing the card script
_spec = importlib.util.spec_from_file_location("cards", os.path.join(HERE, "social-cards.py"))
cards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cards)
from_cards = lambda *names: [getattr(cards, n) for n in names]
GREEN, TEXT, MUTED, PANEL, PANEL_EDGE, GPU_FILL, BG_TOP, BG_BOTTOM = from_cards(
    "GREEN", "TEXT", "MUTED", "PANEL", "PANEL_EDGE", "GPU_FILL", "BG_TOP", "BG_BOTTOM")

K = 2                      # render scale
W, H = 1200, 730           # layout size
OUT = os.path.join(HERE, "..", "content", "ai-lab", "01-intro", "overview.png")

bold = lambda s: cards.bold(s * K)
regular = lambda s: cards.regular(s * K)
mono = lambda s: cards.mono(s * K)
k = lambda *v: [x * K for x in v]
OY = -40                   # the diagram sits this far above its layout coordinates (one grid step)
c = lambda *v: [(x + (OY if i % 2 else 0)) * K for i, x in enumerate(v)]   # (x, y, ...) pairs

def background():
    img = Image.new("RGB", (W * K, H * K))
    d = ImageDraw.Draw(img)
    for y in range(H * K):
        t = y / (H * K - 1)
        d.line([(0, y), (W * K, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    grid = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(grid)
    for x in range(0, W, 40):
        g.line(k(x, 0, x, H), fill=(255, 255, 255, 8), width=K)
    for y in range(0, H, 40):
        g.line(k(0, y, W, y), fill=(255, 255, 255, 8), width=K)
    img.paste(grid, (0, 0), grid)
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(k(420, 140, 1000, 560), fill=(118, 185, 0, 34))
    glow = glow.filter(ImageFilter.GaussianBlur(90 * K))
    img.paste(glow, (0, 0), glow)
    return img


def node(d, x0, y0, x1, y1, title, lines=()):
    """A node, drawn like the GPU trays: panel, green edge, bold title; a node
    without lines has its title centred."""
    d.rounded_rectangle(c(x0, y0, x1, y1), radius=12 * K, fill=PANEL, outline=GREEN, width=2 * K)
    if not lines:
        d.text(c((x0 + x1) / 2, (y0 + y1) / 2), title, font=bold(16), fill=TEXT, anchor="mm")
        return
    d.text(c(x0 + 16, y0 + 12), title, font=bold(17), fill=TEXT)
    for i, line in enumerate(lines):
        d.text(c(x0 + 16, y0 + 42 + i * 24), line, font=regular(15), fill=MUTED)


def note(d, x, y, width, paragraphs):
    """Muted text wrapped to `width` (layout px), a blank line between paragraphs."""
    font = regular(14)
    for para in paragraphs:
        line = ""
        for word in para.split():
            trial = (line + " " + word).strip()
            if d.textlength(trial, font=font) / K > width and line:
                d.text(c(x, y), line, font=font, fill=MUTED)
                y, line = y + 22, word
            else:
                line = trial
        d.text(c(x, y), line, font=font, fill=MUTED)
        y += 22 + 14


def main():
    img = background()
    d = ImageDraw.Draw(img, "RGBA")

    d.text(k(40, 30), "AI Lab: Overview", font=bold(30), fill=TEXT)

    # The machine.
    d.rounded_rectangle(c(30, 140, 1170, 745), radius=22 * K, outline=GREEN, width=2 * K)

    # Platform services.
    d.text(c(60, 166), "platform", font=bold(15), fill=MUTED)
    node(d, 60, 192, 350, 282, "sched-login", ["user shells: sbatch, kubectl"])
    node(d, 60, 298, 350, 412, "sched-control", ["Slurm or k3s (Kueue)", "LDAP, Prometheus, Grafana"])
    node(d, 60, 428, 350, 518, "sched-storage", ["S3 (RustFS), JuiceFS"])

    # The NVL8 domain.
    d.text(c(390, 166), "NVL8 NVLink domain", font=bold(15), fill=GREEN)
    sx0, sy0, sx1, sy1 = 390, 192, 950, 282
    switches = [[sx0 + 20, sy0 + 44, sx0 + 270, sy0 + 78], [sx0 + 290, sy0 + 44, sx0 + 540, sy0 + 78]]
    trays = [(390, 360, 660, 500), (680, 360, 950, 500)]

    links = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(links)
    for tx0, ty0, tx1, _ in trays:
        for g in range(4):
            ax = tx0 + 34 + g * (tx1 - tx0 - 68) / 3
            for bx0, _, bx1, by1 in switches:
                ld.line(c(ax, ty0, (bx0 + bx1) / 2, by1), fill=(118, 185, 0, 90), width=2 * K)
    img.paste(links, (0, 0), links)
    d = ImageDraw.Draw(img, "RGBA")

    d.rounded_rectangle(c(sx0, sy0, sx1, sy1), radius=12 * K, fill=PANEL, outline=GREEN, width=2 * K)
    d.text(c(sx0 + 16, sy0 + 12), "NVLink switch tray", font=bold(17), fill=TEXT)
    for i, b in enumerate(switches):
        d.rounded_rectangle(c(*b), radius=8 * K, fill=GPU_FILL, outline=GREEN, width=2 * K)
        d.text(c((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), f"NVSwitch {i} · 72 ports", font=mono(14), fill=GREEN, anchor="mm")
    msg = "NVLinks"
    tw = d.textlength(msg, font=mono(14)) / K
    d.rounded_rectangle(c(670 - tw / 2 - 10, 309, 670 + tw / 2 + 10, 333), radius=6 * K, fill=BG_BOTTOM)
    d.text(c(670, 321), msg, font=mono(14), fill=MUTED, anchor="mm")

    for t, (tx0, ty0, tx1, ty1) in enumerate(trays):
        d.rounded_rectangle(c(tx0, ty0, tx1, ty1), radius=12 * K, fill=PANEL, outline=GREEN, width=2 * K)
        d.text(c(tx0 + 16, ty0 + 10), f"sched-worker{t + 1} · GPU tray", font=bold(16), fill=TEXT)
        for g in range(4):
            gx = tx0 + 16 + (g % 2) * 122
            gy = ty0 + 42 + (g // 2) * 46
            d.rounded_rectangle(c(gx, gy, gx + 116, gy + 38), radius=7 * K, fill=GPU_FILL, outline=GREEN, width=2 * K)
            d.text(c(gx + 58, gy + 19), "GB200", font=bold(15), fill=GREEN, anchor="mm")
        cx = (tx0 + tx1) / 2
        d.line(c(cx, ty1, cx, ty1 + 16), fill=PANEL_EDGE, width=2 * K)
        node(d, cx - 75, ty1 + 16, cx + 75, ty1 + 56, "BMC · Redfish")
        for hx in (tx0 + 22, tx0 + 46, tx1 - 46, tx1 - 22):   # four HCAs to the InfiniBand leaf
            d.line(c(hx, ty1, hx, 600), fill=(118, 185, 0, 120), width=2 * K)

    # InfiniBand: leaf, uplinks, spine.
    for ux in (560, 630, 710, 780):
        d.line(c(ux, 640, ux, 672), fill=(118, 185, 0, 120), width=2 * K)
    node(d, 390, 600, 950, 640, "InfiniBand leaf")
    node(d, 390, 672, 950, 712, "InfiniBand spine")

    # The switch tray's BMC, to the right.
    d.line(c(sx1, (sy0 + sy1) / 2, 990, (sy0 + sy1) / 2), fill=PANEL_EDGE, width=2 * K)
    node(d, 990, (sy0 + sy1) / 2 - 20, 1140, (sy0 + sy1) / 2 + 20, "BMC · Redfish")
    note(d, 990, 300, 150, ["NVLink switch tray and each GPU tray has a BMC with Redfish API.",
                            "BlueField DPUs are not emulated."])

    path = os.path.normpath(OUT)
    img.save(path, optimize=True)
    print(path)


if __name__ == "__main__":
    main()
