"""Upload v2 Farnsworth model to bim.house.

bim.house doesn't natively know IfcMember/IfcPlate/IfcCurtainWall, so we
flatten the v2 model into the supported set:
  - IFCSLAB     for floors / roof / terrace / ground
  - IFCCOLUMN   for steel W8x48 columns AND for glass-wall mullions
                (bim.house's column color is light, which approximates
                stainless steel framing)
  - IFCWINDOW   for glass panels (renders translucent in their viewer)
  - IFCDOOR     for the south pivot door
  - IFCSTAIR    for stairs
  - IFCRAILING  for handrails
  - IFCSPACE    for the pavilion space
"""

from __future__ import annotations

import json
import urllib.request

API = "https://bim.house/api/bim/save"
SLUG = "bim"

# All in millimetres (bim.house wants integers)
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
COL_D = 210
COL_BAY = 6706
COL_CANTI = 1676
N_BAYS = 3

GLASS_THK = 25
GLASS_H = 2900
FRAME = 50  # mullion / head / sill width

DOOR_WIDTH = 900
DOOR_HEIGHT = 2700
DOOR_THK = 40

STAIR_WIDTH = 1500
STAIR_RISER = 160
STAIR_TREAD = 280
STAIR_RISERS = 5

SITE_LEN = 60000
SITE_WID = 30000

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


# Site ground
add("IFCSLAB", "Ground (Fox River meadow)",
    -SITE_LEN // 2 - 5000, -SITE_WID // 2 + MAIN_WID // 2, -200,
    SITE_LEN, SITE_WID, 200, descr="Topsoil meadow")

# Slabs
add("IFCSLAB", "Main Floor Slab (travertine + concrete + insulation)",
    0, 0, MAIN_TOP - MAIN_THK, MAIN_LEN, MAIN_WID, MAIN_THK,
    descr="FLOOR-300 layered (travertine 50 + RC 200 + insulation 50)")
add("IFCROOF", "Roof Slab",
    0, 0, ROOF_BOT, MAIN_LEN, MAIN_WID, ROOF_THK,
    descr="ROOF-250 layered (membrane 10 + RC 200 + insulation 40)")
add("IFCSLAB", "Terrace Slab",
    TERRACE_X0, TERRACE_Y0, TERRACE_TOP - TERRACE_THK,
    TERRACE_LEN, TERRACE_WID, TERRACE_THK,
    descr="TERRACE-250 (travertine 50 + RC 200)")

# Columns (8) — W8x48 steel, white enamel
col_height = ROOF_BOT + ROOF_THK
long_xs = [COL_CANTI + i * COL_BAY for i in range(N_BAYS + 1)]
for i, cx in enumerate(long_xs):
    add("IFCCOLUMN", f"W8x48 Column N{i + 1}",
        cx - COL_W // 2, MAIN_WID - 20, 0, COL_W, COL_D, col_height,
        descr="Structural steel W8x48, painted white enamel")
    add("IFCCOLUMN", f"W8x48 Column S{i + 1}",
        cx - COL_W // 2, -COL_D + 20, 0, COL_W, COL_D, col_height,
        descr="Structural steel W8x48, painted white enamel")

# Curtain wall framing as IFCCOLUMN/IFCBEAM substitutes
# (bim.house has no IFCMEMBER; columns render light-colored)
glass_x0 = long_xs[0] + COL_W // 2
glass_x1 = long_xs[-1] - COL_W // 2
glass_long = glass_x1 - glass_x0

for label, ywall in (("South", 0), ("North", MAIN_WID - GLASS_THK)):
    # head and sill (full length 50mm tall)
    add("IFCBEAM", f"CW {label} — Sill",
        glass_x0, ywall, MAIN_TOP, glass_long, GLASS_THK, FRAME,
        descr="Stainless steel sill angle 50×50")
    add("IFCBEAM", f"CW {label} — Head",
        glass_x0, ywall, MAIN_TOP + GLASS_H - FRAME,
        glass_long, GLASS_THK, FRAME,
        descr="Stainless steel head angle 50×50")
    # 4 jamb mullions at column lines
    for j, cx in enumerate(long_xs):
        add("IFCCOLUMN", f"CW {label} — Jamb {j + 1}",
            cx - FRAME // 2, ywall, MAIN_TOP + FRAME,
            FRAME, GLASS_THK, GLASS_H - 2 * FRAME,
            descr="Stainless steel jamb 50×50")
    # 3 glass panels between jambs
    for k in range(N_BAYS):
        x_panel_start = long_xs[k] + FRAME // 2
        x_panel_end = long_xs[k + 1] - FRAME // 2
        add("IFCWINDOW", f"CW {label} — Glass Panel {k + 1}",
            x_panel_start, ywall, MAIN_TOP + FRAME,
            x_panel_end - x_panel_start, GLASS_THK, GLASS_H - 2 * FRAME,
            descr="Float plate glass 25mm")

