"""Flatten the full v1.2.0 model into bim.house's box-primitive format.

bim.house's cloud save only stores `{cls, x,y,z, w,d,h, rotation}` per
element, with cls limited to a small set that does not include
IfcCurtainWall / IfcMember / IfcPlate / IfcLightFixture /
IfcSanitaryTerminal / IfcGeographicElement / IfcCovering.

So we map:
  - IfcMember / IfcCurtainWall jambs/heads → IFCBEAM or IFCCOLUMN
  - IfcPlate (glass) → IFCWINDOW (their viewer renders it translucent)
  - IfcLightFixture → IFCBUILDINGELEMENTPROXY
  - IfcSanitaryTerminal → IFCFURNISHINGELEMENT
  - IfcGeographicElement → IFCSLAB (for water/path/driveway) or
                           IFCCOLUMN (for tree trunks)
  - IfcCovering (curtain) → IFCWINDOW (translucent)
  - IfcCovering (flooring/ceiling) → IFCSLAB

Everything is in millimetres, integer corner-anchored.
"""

from __future__ import annotations

import json
import urllib.request

API = "https://bim.house/api/bim/save"
SLUG = "bim"

# ─── dimensions in mm ───────────────────────────────────────────────────────
MAIN_LEN = 23470
MAIN_WID = 8740
MAIN_TOP = 1600
MAIN_THK = 300

ROOF_BOT = 4500
ROOF_THK = 250

TERRACE_LEN = 16760
TERRACE_WID = 6710
TERRACE_TOP = 800
TERRACE_THK = 250
TERRACE_X0 = -TERRACE_LEN - 300
TERRACE_Y0 = (MAIN_WID - TERRACE_WID) // 2

COL_W = 210
COL_BAY = 6706
COL_CANTI = 1676
N_BAYS = 3

GLASS_THK = 25
GLASS_H = 2900
FRAME = 50

DOOR_W = 900
DOOR_H = 2700
DOOR_T = 40

STAIR_WIDTH = 1500
STAIR_RISER = 160
STAIR_TREAD = 280
STAIR_RISERS = 5

elements = []


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


# ─── slabs / roof / terrace / ground ────────────────────────────────────────
add("IFCSLAB", "Site Ground (meadow)",
    -35000, -10630, -200, 60000, 30000, 200,
    descr="60×30 m meadow plate")

add("IFCSLAB", "Main Floor Slab (FLOOR-300)",
    0, 0, MAIN_TOP - MAIN_THK, MAIN_LEN, MAIN_WID, MAIN_THK,
    descr="travertine 50 + RC 200 + insulation 50")
add("IFCSLAB", "Travertine Floor Finish (main)",
    0, 0, MAIN_TOP, MAIN_LEN, MAIN_WID, 5,
    descr="visible 5mm travertine bump")

add("IFCROOF", "Roof Slab (ROOF-250)",
    0, 0, ROOF_BOT, MAIN_LEN, MAIN_WID, ROOF_THK,
    descr="membrane 10 + RC 200 + insulation 40")
add("IFCSLAB", "Plaster Ceiling",
    0, 0, ROOF_BOT - 15, MAIN_LEN, MAIN_WID, 15,
    descr="white plaster 15mm")

add("IFCSLAB", "Terrace Slab",
    TERRACE_X0, TERRACE_Y0, TERRACE_TOP - TERRACE_THK,
    TERRACE_LEN, TERRACE_WID, TERRACE_THK,
    descr="travertine 50 + RC 200")
add("IFCSLAB", "Travertine Floor Finish (terrace)",
    TERRACE_X0, TERRACE_Y0, TERRACE_TOP, TERRACE_LEN, TERRACE_WID, 5,
    descr="terrace travertine bump")

# ─── 8 W8x48 columns ────────────────────────────────────────────────────────
col_height = ROOF_BOT + ROOF_THK
long_xs = [COL_CANTI + i * COL_BAY for i in range(N_BAYS + 1)]
for i, cx in enumerate(long_xs):
    add("IFCCOLUMN", f"W8x48 Column N{i + 1}",
        cx - COL_W // 2, MAIN_WID - 20, 0, COL_W, COL_W, col_height,
        descr="white-painted structural steel")
    add("IFCCOLUMN", f"W8x48 Column S{i + 1}",
        cx - COL_W // 2, -COL_W + 20, 0, COL_W, COL_W, col_height,
        descr="white-painted structural steel")

