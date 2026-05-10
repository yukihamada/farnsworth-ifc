"""Render the IFC to a PNG isometric view for visual verification."""

from __future__ import annotations

import os

import ifcopenshell
import ifcopenshell.geom
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection

PATH = os.path.join(os.path.dirname(__file__), "farnsworth_house.ifc")
OUT = os.path.join(os.path.dirname(__file__), "farnsworth_house.png")

COLOURS = {
    "IfcSlab": "#dcd2bf",
    "IfcColumn": "#fafaf7",
    "IfcWall": "#a8c9d8",
    "IfcStair": "#cbb98c",
    "IfcGeographicElement": "#7c9954",
}
ALPHAS = {
    "IfcWall": 0.45,
    "IfcGeographicElement": 0.85,
}


def iso_project(verts: np.ndarray) -> np.ndarray:
    """Standard 30°/30° isometric projection."""
    angle_x = np.deg2rad(30)
    angle_y = np.deg2rad(30)
    cx, sx = np.cos(angle_x), np.sin(angle_x)
    cy, sy = np.cos(angle_y), np.sin(angle_y)
    M = np.array([
        [cy, 0, sy],
        [sx * sy, cx, -sx * cy],
    ])
    return verts @ M.T


def main():
    f = ifcopenshell.open(PATH)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)

    fig, ax = plt.subplots(figsize=(14, 8), dpi=140)

    by_class: dict[str, list] = {}
    for product in f.by_type("IfcProduct"):
        if not getattr(product, "Representation", None):
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, product)
        except Exception:
            continue
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces).reshape(-1, 3)
        cls = product.is_a()
        by_class.setdefault(cls, []).append((verts, faces, product))

    # Draw furthest first (geographic, slabs) then near (columns, walls).
    draw_order = [
        "IfcGeographicElement", "IfcSlab", "IfcStair", "IfcColumn", "IfcWall",
    ]
    for cls in draw_order:
        items = by_class.get(cls, [])
        polys = []
        for verts, faces, product in items:
            projected = iso_project(verts)
            for tri in faces:
                polys.append(projected[tri])
        if not polys:
            continue
        coll = PolyCollection(
            polys,
            facecolor=COLOURS.get(cls, "#cccccc"),
            edgecolor=(0, 0, 0, 0.35),
            linewidths=0.3,
            alpha=ALPHAS.get(cls, 1.0),
        )
        ax.add_collection(coll)

    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_axis_off()
    ax.set_title(
        "Farnsworth House — Mies van der Rohe (1951) — IFC4 LOD 300",
        fontsize=12, color="#222",
    )

    fig.patch.set_facecolor("#f4f1ea")
    plt.tight_layout()
    plt.savefig(OUT, facecolor=fig.get_facecolor(), bbox_inches="tight")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
