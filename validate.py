"""Validate the generated IFC: schema, spatial tree, geometry, materials."""

from __future__ import annotations

import os
import sys

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.validate

PATH = os.path.join(os.path.dirname(__file__), "farnsworth_house.ifc")


def main() -> int:
    f = ifcopenshell.open(PATH)
    print(f"Schema: {f.schema}")
    print(f"File:   {PATH} ({os.path.getsize(PATH) / 1024:.1f} KB)")

    # 1. Schema validation
    print("\n[1/4] Schema validation (ifcopenshell.validate.validate)…")
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger=logger, express_rules=True)
    issues = logger.statements
    if not issues:
        print("  OK — no schema issues.")
    else:
        print(f"  {len(issues)} issues:")
        for i in issues[:20]:
            print("   -", i.get("message") or i)

    # 2. Spatial hierarchy
    print("\n[2/4] Spatial hierarchy:")
    project = f.by_type("IfcProject")[0]

    def walk(elem, depth=0):
        label = f"{elem.is_a()} '{elem.Name or ''}'"
        print("  " + "  " * depth + "- " + label)
        for rel in getattr(elem, "IsDecomposedBy", []) or []:
            for child in rel.RelatedObjects:
                walk(child, depth + 1)
        for rel in getattr(elem, "ContainsElements", []) or []:
            for child in rel.RelatedElements:
                kind = child.is_a()
                name = child.Name or ""
                print("  " + "  " * (depth + 1) + f"  · {kind}: {name}")

    walk(project)

    # 3. Geometry — confirm every product yields a valid mesh
    print("\n[3/4] Geometry kernel pass:")
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    products = [
        p
        for p in f.by_type("IfcProduct")
        if getattr(p, "Representation", None) is not None
    ]
    failures = []
    bbox_min = [float("inf")] * 3
    bbox_max = [float("-inf")] * 3
    total_vertices = 0
    for p in products:
        try:
            shape = ifcopenshell.geom.create_shape(settings, p)
            verts = shape.geometry.verts
            total_vertices += len(verts) // 3
            for i in range(0, len(verts), 3):
                x, y, z = verts[i], verts[i + 1], verts[i + 2]
                bbox_min[0] = min(bbox_min[0], x)
                bbox_min[1] = min(bbox_min[1], y)
                bbox_min[2] = min(bbox_min[2], z)
                bbox_max[0] = max(bbox_max[0], x)
                bbox_max[1] = max(bbox_max[1], y)
                bbox_max[2] = max(bbox_max[2], z)
        except Exception as e:
            failures.append((p.is_a(), p.Name, str(e)[:80]))
    print(f"  products with geometry: {len(products)}")
    print(f"  total vertices: {total_vertices}")
    print(f"  bounding box: "
          f"({bbox_min[0]:.2f}, {bbox_min[1]:.2f}, {bbox_min[2]:.2f}) -> "
          f"({bbox_max[0]:.2f}, {bbox_max[1]:.2f}, {bbox_max[2]:.2f})")
    print(f"  size: {bbox_max[0] - bbox_min[0]:.2f} × "
          f"{bbox_max[1] - bbox_min[1]:.2f} × "
          f"{bbox_max[2] - bbox_min[2]:.2f} m")
    if failures:
        print(f"  geometry failures ({len(failures)}):")
        for kind, name, err in failures:
            print(f"   - {kind} '{name}': {err}")
    else:
        print("  OK — every product produced valid geometry.")

    # 4. Material + Pset coverage
    print("\n[4/4] Material / Pset coverage:")
    products_with_geom = [p for p in products if not p.is_a("IfcSpatialElement")]
    matless = []
    psetless = []
    for p in products_with_geom:
        mat = ifcopenshell.util.element.get_material(p)
        if mat is None:
            matless.append(p)
        psets = ifcopenshell.util.element.get_psets(p)
        if not psets:
            psetless.append(p)
    print(f"  physical products: {len(products_with_geom)}")
    print(f"  without material:   {len(matless)}")
    print(f"  without pset:       {len(psetless)}")
    if matless:
        for p in matless:
            print(f"   - missing material: {p.is_a()} '{p.Name}'")

    return 0 if not (issues or failures) else 1


if __name__ == "__main__":
    sys.exit(main())
