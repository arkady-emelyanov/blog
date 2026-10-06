#!/usr/bin/env python3
"""Generates the LinkedIn preview images (og:image, 1200x627) of the AI lab series.

    .venv/bin/python tools/social-cards.py

One card per page of docs/ai-lab/, written to docs/ai-lab/images/social/.
Headline and description come from each page (H1 and front-matter
`description`), so the card says what LinkedIn's text below it says.
Adapted from ai-lab's docs/assets/social-preview.py: the same look, with the
part's number, headline and topics, and the cluster diagram highlighting the
part's subject so the cards are distinguishable in a feed.
"""
import os
import re
import textwrap

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 627  # LinkedIn link preview, 1.91:1
SERIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "ai-lab")
OUT = os.path.join(SERIES, "images", "social")

BG_TOP, BG_BOTTOM = (8, 12, 20), (17, 24, 39)
GREEN = (118, 185, 0)          # NVIDIA green
TEXT = (236, 240, 245)
MUTED = (148, 163, 184)
DIM = (71, 85, 105)
PANEL = (22, 31, 48)
PANEL_EDGE = (51, 65, 85)
CHIP = (30, 41, 59)
GPU_FILL = (14, 22, 12)

FONTS = "/usr/share/fonts/truetype"
bold = lambda s: ImageFont.truetype(f"{FONTS}/noto/NotoSans-Bold.ttf", s)
regular = lambda s: ImageFont.truetype(f"{FONTS}/noto/NotoSans-Regular.ttf", s)
mono = lambda s: ImageFont.truetype(f"{FONTS}/dejavu/DejaVuSansMono.ttf", s)

# Highlightable parts of the diagram.
GPUS, SWITCH, LINKS, BMCS, IB = "gpus", "switch", "links", "bmcs", "ib"
ALL = {GPUS, SWITCH, LINKS, BMCS}

CARDS = [
    dict(file="index", part=None,
         headline="A GB200 GPU cluster on a single Linux machine, in seven parts",
         chips=["Slurm · k3s", "Redfish BMCs", "NVLink", "Prometheus · Grafana"],
         focus=ALL, label="NVL8 domain, emulated"),
    dict(file="01-intro", part=1,
         chips=["Incus · Ansible", "CUDA · NVML · NCCL", "make up"],
         focus=ALL, label="2 trays · 8 GB200 · 1 switch tray"),
    dict(file="02-slurm", part=2,
         chips=["GRES · cons_tres", "topology/block", "sacct · quotas"],
         focus={GPUS}, label="slurmd · gpu:gb200:4 per tray"),
    dict(file="03-kubernetes", part=3,
         chips=["k3s · CDI", "Kueue TAS", "JobSet"],
         focus={GPUS}, label="nvidia.com/gpu: 4 per node"),
    dict(file="04-bmc-redfish", part=4,
         chips=["Redfish", "power cycles", "GPU sensors"],
         focus={BMCS}, label="out of band: 3 BMCs"),
    dict(file="05-nvlink", part=5,
         chips=["partitions · cliques", "gRPC controller", "fabric metrics"],
         focus={SWITCH, LINKS}, label="144 links · 2 NVSwitch chips"),
    dict(file="06-observability", part=6,
         chips=["GPUs", "scheduler", "NVLink", "BMCs"],
         focus=ALL, label="every layer, one dashboard", chart=True),
    dict(file="07-networking", part=7,
         chips=["InfiniBand topology", "front-end Ethernet", "no RDMA · no DPUs"],
         focus={IB}, label="scale-out: leaf · spine"),
]
PARTS = max(c["part"] or 0 for c in CARDS)


def page(name):
    """(headline, description) of docs/ai-lab/<name>.md: the H1 without its
    "AI lab, part N: " prefix, and the front-matter description."""
    text = open(os.path.join(SERIES, name + ".md")).read()
    title = re.search(r"^# (.+)$", text, re.M).group(1)
    title = re.sub(r"^AI lab, part \d+: ", "", title)
    desc = re.search(r'^description: "(.+)"$', text, re.M)
    return title[0].upper() + title[1:], desc.group(1) if desc else ""


def background():
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(grid)
    for x in range(0, W, 40):
        g.line([(x, 0), (x, H)], fill=(255, 255, 255, 8))
    for y in range(0, H, 40):
        g.line([(0, y), (W, y)], fill=(255, 255, 255, 8))
    img.paste(grid, (0, 0), grid)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([720, 110, 1180, 540], fill=(118, 185, 0, 38))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img.paste(glow, (0, 0), glow)
    return img


def chip(d, x, y, label, font, fill=CHIP, colour=TEXT):
    w = d.textlength(label, font=font) + 28
    d.rounded_rectangle([x, y, x + w, y + 38], radius=19, fill=fill, outline=PANEL_EDGE, width=1)
    d.text((x + 14, y + 19), label, font=font, fill=colour, anchor="lm")
    return x + w + 10