# West and East curtain walls (1 panel + 2 jambs each)
for label, xwall in (("West", glass_x0), ("East", glass_x1 - GLASS_THK)):
    add("IFCBEAM", f"CW {label} — Sill",
        xwall, 0, MAIN_TOP, GLASS_THK, MAIN_WID, FRAME,
        descr="Stainless steel sill")
    add("IFCBEAM", f"CW {label} — Head",
        xwall, 0, MAIN_TOP + GLASS_H - FRAME, GLASS_THK, MAIN_WID, FRAME,
        descr="Stainless steel head")
    add("IFCCOLUMN", f"CW {label} — Jamb S",
        xwall, 0, MAIN_TOP + FRAME, GLASS_THK, FRAME, GLASS_H - 2 * FRAME)
    add("IFCCOLUMN", f"CW {label} — Jamb N",
        xwall, MAIN_WID - FRAME, MAIN_TOP + FRAME,
        GLASS_THK, FRAME, GLASS_H - 2 * FRAME)
    add("IFCWINDOW", f"CW {label} — Glass Panel",
        xwall, FRAME, MAIN_TOP + FRAME,
        GLASS_THK, MAIN_WID - 2 * FRAME, GLASS_H - 2 * FRAME,
        descr="Float plate glass 25mm — short side")

# Door — south face center bay
mid_panel_x = long_xs[1] + FRAME // 2 + ((long_xs[2] - long_xs[1] - FRAME) - DOOR_WIDTH) // 2
add("IFCDOOR", "South Pivot Door",
    mid_panel_x, -DOOR_THK // 2, MAIN_TOP,
    DOOR_WIDTH, DOOR_THK, DOOR_HEIGHT,
    descr="Stainless frame + glass, pivot")

# Stairs (grade -> terrace, terrace -> main)
add("IFCSTAIR", "Stair — Grade to Terrace",
    TERRACE_X0 - STAIR_RISERS * STAIR_TREAD,
    TERRACE_Y0 + (TERRACE_WID - STAIR_WIDTH) // 2,
    0,
    STAIR_RISERS * STAIR_TREAD, STAIR_WIDTH, TERRACE_TOP,
    descr=f"5R × 4T (riser 160, tread 280), travertine")
add("IFCSTAIR", "Stair — Terrace to Main",
    -STAIR_RISERS * STAIR_TREAD,
    (MAIN_WID - STAIR_WIDTH) // 2,
    TERRACE_TOP,
    STAIR_RISERS * STAIR_TREAD, STAIR_WIDTH, MAIN_TOP - TERRACE_TOP,
    descr=f"5R × 4T (riser 160, tread 280), travertine")

# Railings (one per stair)
add("IFCRAILING", "Stair 1 Handrail",
    TERRACE_X0 - STAIR_RISERS * STAIR_TREAD,
    TERRACE_Y0 + (TERRACE_WID - STAIR_WIDTH) // 2 + STAIR_WIDTH - 40,
    TERRACE_TOP + 850,
    STAIR_RISERS * STAIR_TREAD, 40, 40,
    descr="Stainless rail 40×40")
add("IFCRAILING", "Stair 2 Handrail",
    -STAIR_RISERS * STAIR_TREAD,
    (MAIN_WID - STAIR_WIDTH) // 2 + STAIR_WIDTH - 40,
    MAIN_TOP + 850,
    STAIR_RISERS * STAIR_TREAD, 40, 40,
    descr="Stainless rail 40×40")

# Pavilion space
add("IFCSPACE", "Pavilion (universal space)",
    glass_x0 + GLASS_THK, GLASS_THK, MAIN_TOP,
    glass_long - 2 * GLASS_THK,
    MAIN_WID - 2 * GLASS_THK,
    GLASS_H,
    descr="Mies's universal space — single open living volume")


payload = {
    "slug": SLUG,
    "elements": elements,
    "note": "Farnsworth House v2 (LOD 350) — Mies van der Rohe, 1951 — "
            "type-bound, gridded, curtain-walled, 38/38 Revit/ArchiCAD checks PASS",
}

req = urllib.request.Request(
    API,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
print(f"POST {API}")
print(f"  elements: {len(elements)}")
classes = {}
for e in elements:
    classes[e["cls"]] = classes.get(e["cls"], 0) + 1
for c in sorted(classes):
    print(f"    {c:14s} {classes[c]}")

with urllib.request.urlopen(req, timeout=30) as resp:
    body = resp.read().decode()
    data = json.loads(body)
    print()
    print(f"  HTTP {resp.status}")
    print()
    print("=== bim.house URLs ===")
    print(f"  Viewer: https://bim.house{data['edit_url']}")
    print(f"  IFC:    https://bim.house{data['ifc_url']}")
    print(f"  JSON:   https://bim.house{data['json_url']}")
    print(f"  Wiki:   https://bim.house{data['wiki_url']}")
    print(f"  Token:  {data['token']}")