# ─── curtain walls flattened ────────────────────────────────────────────────
glass_x0 = long_xs[0] + COL_W // 2
glass_x1 = long_xs[-1] - COL_W // 2
glass_long = glass_x1 - glass_x0

for label, ywall in (("South", 0), ("North", MAIN_WID - GLASS_THK)):
    add("IFCBEAM", f"CW {label} — Sill",
        glass_x0, ywall, MAIN_TOP, glass_long, GLASS_THK, FRAME,
        descr="SS angle 50×50 sill")
    add("IFCBEAM", f"CW {label} — Head",
        glass_x0, ywall, MAIN_TOP + GLASS_H - FRAME,
        glass_long, GLASS_THK, FRAME,
        descr="SS angle 50×50 head")
    for j, cx in enumerate(long_xs):
        add("IFCCOLUMN", f"CW {label} — Jamb {j + 1}",
            cx - FRAME // 2, ywall, MAIN_TOP + FRAME,
            FRAME, GLASS_THK, GLASS_H - 2 * FRAME)
    for k in range(N_BAYS):
        x_panel_start = long_xs[k] + FRAME // 2
        x_panel_end = long_xs[k + 1] - FRAME // 2
        add("IFCWINDOW", f"CW {label} — Glass Panel {k + 1}",
            x_panel_start, ywall, MAIN_TOP + FRAME,
            x_panel_end - x_panel_start, GLASS_THK, GLASS_H - 2 * FRAME,
            descr="float plate glass 25mm")

for label, xwall in (("West", glass_x0), ("East", glass_x1 - GLASS_THK)):
    add("IFCBEAM", f"CW {label} — Sill",
        xwall, 0, MAIN_TOP, GLASS_THK, MAIN_WID, FRAME)
    add("IFCBEAM", f"CW {label} — Head",
        xwall, 0, MAIN_TOP + GLASS_H - FRAME, GLASS_THK, MAIN_WID, FRAME)
    add("IFCCOLUMN", f"CW {label} — Jamb S",
        xwall, 0, MAIN_TOP + FRAME, GLASS_THK, FRAME, GLASS_H - 2 * FRAME)
    add("IFCCOLUMN", f"CW {label} — Jamb N",
        xwall, MAIN_WID - FRAME, MAIN_TOP + FRAME,
        GLASS_THK, FRAME, GLASS_H - 2 * FRAME)
    add("IFCWINDOW", f"CW {label} — Glass Panel",
        xwall, FRAME, MAIN_TOP + FRAME,
        GLASS_THK, MAIN_WID - 2 * FRAME, GLASS_H - 2 * FRAME)

# Window stops at column inner faces
for i, cx in enumerate(long_xs):
    add("IFCCOLUMN", f"Window Stop S{i + 1}",
        cx - 40, 0, MAIN_TOP + 50, 80, 40, 2800)
    add("IFCCOLUMN", f"Window Stop N{i + 1}",
        cx - 40, MAIN_WID - 40, MAIN_TOP + 50, 80, 40, 2800)

# ─── door ───────────────────────────────────────────────────────────────────
mid_x = long_xs[1] + FRAME // 2 + ((long_xs[2] - long_xs[1] - FRAME) - DOOR_W) // 2
add("IFCDOOR", "South Pivot Door",
    mid_x, -DOOR_T // 2, MAIN_TOP, DOOR_W, DOOR_T, DOOR_H,
    descr="stainless frame + glass, pivot")

# ─── stairs ─────────────────────────────────────────────────────────────────
add("IFCSTAIR", "Stair — Grade to Terrace",
    TERRACE_X0 - STAIR_RISERS * STAIR_TREAD,
    TERRACE_Y0 + (TERRACE_WID - STAIR_WIDTH) // 2,
    0, STAIR_RISERS * STAIR_TREAD, STAIR_WIDTH, TERRACE_TOP,
    descr="5R × 4T travertine")
