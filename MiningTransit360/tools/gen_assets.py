# -*- coding: utf-8 -*-
"""
Genere les ressources graphiques de MINING TRANSIT 360 (assets/) :
  - icons/*.png   : icones blanches (et variantes couleur) 96x96, fond transparent
  - logo.png      : logo camion-benne orange + montagne
  - banner.png    : bandeau superieur (degrade nuit + pistes de bauxite + camion)
Icones : Font Awesome Free (police SIL OFL 1.1 / icones CC BY 4.0) - attribution dans docs.
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "assets")
FA = None
for p in ("/usr/local/lib/python3.13/dist-packages/fontawesomefree/static/fontawesomefree/webfonts/fa-solid-900.ttf",):
    if os.path.exists(p):
        FA = p
if FA is None:
    import fontawesomefree
    FA = os.path.join(os.path.dirname(fontawesomefree.__file__), "static/fontawesomefree/webfonts/fa-solid-900.ttf")

ICONS = {
    "truck": 0xf0d1, "truck-moving": 0xf4df, "chart-line": 0xf201, "coins": 0xf51e, "handshake": 0xf2b5,
    "house": 0xf015, "gears": 0xf085, "user": 0xf007, "users": 0xf0c0, "gas-pump": 0xf52f, "clock": 0xf017,
    "circle-dot": 0xf192, "wrench": 0xf0ad, "file-invoice-dollar": 0xf571, "money-bill-wave": 0xf53a,
    "chart-column": 0xe0e3, "gear": 0xf013, "user-shield": 0xf505, "right-from-bracket": 0xf2f5, "bell": 0xf0f3,
    "circle-exclamation": 0xf06a, "triangle-exclamation": 0xf071, "circle-info": 0xf05a, "circle-check": 0xf058,
    "calendar-days": 0xf073, "chart-pie": 0xf200, "file-lines": 0xf15c, "warehouse": 0xf494,
    "boxes-stacked": 0xf468, "weight-scale": 0xf496, "route": 0xf4d7, "clipboard-list": 0xf46d,
    "folder-open": 0xf07c, "magnifying-glass": 0xf002, "plus": 0x2b, "print": 0xf02f, "file-pdf": 0xf1c1,
    "file-excel": 0xf1c3, "floppy-disk": 0xf0c7, "rotate": 0xf2f1, "xmark": 0xf00d, "pen": 0xf304,
    "trash": 0xf1f8, "check": 0xf00c, "ban": 0xf05e, "eye": 0xf06e, "cash-register": 0xf788,
    "hand-holding-dollar": 0xf4c0, "receipt": 0xf543, "id-card": 0xf2c2, "truck-ramp-box": 0xf4de,
    "screwdriver-wrench": 0xf7d9, "calendar-check": 0xf274, "bullseye": 0xf140, "scale-balanced": 0xf24e,
    "clock-rotate-left": 0xf1da, "shield-halved": 0xf3ed, "database": 0xf1c0, "file-export": 0xf56e,
    "arrow-up": 0xf062, "arrow-down": 0xf063,
}
COLORED = {"circle-exclamation": (229, 57, 53), "triangle-exclamation": (251, 166, 26), "circle-info": (30, 111, 217),
           "circle-check": (46, 160, 67), "arrow-up": (46, 160, 67), "arrow-down": (229, 57, 53)}


def icon(name, code, color=(255, 255, 255), size=96, suffix=""):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    f = ImageFont.truetype(FA, int(size * 0.78))
    dr = ImageDraw.Draw(img)
    ch = chr(code)
    bb = dr.textbbox((0, 0), ch, font=f)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    dr.text(((size - w) / 2 - bb[0], (size - h) / 2 - bb[1]), ch, font=f, fill=color + (255,))
    img.save(os.path.join(OUT, "icons", "%s%s.png" % (name, suffix)))


def logo():
    W, H = 360, 230
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # montagne
    d.polygon([(40, 150), (150, 30), (210, 95), (250, 60), (340, 150)], fill=(247, 148, 29, 255))
    d.polygon([(150, 30), (128, 56), (150, 50), (165, 62), (175, 48)], fill=(255, 255, 255, 255))
    d.polygon([(250, 60), (238, 76), (252, 72), (262, 80)], fill=(255, 255, 255, 255))
    # camion benne (silhouette)
    f = ImageFont.truetype(FA, 150)
    ch = chr(0xf4df)
    d.text((70, 60), ch, font=f, fill=(255, 180, 0, 255))
    img = img.filter(ImageFilter.SMOOTH)
    img.save(os.path.join(OUT, "logo.png"))


def banner():
    W, H = 1500, 200
    img = Image.new("RGB", (W, H))
    px = img.load()
    for x in range(W):
        for y in range(H):
            t = x / W
            # degrade navy -> ciel crepuscule a droite
            r = int(11 + 60 * t ** 2 + 40 * (y / H) * t)
            g = int(42 + 50 * t ** 2)
            b = int(85 + 70 * t)
            px[x, y] = (min(255, r), min(255, g), min(255, b))
    d = ImageDraw.Draw(img)
    # collines de bauxite (rouge/ocre)
    d.polygon([(500, 200), (650, 110), (780, 140), (900, 80), (1050, 120), (1200, 70), (1350, 120), (1500, 90), (1500, 200)],
              fill=(150, 72, 40))
    d.polygon([(560, 200), (760, 150), (950, 170), (1150, 130), (1500, 160), (1500, 200)], fill=(181, 92, 50))
    d.polygon([(700, 200), (980, 175), (1500, 185), (1500, 200)], fill=(205, 120, 70))
    # piste
    d.polygon([(820, 200), (1010, 170), (1060, 170), (980, 200)], fill=(220, 150, 95))
    # camion
    f = ImageFont.truetype(FA, 120)
    d.text((1180, 60), chr(0xf4df), font=f, fill=(255, 176, 0))
    img = img.filter(ImageFilter.SMOOTH_MORE)
    img.save(os.path.join(OUT, "banner.png"))


if __name__ == "__main__":
    os.makedirs(os.path.join(OUT, "icons"), exist_ok=True)
    for n, c in ICONS.items():
        icon(n, c)
        if n in COLORED:
            icon(n, c, COLORED[n], suffix="_c")
    icon("truck", ICONS["truck"], (247, 148, 29), suffix="_o")
    logo()
    banner()
    print("icons:", len(os.listdir(os.path.join(OUT, "icons"))))
