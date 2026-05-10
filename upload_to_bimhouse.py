"""Convert the Farnsworth model into bim.house element format and POST it."""

from __future__ import annotations

import json
import math
import urllib.request

API = "https://bim.house/api/bim/save"
SLUG = "bim"

# All dimensions in millimetres (bim.house expects integers, mm)
MAIN_LEN = 23470
MAIN_WID = 8740
MAIN_TOP = 1600
MAIN_THK = 300

TERRACE_LEN = 16760
TERRACE_WID = 6710
TERRACE_TOP = 800
TERRACE_THK = 250

ROOF_BOT = 4500
ROOF_THK = 250

COL_W = 210
COL_D = 210
COL_BASE = 0
COL_TOP = ROOF_BOT + ROOF_THK  # 4750
COL_BAY = 6710
COL_CANTI = 1680

GLASS_THK = 25
GLASS_H = 2900

SITE_LEN = 60000
SITE_WID = 30000

elements: list[dict] = []


def add(cls, label, x, y, z, w, d, h, *, descr="", shape="box", rotation=0):
    elements.append({
        "id": f"e{len(elements) + 1:03d}",
        "cls": cls,
        "label": label,
        "descr": descr,
        "shape": shape,
        "x": int(round(x)),
        "y": int(round(y)),
        "z": int(round(z)),
        "w": int(round(w)),
        "d": int(round(d)),
        "h": int(round(h)),
        "rotation": int(round(rotation)),
    })


# ---- Site ground ----------------------------------------------------------
ground_x = -SITE_LEN // 2 - 5000
ground_y = -SITE_WID // 2 + MAIN_WID // 2
add(
    "IFCSLAB", "Ground (Fox River meadow)",
    ground_x, ground_y, -200,
    SITE_LEN, SITE_WID, 200,
    descr="Topsoil meadow plate (site element)",
)

# ---- Main floor slab ------------------------------------------------------
add(
    "IFCSLAB", "Main Floor Slab (travertine)",
    0, 0, MAIN_TOP - MAIN_THK,
    MAIN_LEN, MAIN_WID, MAIN_THK,
    descr="77'×28'8\" precast concrete + travertine pavers",
)

# ---- Roof slab ------------------------------------------------------------
add(
    "IFCROOF", "Roof Slab",
    0, 0, ROOF_BOT,
    MAIN_LEN, MAIN_WID, ROOF_THK,
    descr="Roof plate, 250mm",
)

# ---- Terrace slab ---------------------------------------------------------
terrace_x0 = -TERRACE_LEN - 300
terrace_y0 = (MAIN_WID - TERRACE_WID) // 2
add(
    "IFCSLAB", "Terrace Slab",
    terrace_x0, terrace_y0, TERRACE_TOP - TERRACE_THK,
    TERRACE_LEN, TERRACE_WID, TERRACE_THK,
    descr="55'×22' lower terrace, travertine",
)

# ---- 8 steel columns (W8x48 simplified as 210mm box) ---------------------
col_height = COL_TOP - COL_BASE
long_xs = [COL_CANTI + i * COL_BAY for i in range(4)]
# columns sit OUTBOARD of slab on long sides
north_y = MAIN_WID - 20  # column inner face flush with slab edge approx.
south_y = -COL_D + 20

for i, cx in enumerate(long_xs):
    # corner = center - half-extent
    add(
        "IFCCOLUMN", f"Steel Column N{i + 1}",
        cx - COL_W // 2, north_y, COL_BASE,
        COL_W, COL_D, col_height,
        descr="W8x48, white-painted steel",
    )
    add(
        "IFCCOLUMN", f"Steel Column S{i + 1}",
        cx - COL_W // 2, south_y, COL_BASE,
        COL_W, COL_D, col_height,
        descr="W8x48, white-painted steel",
    )

# ---- Glass enclosure (use IFCWINDOW for transparent rendering) -----------
glass_x0 = long_xs[0] + COL_W // 2
glass_x1 = long_xs[-1] - COL_W // 2
glass_long = glass_x1 - glass_x0  # length of S/N walls

# South (long, at y=0)
add(
    "IFCWINDOW", "Glass Wall — South",
    glass_x0, 0, MAIN_TOP,
    glass_long, GLASS_THK, GLASS_H,
    descr="Floor-to-ceiling plate glass curtain",
)
# North (long, at y=MAIN_WID-25)
add(
    "IFCWINDOW", "Glass Wall — North",
    glass_x0, MAIN_WID - GLASS_THK, MAIN_TOP,
    glass_long, GLASS_THK, GLASS_H,
    descr="Floor-to-ceiling plate glass curtain",
)
# West (short, at x=glass_x0, runs along y)
add(
    "IFCWINDOW", "Glass Wall — West",
    glass_x0, 0, MAIN_TOP,
    GLASS_THK, MAIN_WID, GLASS_H,
    descr="Floor-to-ceiling plate glass — entry side",
)
# East (short)
add(
    "IFCWINDOW", "Glass Wall — East",
    glass_x1 - GLASS_THK, 0, MAIN_TOP,
    GLASS_THK, MAIN_WID, GLASS_H,
    descr="Floor-to-ceiling plate glass",
)

# ---- Stairs --------------------------------------------------------------
# grade -> terrace
add(
    "IFCSTAIR", "Stair — Grade to Terrace",
    terrace_x0 - 1800, terrace_y0 + (TERRACE_WID - 1500) // 2, 0,
    1800, 1500, TERRACE_TOP,
    descr="Free-floating travertine treads",
)
# terrace -> main
add(
    "IFCSTAIR", "Stair — Terrace to Main",
    -1800, (MAIN_WID - 1500) // 2, TERRACE_TOP,
    1800, 1500, MAIN_TOP - TERRACE_TOP,
    descr="Free-floating travertine treads",
)

# ---- Single interior space (the pavilion) --------------------------------
add(
    "IFCSPACE", "Pavilion Space",
    glass_x0 + GLASS_THK, GLASS_THK, MAIN_TOP,
    glass_long - 2 * GLASS_THK, MAIN_WID - 2 * GLASS_THK, GLASS_H,
    descr="Single open living space — Mies's universal space",
)


payload = {
    "slug": SLUG,
    "elements": elements,
    "note": "Farnsworth House (Mies van der Rohe, 1951) — generated from IFC4 model",
}

req = urllib.request.Request(
    API,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
print(f"POST {API}")
print(f"  elements: {len(elements)}")
print(f"  bbox of payload (mm): "
      f"x [{min(e['x'] for e in elements)}..{max(e['x']+e['w'] for e in elements)}], "
      f"y [{min(e['y'] for e in elements)}..{max(e['y']+e['d'] for e in elements)}], "
      f"z [{min(e['z'] for e in elements)}..{max(e['z']+e['h'] for e in elements)}]")
with urllib.request.urlopen(req, timeout=30) as resp:
    body = resp.read().decode()
    print(f"  HTTP {resp.status}")
    data = json.loads(body)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()
    print(f"View URL: https://bim.house{data['edit_url']}")
    print(f"IFC URL:  https://bim.house{data['ifc_url']}")
    print(f"JSON URL: https://bim.house{data['json_url']}")
