"""Render v2 IFC to PNG for visual confirmation."""

from __future__ import annotations

import os

import ifcopenshell
import ifcopenshell.geom
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import PolyCollection

PATH = os.path.join(os.path.dirname(__file__), "farnsworth_house_v2.ifc")
OUT = os.path.join(os.path.dirname(__file__), "farnsworth_house_v2.png")

COLOURS = {
    "IfcSlab": "#dcd2bf",
    "IfcColumn": "#fafaf7",
    "IfcCurtainWall": None,    # aggregate, no own geometry
    "IfcMember": "#c0c4c8",
    "IfcPlate": "#a8c9d8",
    "IfcDoor": "#888a8c",
    "IfcStair": None,
    "IfcStairFlight": "#cbb98c",
    "IfcRailing": "#8a8a85",
    "IfcSpace": None,           # don't render in iso
    "IfcOpeningElement": None,
    "IfcGrid": None,
}
ALPHAS = {
    "IfcPlate": 0.42,
    "IfcDoor": 0.85,
    "IfcSpace": 0.10,
}


def iso_project(verts: np.ndarray) -> np.ndarray:
    angle_x = np.deg2rad(30)
    angle_y = np.deg2rad(30)
    cx, sx = np.cos(angle_x), np.sin(angle_x)
    cy, sy = np.cos(angle_y), np.sin(angle_y)
    M = np.array([[cy, 0, sy], [sx * sy, cx, -sx * cy]])
    return verts @ M.T


def main():
    f = ifcopenshell.open(PATH)
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)

    fig, ax = plt.subplots(figsize=(15, 9), dpi=150)

    by_class: dict[str, list] = {}
    for product in f.by_type("IfcProduct"):
        if not getattr(product, "Representation", None):
            continue
        if product.is_a("IfcGrid"):
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, product)
        except Exception:
            continue
        verts = np.array(shape.geometry.verts).reshape(-1, 3)
        faces = np.array(shape.geometry.faces).reshape(-1, 3)
        cls = product.is_a()
        by_class.setdefault(cls, []).append((verts, faces))

    # Add a "ground" pseudo-class to draw last (top)
    draw_order = [
        "IfcSlab",
        "IfcStairFlight",
        "IfcRailing",
        "IfcMember",
        "IfcColumn",
        "IfcPlate",
        "IfcDoor",
    ]
    for cls in draw_order:
        items = by_class.get(cls, [])
        if not items or COLOURS.get(cls) is None:
            continue
        polys = []
        for verts, faces in items:
            projected = iso_project(verts)
            for tri in faces:
                polys.append(projected[tri])
        coll = PolyCollection(
            polys,
            facecolor=COLOURS[cls],
            edgecolor=(0, 0, 0, 0.32),
            linewidths=0.25,
            alpha=ALPHAS.get(cls, 1.0),
        )
        ax.add_collection(coll)

    ax.set_aspect("equal")
    ax.autoscale_view()
    ax.set_axis_off()
    ax.set_title(
        "Farnsworth House — IFC4 v2 (LOD 350) — type-bound, layered, gridded",
        fontsize=12, color="#222",
    )
    fig.patch.set_facecolor("#f4f1ea")
    plt.tight_layout()
    plt.savefig(OUT, facecolor=fig.get_facecolor(), bbox_inches="tight")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
