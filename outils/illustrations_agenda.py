"""Illustrations colorées pour les événements de l'agenda (Modern Events Calendar ou autre).

Cartes PNG 1200×675 : fond de couleur, grand disque clair, pictogramme blanc au trait (Tabler Icons),
confettis et étoiles. Aucun texte dans l'image : l'agenda la recadre (carré, 3:2…) et affiche déjà
le titre et la date ; le pictogramme reste au centre.

Usage :
    npm install @tabler/icons@3          # pictogrammes (licence MIT), dans le dossier courant
    pip install playwright && playwright install chromium   # sauf si Chromium est déjà disponible
    python outils/illustrations_agenda.py evenements.json sortie/

evenements.json : [{"fichier": "noel", "icones": ["christmas-tree"], "couleur": "sapin"}, …]
  - fichier : nom du PNG produit (sans extension) ;
  - icones : 1 à 3 noms de pictogrammes Tabler « outline » (https://tabler.io/icons) ;
  - couleur : une clé de PALETTE (par défaut : cycle sur la palette).
Les images s'envoient ensuite avec wp_media_envoyer_preparer, puis se posent comme image à la une
par l'import XML de MEC (<featured_image><full>URL</full></featured_image>) — voir le README.
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
from pathlib import Path

# fond, disque, confettis (3 couleurs)
PALETTE = {
    "azur": ("#1e88c7", "#49a5db", ["#ffd166", "#ffffff", "#a8e0ff"]),
    "corail": ("#ef6351", "#f4857a", ["#ffd166", "#ffffff", "#ffe8d6"]),
    "orange": ("#f28c28", "#f6a656", ["#ffffff", "#ffe066", "#ffd9b3"]),
    "jaune": ("#f2b705", "#f7c93a", ["#ffffff", "#ef6351", "#1e88c7"]),
    "vert": ("#3aa06b", "#5fb888", ["#ffd166", "#ffffff", "#c7f0d8"]),
    "sapin": ("#2e7d5b", "#4b9a78", ["#ef6351", "#ffd166", "#ffffff"]),
    "turquoise": ("#1fa3a3", "#4cbaba", ["#ffd166", "#ffffff", "#c9f2f2"]),
    "violet": ("#7b5ea7", "#9a80c2", ["#ffd166", "#ffffff", "#e2d6f5"]),
    "rose": ("#e0609a", "#ea86b4", ["#ffd166", "#ffffff", "#ffd6e8"]),
    "rouge": ("#d94646", "#e36c6c", ["#ffd166", "#ffffff", "#3aa06b"]),
    "ciel": ("#3f9fe0", "#6cb8ea", ["#ffffff", "#ffd166", "#d7efff"]),
}
W, H = 1200, 675
ICONS = Path("node_modules/@tabler/icons/icons/outline")


def icon_svg(name: str, size: int, stroke: float = 1.8) -> str:
    path = ICONS / f"{name}.svg"
    if not path.is_file():
        raise SystemExit(f"pictogramme inconnu : {name} (attendu : {path})")
    s = path.read_text()
    s = re.sub(r'width="24"', f'width="{size}"', s, count=1)
    s = re.sub(r'height="24"', f'height="{size}"', s, count=1)
    return s.replace('stroke-width="2"', f'stroke-width="{stroke}"')


def star(x: float, y: float, r: float, color: str, rot: float) -> str:
    pts = []
    for i in range(10):
        a = math.radians(rot + i * 36)
        rr = r if i % 2 == 0 else r * 0.45
        pts.append(f"{x + rr * math.cos(a):.1f},{y + rr * math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="{color}"/>'


def confettis(seed: int, colors: list[str]) -> str:
    rnd = random.Random(seed)
    out = []
    for _ in range(26):
        while True:  # hors du disque central
            x, y = rnd.uniform(40, W - 40), rnd.uniform(40, H - 40)
            if (x - W / 2) ** 2 + (y - H / 2) ** 2 > 265 ** 2:
                break
        c, k, op = rnd.choice(colors), rnd.random(), rnd.uniform(0.55, 0.9)
        if k < 0.35:
            out.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{rnd.uniform(7, 16):.0f}" fill="{c}" opacity="{op:.2f}"/>')
        elif k < 0.7:
            out.append(f'<g opacity="{op:.2f}">{star(x, y, rnd.uniform(14, 26), c, rnd.uniform(0, 72))}</g>')
        else:
            w, h = rnd.uniform(12, 24), rnd.uniform(28, 46)
            out.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" rx="4" fill="{c}" '
                       f'opacity="{op:.2f}" transform="rotate({rnd.uniform(-40, 40):.0f} {x:.0f} {y:.0f})"/>')
    return "".join(out)


def page(ev: dict, index: int) -> str:
    couleur = ev.get("couleur") or list(PALETTE)[index % len(PALETTE)]
    fond, disque, conf = PALETTE[couleur]
    icons = ev["icones"][:3]
    size = 340 if len(icons) == 1 else (250 if len(icons) == 2 else 200)
    svgs = "".join(f'<div class="ic">{icon_svg(n, size)}</div>' for n in icons)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0}}body{{width:{W}px;height:{H}px;background:{fond};position:relative;overflow:hidden}}
svg.deco{{position:absolute;inset:0}}
.disque{{position:absolute;left:{W // 2}px;top:{H // 2}px;width:460px;height:460px;margin:-230px 0 0 -230px;border-radius:50%;background:{disque}}}
.wrap{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center}}
.icons{{display:flex;gap:28px;align-items:flex-end}} .ic svg{{display:block;stroke:#fff;filter:drop-shadow(0 3px 0 rgba(0,0,0,.12))}}
</style></head><body>
<svg class="deco" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{confettis(index + 1, conf)}</svg>
<div class="disque"></div><div class="wrap"><div class="icons">{svgs}</div></div>
</body></html>"""


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit(__doc__)
    from playwright.sync_api import sync_playwright
    evs = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    out = Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": W, "height": H})
        for i, ev in enumerate(evs):
            pg.set_content(page(ev, i))
            pg.wait_for_timeout(100)
            pg.screenshot(path=str(out / f"{ev['fichier']}.png"), type="png")
            print(out / f"{ev['fichier']}.png")
        browser.close()


if __name__ == "__main__":
    main(sys.argv)