add("IFCSTAIR", "Stair — Terrace to Main",
    -STAIR_RISERS * STAIR_TREAD, (MAIN_WID - STAIR_WIDTH) // 2,
    TERRACE_TOP, STAIR_RISERS * STAIR_TREAD, STAIR_WIDTH,
    MAIN_TOP - TERRACE_TOP,
    descr="5R × 4T travertine")

# ─── railings ──────────────────────────────────────────────────────────────
add("IFCRAILING", "Stair 1 Handrail",
    TERRACE_X0 - STAIR_RISERS * STAIR_TREAD,
    TERRACE_Y0 + (TERRACE_WID - STAIR_WIDTH) // 2 + STAIR_WIDTH - 40,
    TERRACE_TOP + 850, STAIR_RISERS * STAIR_TREAD, 40, 40)
add("IFCRAILING", "Stair 2 Handrail",
    -STAIR_RISERS * STAIR_TREAD,
    (MAIN_WID - STAIR_WIDTH) // 2 + STAIR_WIDTH - 40,
    MAIN_TOP + 850, STAIR_RISERS * STAIR_TREAD, 40, 40)

# ─── interior: primavera core, fireplace, kitchen, furniture ───────────────
# (within the Main Floor storey, so MAIN_TOP added for world Z)
def F(name, x, y, z, w, d, h, descr=""):
    add("IFCFURNISHINGELEMENT", name,
        x, y, MAIN_TOP + z, w, d, h, descr=descr)


F("Primavera Wood Core", 4500, 3200, 0, 5500, 2300, 2400,
  descr="iconic freestanding box with kitchen, bath, mech")
F("Fireplace Hearth (Roman travertine)", 6000, 400, 0, 3000, 800, 450)
F("Fireplace Chimney Block", 7000, 1000, 450, 1000, 400, 1950)
F("Kitchen Counter (stainless + primavera)", 4500, 5550, 0, 5500, 650, 850)
F("Refrigerator", 9300, 5550, 0, 650, 650, 1800)
F("Cooktop / Range", 7000, 5550, 850, 800, 650, 100)
F("Range Hood", 7000, 5650, 1500, 800, 450, 300)
F("Bed (Mies design, daybed)", 2100, 4200, 100, 2000, 1600, 450)
F("Wardrobe (primavera screen)", 2000, 1400, 0, 2200, 550, 1800)
F("Nightstand", 4200, 6000, 0, 550, 400, 550)
F("Mies Dining Table (Italian marble)", 11000, 3400, 0, 1800, 950, 740)
for i, (cx, cy) in enumerate([(10850, 2550), (10850, 4550), (12500, 2550), (12500, 4550)]):
    F(f"Mies Brno Dining Chair {i+1}", cx, cy, 0, 550, 550, 850)
for i, (cx, cy) in enumerate([(15500, 1400), (15500, 5200), (18500, 1400), (18500, 5200)]):
    F(f"Barcelona Chair {i+1}", cx, cy, 0, 780, 780, 760)
F("Mies Coffee Table (glass)", 16400, 3200, 0, 1300, 1100, 420)
F("Sideboard (Mies, primavera)", 19500, 6800, 0, 2000, 500, 850)
F("Sideboard (dining)", 12200, 6800, 0, 1800, 500, 850)
F("Bookshelf (primavera, full height)", 20500, 1500, 0, 1200, 400, 1800)

# Sanitary (mapped to FURNISHING)
F("Wall-hung WC", 5000, 3400, 0, 650, 400, 420)
F("Wall-hung Sink", 5000, 4100, 850, 550, 400, 200)
F("Shower Stall", 8500, 3400, 0, 1000, 1000, 2100)
F("Kitchen Sink (stainless)", 5500, 5650, 850, 850, 500, 200)

# Lighting (mapped to BUILDINGELEMENTPROXY since IFCLIGHTFIXTURE not supported)
def Lt(name, x, y, z, w, d, h, descr=""):
    add("IFCBUILDINGELEMENTPROXY", name,
        x, y, MAIN_TOP + z, w, d, h, descr=descr)

