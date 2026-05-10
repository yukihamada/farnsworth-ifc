"""Comprehensive validation of farnsworth_house_v2.ifc — all 9 BIM-grade features."""

from __future__ import annotations

import os
import sys

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.validate

PATH = os.path.join(os.path.dirname(__file__), "farnsworth_house_v2.ifc")


def _h(title):
    print()
    print("=" * 72)
    print(f" {title}")
    print("=" * 72)


def main() -> int:
    f = ifcopenshell.open(PATH)
    print(f"File:   {PATH} ({os.path.getsize(PATH) / 1024:.1f} KB)")
    print(f"Schema: {f.schema}")

    failed = []

    # ----- 0. Schema -------------------------------------------------------
    _h("0. Schema validation")
    logger = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, logger=logger, express_rules=True)
    issues = logger.statements
    if issues:
        print(f"  ✗ {len(issues)} schema/rule violations")
        for i in issues[:10]:
            print("   -", str(i.get("message") or i)[:200])
        failed.append("schema")
    else:
        print("  ✓ buildingSMART IFC4 + EXPRESS rules: PASS")

    # ----- 1. Type definitions ---------------------------------------------
    _h("1. IfcXxxType + IfcRelDefinesByType")
    expected_types = [
        "IfcSlabType", "IfcColumnType", "IfcCurtainWallType",
        "IfcMemberType", "IfcPlateType", "IfcDoorType",
        "IfcStairType", "IfcStairFlightType", "IfcRailingType",
        "IfcSpaceType",
    ]
    counts = {t: len(f.by_type(t)) for t in expected_types}
    for t, n in counts.items():
        mark = "✓" if n > 0 else "✗"
        print(f"  {mark} {t:30s} {n}")
    rels = f.by_type("IfcRelDefinesByType")
    print(f"  IfcRelDefinesByType: {len(rels)}")
    typed_objects = sum(len(r.RelatedObjects) for r in rels)
    print(f"  Total typed instances: {typed_objects}")
    if any(n == 0 for n in counts.values()):
        failed.append("types")

    # ----- 2. Curtain walls ------------------------------------------------
    _h("2. IfcCurtainWall + IfcMember/IfcPlate")
    cws = f.by_type("IfcCurtainWall")
    members = f.by_type("IfcMember")
    plates = f.by_type("IfcPlate")
    print(f"  curtain walls: {len(cws)}")
    print(f"  members:       {len(members)}")
    print(f"  plates:        {len(plates)}")
    for cw in cws:
        parts = []
        for rel in cw.IsDecomposedBy or []:
            parts.extend(rel.RelatedObjects)
        m = sum(1 for p in parts if p.is_a("IfcMember"))
        pl = sum(1 for p in parts if p.is_a("IfcPlate"))
        print(f"   - {cw.Name}: {len(parts)} parts ({m} members, {pl} plates)")
    if not cws or not members or not plates:
        failed.append("curtain_wall")

    # ----- 3. Openings + door ----------------------------------------------
    _h("3. IfcOpeningElement + IfcDoor + Rels")
    openings = f.by_type("IfcOpeningElement")
    doors = f.by_type("IfcDoor")
    voids = f.by_type("IfcRelVoidsElement")
    fills = f.by_type("IfcRelFillsElement")
    print(f"  openings: {len(openings)}, doors: {len(doors)}, voids: {len(voids)}, fills: {len(fills)}")
    for v in voids:
        print(f"   - opening {v.RelatedOpeningElement.Name} voids "
              f"{v.RelatingBuildingElement.is_a()} '{v.RelatingBuildingElement.Name}'")
    for fi in fills:
        print(f"   - door '{fi.RelatedBuildingElement.Name}' fills opening "
              f"'{fi.RelatingOpeningElement.Name}'")
    if not (openings and doors and voids and fills):
        failed.append("openings")

    # ----- 4. Grid ---------------------------------------------------------
    _h("4. IfcGrid")
    grids = f.by_type("IfcGrid")
    for g in grids:
        u = list(g.UAxes or [])
        v = list(g.VAxes or [])
        print(f"  grid '{g.Name}'")
        print(f"   U axes ({len(u)}): " + ", ".join(a.AxisTag for a in u))
        print(f"   V axes ({len(v)}): " + ", ".join(a.AxisTag for a in v))
    if not grids:
        failed.append("grid")

    # ----- 5. Stairs -------------------------------------------------------
    _h("5. IfcStairFlight with riser/tread metadata + railing")
    stairs = f.by_type("IfcStair")
    flights = f.by_type("IfcStairFlight")
    railings = f.by_type("IfcRailing")
    print(f"  stairs:   {len(stairs)}")
    print(f"  flights:  {len(flights)}")
    print(f"  railings: {len(railings)}")
    for fl in flights:
        print(f"   - {fl.Name}: {fl.NumberOfRisers}R × {fl.NumberOfTreads}T  "
              f"(riser={fl.RiserHeight}m, tread={fl.TreadLength}m)")
    if not (stairs and flights and railings):
        failed.append("stairs")

    # ----- 6. Material layer/profile sets ----------------------------------
    _h("6. IfcMaterialLayerSet / IfcMaterialProfileSet")
    layer_sets = f.by_type("IfcMaterialLayerSet")
    layer_usages = f.by_type("IfcMaterialLayerSetUsage")
    profile_sets = f.by_type("IfcMaterialProfileSet")
    profile_usages = f.by_type("IfcMaterialProfileSetUsage")
    print(f"  layer sets:    {len(layer_sets)}")
    print(f"  layer usages:  {len(layer_usages)}")
    print(f"  profile sets:  {len(profile_sets)}")
    print(f"  profile usages:{len(profile_usages)}")
    for ls in layer_sets:
        layers = ls.MaterialLayers or []
        total = sum(l.LayerThickness for l in layers)
        composition = " + ".join(
            f"{l.Material.Name} {l.LayerThickness*1000:.0f}mm"
            for l in layers
        )
        print(f"   - {ls.LayerSetName} (total {total*1000:.0f}mm): {composition}")
    for ps in profile_sets:
        profiles = ps.MaterialProfiles or []
        for mp in profiles:
            prof = mp.Profile
            mat = mp.Material
            print(f"   - {ps.Name}: {prof.is_a()} {prof.ProfileName} of {mat.Name}")
    if not (layer_sets and layer_usages and profile_sets):
        failed.append("layered_materials")

    # ----- 7. Space boundaries ---------------------------------------------
    _h("7. IfcRelSpaceBoundary")
    spaces = f.by_type("IfcSpace")
    boundaries = f.by_type("IfcRelSpaceBoundary")
    print(f"  spaces:     {len(spaces)}")
    print(f"  boundaries: {len(boundaries)}")
    for sp in spaces:
        bs = [b for b in boundaries if b.RelatingSpace == sp]
        print(f"   - space '{sp.Name}': {len(bs)} boundaries")
        for b in bs:
            be = b.RelatedBuildingElement
            print(f"      <-> {be.is_a()} '{be.Name}'  "
                  f"({b.PhysicalOrVirtualBoundary}/{b.InternalOrExternalBoundary})")
    if not boundaries:
        failed.append("space_boundary")

    # ----- 8. Quantities (Qto_*) ------------------------------------------
    _h("8. Qto_* base quantities")
    qtos = f.by_type("IfcElementQuantity")
    print(f"  Qto sets: {len(qtos)}")
    classes_with_qto = {}
    rels_qto = [r for r in f.by_type("IfcRelDefinesByProperties")
                if r.RelatingPropertyDefinition.is_a("IfcElementQuantity")]
    for r in rels_qto:
        for obj in r.RelatedObjects:
            classes_with_qto.setdefault(obj.is_a(), 0)
            classes_with_qto[obj.is_a()] += 1
    for cls in sorted(classes_with_qto):
        print(f"   - {cls:25s} {classes_with_qto[cls]} Qto-bound")
    if not qtos:
        failed.append("quantities")

    # ----- 9. Storey-relative placements -----------------------------------
    _h("9. Storey-relative IfcLocalPlacement")
    elements = [
        p for p in f.by_type("IfcProduct")
        if not p.is_a("IfcSpatialStructureElement")
        and not p.is_a("IfcOpeningElement")
        and not p.is_a("IfcGrid")
        and getattr(p, "ObjectPlacement", None)
    ]
    storey_rooted = 0
    other_rooted = 0
    world_rooted = 0
    for el in elements:
        # Walk up PlacementRelTo chain looking for a storey
        ph = el.ObjectPlacement
        chain_storey = False
        depth = 0
        while ph is not None and depth < 8:
            owners = [o for o in f.get_inverse(ph)
                      if o.is_a("IfcSpatialStructureElement")]
            if any(o.is_a("IfcBuildingStorey") for o in owners):
                chain_storey = True
                break
            ph = ph.PlacementRelTo
            depth += 1
        if chain_storey:
            storey_rooted += 1
        elif ph is None:
            world_rooted += 1
        else:
            other_rooted += 1
    print(f"  total physical products: {len(elements)}")
    print(f"  rooted under a storey:   {storey_rooted}")
    print(f"  rooted in world:         {world_rooted}")
    print(f"  other:                   {other_rooted}")
    if storey_rooted < len(elements) - 2:
        failed.append("storey_placement")

    # ----- 10. Geometry kernel pass ----------------------------------------
    _h("10. Geometry kernel sanity")
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    products = [
        p for p in f.by_type("IfcProduct")
        if getattr(p, "Representation", None)
        and not p.is_a("IfcGrid")  # grid is 2D-only, 3D kernel skips
    ]
    ok = 0
    bad = []
    bbox_min = [float("inf")] * 3
    bbox_max = [float("-inf")] * 3
    total_v = 0
    for p in products:
        try:
            shape = ifcopenshell.geom.create_shape(settings, p)
            verts = shape.geometry.verts
            total_v += len(verts) // 3
            for i in range(0, len(verts), 3):
                for j in range(3):
                    bbox_min[j] = min(bbox_min[j], verts[i + j])
                    bbox_max[j] = max(bbox_max[j], verts[i + j])
            ok += 1
        except Exception as e:
            bad.append((p.is_a(), p.Name, str(e)[:100]))
    print(f"  products with geometry: {len(products)}")
    print(f"  shape kernel OK:        {ok}")
    print(f"  failures:               {len(bad)}")
    print(f"  total vertices:         {total_v}")
    print(f"  world bbox: ({bbox_min[0]:+.2f}, {bbox_min[1]:+.2f}, {bbox_min[2]:+.2f}) -> "
          f"({bbox_max[0]:+.2f}, {bbox_max[1]:+.2f}, {bbox_max[2]:+.2f}) m")
    print(f"  size:       {bbox_max[0] - bbox_min[0]:.2f} × "
          f"{bbox_max[1] - bbox_min[1]:.2f} × {bbox_max[2] - bbox_min[2]:.2f} m")
    for cls, name, err in bad[:10]:
        print(f"   ✗ {cls} '{name}': {err}")
    if bad:
        failed.append("geometry")

    # ----- summary ---------------------------------------------------------
    _h("Summary")
    if failed:
        print(f"  FAIL — {len(failed)} sections need attention: {', '.join(failed)}")
        return 1
    print("  ALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