def diagram(img, focus, label, chart):
    """The NVL8 domain: switch tray on top, two GPU trays, their BMCs; the
    parts in `focus` are drawn in green, the rest dimmed."""
    on = lambda part: part in focus
    trays = [(730, 318, 930, 498), (950, 318, 1150, 498)]
    sx0, sy0, sx1, sy1 = 730, 88, 1150, 190
    switch_boxes = [[sx0 + 16, sy0 + 46, sx0 + 202, sy0 + 86], [sx0 + 218, sy0 + 46, sx0 + 404, sy0 + 86]]

    links = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(links)
    alpha = 110 if on(LINKS) else 34
    for tx0, ty0, tx1, _ in trays:
        for g in range(4):
            ax = tx0 + 28 + g * (tx1 - tx0 - 56) / 3
            for bx0, _, bx1, by1 in switch_boxes:
                ld.line([(ax, ty0), ((bx0 + bx1) / 2, by1)], fill=(118, 185, 0, alpha), width=3 if on(LINKS) else 2)
    img.paste(links, (0, 0), links)

    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle([sx0, sy0, sx1, sy1], radius=14, fill=PANEL, outline=GREEN if on(SWITCH) else PANEL_EDGE, width=2)
    d.text((sx0 + 18, sy0 + 13), "NVLink switch tray", font=bold(18), fill=TEXT if on(SWITCH) else MUTED)
    for i, box in enumerate(switch_boxes):
        c = GREEN if on(SWITCH) else DIM
        d.rounded_rectangle(box, radius=8, fill=GPU_FILL if on(SWITCH) else PANEL, outline=c, width=2)
        d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), f"NVSwitch {i} · 72 ports", font=mono(13), fill=c, anchor="mm")

    cw = d.textlength(label, font=mono(14))
    d.rounded_rectangle([940 - cw / 2 - 10, 242, 940 + cw / 2 + 10, 266], radius=6, fill=BG_BOTTOM)
    d.text((940, 254), label, font=mono(14), fill=GREEN, anchor="mm")

    for t, (tx0, ty0, tx1, ty1) in enumerate(trays):
        tray_on = on(GPUS)
        d.rounded_rectangle([tx0, ty0, tx1, ty1], radius=14, fill=PANEL, outline=GREEN if tray_on and not chart else PANEL_EDGE, width=2)
        d.text((tx0 + 16, ty0 + 11), f"sched-worker{t + 1}", font=bold(17), fill=TEXT if tray_on else MUTED)
        for g in range(4):
            gx = tx0 + 16 + (g % 2) * 88
            gy = ty0 + 46 + (g // 2) * 62
            c = GREEN if tray_on else DIM
            d.rounded_rectangle([gx, gy, gx + 80, gy + 50], radius=7, fill=GPU_FILL if tray_on else PANEL, outline=c, width=2)
            if chart:  # a tiny utilisation plot per GPU
                pts = [(gx + 8, gy + 40), (gx + 22, gy + 40), (gx + 30, gy + 14), (gx + 58, gy + 12), (gx + 64, gy + 40), (gx + 72, gy + 40)]
                d.line(pts, fill=GREEN, width=3)
            else:
                d.text((gx + 40, gy + 25), "GB200", font=bold(15), fill=c, anchor="mm")
        cx = (tx0 + tx1) / 2
        if on(IB):  # four HCAs per tray down to the InfiniBand leaf
            for h in range(4):
                hx = tx0 + 40 + h * 40
                d.line([(hx, ty1), (hx, 526)], fill=(118, 185, 0, 140), width=2)
        else:
            c = GREEN if on(BMCS) else PANEL_EDGE
            d.line([(cx, ty1), (cx, ty1 + 12)], fill=c, width=2)
            d.rounded_rectangle([cx - 66, ty1 + 12, cx + 66, ty1 + 42], radius=8, fill=GPU_FILL if on(BMCS) else CHIP, outline=c, width=2 if on(BMCS) else 1)
            d.text((cx, ty1 + 27), "BMC · Redfish", font=bold(14) if on(BMCS) else regular(13), fill=GREEN if on(BMCS) else MUTED, anchor="mm")
    if on(IB):
        d.rounded_rectangle([730, 526, 1150, 562], radius=10, fill=GPU_FILL, outline=GREEN, width=2)
        d.text((940, 544), "InfiniBand leaf · 8 × NDR · uplinks to spine", font=mono(14), fill=GREEN, anchor="mm")


def card(spec):
    img = background()
    diagram(img, spec["focus"], spec["label"], spec.get("chart", False))
    d = ImageDraw.Draw(img)
    x = 56

    tag = "AI LAB · SERIES" if spec["part"] is None else f"AI LAB · PART {spec['part']} OF {PARTS}"
    chip(d, x, 56, tag, bold(16), fill=GPU_FILL, colour=GREEN)

    headline, desc = page(spec["file"])
    headline = spec.get("headline", headline)
    y = 116
    for line in textwrap.wrap(headline, width=26):
        d.text((x, y), line, font=bold(44), fill=TEXT)
        y += 56
    d.rectangle([x, y + 14, x + 110, y + 20], fill=GREEN)
    y += 44
    for line in textwrap.wrap(desc, width=50):
        d.text((x, y), line, font=regular(21), fill=MUTED)
        y += 29

    y = 470
    cx = x
    for label in spec["chips"]:
        cx = chip(d, cx, y, label, regular(18))

    d.text((x, 560), "Hands-on AI infrastructure without the hardware", font=regular(20), fill=MUTED)
    d.text((W - 50, 600), "github.com/arkady-emelyanov/ai-lab", font=regular(16), fill=MUTED, anchor="rs")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.normpath(os.path.join(OUT, spec["file"] + ".png"))
    img.save(path, optimize=True)
    print(path)


def main():
    for spec in CARDS:
        card(spec)


if __name__ == "__main__":
    main()