Lt("Floor Lamp (Mies design)", 15200, 3300, 0, 200, 200, 1650, descr="brass uplighter")
Lt("Table Lamp (sideboard E)", 20500, 7100, 850, 300, 300, 550)
Lt("Table Lamp (dining sideboard)", 13100, 7100, 850, 300, 300, 550)
Lt("Ceiling Recessed Downlight 1", 6850, 4300, 2850, 300, 300, 50)
Lt("Ceiling Recessed Downlight 2", 15850, 4300, 2850, 300, 300, 50)

# Curtains (IFCCOVERING is supported by bim.house)
add("IFCCOVERING", "Shantung Curtain — South West",
    glass_x0 + 400, 100, MAIN_TOP, 600, 50, 2850,
    descr="hand-woven Shantung silk, cream")
add("IFCCOVERING", "Shantung Curtain — North West",
    glass_x0 + 400, MAIN_WID - 150, MAIN_TOP, 600, 50, 2850)
add("IFCCOVERING", "Curtain Track — South",
    glass_x0, 40, MAIN_TOP + 2850, glass_long, 40, 50,
    descr="brass curtain track")
add("IFCCOVERING", "Curtain Track — North",
    glass_x0, MAIN_WID - 80, MAIN_TOP + 2850, glass_long, 40, 50)

# ─── pavilion space ────────────────────────────────────────────────────────
add("IFCSPACE", "Pavilion (universal space)",
    glass_x0 + GLASS_THK, GLASS_THK, MAIN_TOP,
    glass_long - 2 * GLASS_THK, MAIN_WID - 2 * GLASS_THK, GLASS_H,
    descr="single open living volume — Mies's universal space")

# ─── site landscape ────────────────────────────────────────────────────────
add("IFCSLAB", "Fox River",
    -50000, 24000, -500, 120000, 35000, 400,
    descr="water body — north of pavilion")
add("IFCSLAB", "Gravel Driveway",
    -2000, -25000, 0, 4000, 18000, 50,
    descr="crushed limestone, 4 m wide")
add("IFCSLAB", "Bluestone Path (drive → terrace)",
    -22000, -7000, 0, 3000, 12000, 50)

# Trees (trunk = IFCCOLUMN, canopy = IFCBUILDINGELEMENTPROXY)
TREES = [
    (-30000, -8000, "1"),
    (-25000, 18000, "2"),
    (15000, -8000, "3"),
    (28000, 14000, "4"),
    (-12000, 22000, "5"),
    (10000, 22000, "6"),
]
for tx, ty, tag in TREES:
    add("IFCCOLUMN", f"Tree Trunk {tag}",
        tx - 200, ty - 200, 0, 400, 400, 4500,
        descr="black walnut trunk")
    add("IFCBUILDINGELEMENTPROXY", f"Tree Canopy {tag}",
        tx - 2000, ty - 2000, 4000, 4000, 4000, 4000,
        descr="black walnut canopy")

# Black maple (specimen)
add("IFCCOLUMN", "Black Maple Trunk",
    -2000, 16000, 0, 800, 800, 12000,
    descr="Acer nigrum specimen")
add("IFCBUILDINGELEMENTPROXY", "Black Maple Canopy",
    -7000, 11000, 8000, 11000, 11000, 7000,
    descr="11 m diameter canopy")


# ─── post ───────────────────────────────────────────────────────────────────
payload = {
    "slug": SLUG,
    "elements": elements,
    "note": "Farnsworth House v1.2.0 — full LOD-500 model flattened for "
            "bim.house (box primitives only)",
}

print(f"POST {API}")
print(f"  elements: {len(elements)}")
classes = {}
for e in elements:
    classes[e["cls"]] = classes.get(e["cls"], 0) + 1
for c in sorted(classes):
    print(f"    {c:30s} {classes[c]}")
print()

req = urllib.request.Request(
    API,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as resp:
    body = resp.read().decode()
    data = json.loads(body)
    print(f"  HTTP {resp.status}")
    print()
    print("=== bim.house URLs ===")
    print(f"  Viewer:  https://bim.house{data['edit_url']}")
    print(f"  IFC:     https://bim.house{data['ifc_url']}")
    print(f"  JSON:    https://bim.house{data['json_url']}")
    print(f"  Token:   {data['token']}")
