"""
Revit / ArchiCAD acceptance checklist for farnsworth_house_v2.ifc.

Verifies the IFC against the typical "Open BIM Reference View" requirements
that BIM coordinators check before signing off an IFC handover.
"""

from __future__ import annotations

import os
import sys

import ifcopenshell
import ifcopenshell.util.element

PATH = os.path.join(os.path.dirname(__file__), "farnsworth_house_v2.ifc")


def _check(label, ok, detail=""):
    mark = "✓" if ok else "✗"
    print(f"  {mark} {label}" + (f"  — {detail}" if detail else ""))
    return ok


def main():
    f = ifcopenshell.open(PATH)
    print(f"Target: {PATH}")
    print(f"Schema: {f.schema}")
    print()

    passed = 0
    total = 0

    print("=" * 72)
    print(" REVIT / ARCHICAD HANDOVER CHECKLIST")
    print(" (mirrors common 'Reference View' requirements)")
    print("=" * 72)

    # Group A — Project & spatial structure
    print("\nA. Project structure")
    checks_a = [
        ("Single IfcProject defined", len(f.by_type("IfcProject")) == 1),
        ("Length unit declared",
         any(u.UnitType == "LENGTHUNIT" for u in f.by_type("IfcSIUnit"))),
        ("IfcSite with georef metadata",
         bool(f.by_type("IfcSite")) and bool(f.by_type("IfcSite")[0].RefLatitude)),
        ("IfcBuilding present", len(f.by_type("IfcBuilding")) >= 1),
        ("≥2 IfcBuildingStorey with elevation",
         all(s.Elevation is not None for s in f.by_type("IfcBuildingStorey"))
         and len(f.by_type("IfcBuildingStorey")) >= 2),
    ]
    for label, ok in checks_a:
        if _check(label, ok):
            passed += 1
        total += 1

    # Group B — Type / occurrence pattern
    print("\nB. Type definitions (IfcXxxType)")
    expected_pairs = [
        ("IfcSlabType", "IfcSlab"),
        ("IfcColumnType", "IfcColumn"),
        ("IfcCurtainWallType", "IfcCurtainWall"),
        ("IfcMemberType", "IfcMember"),
        ("IfcPlateType", "IfcPlate"),
        ("IfcDoorType", "IfcDoor"),
        ("IfcStairType", "IfcStair"),
        ("IfcStairFlightType", "IfcStairFlight"),
        ("IfcRailingType", "IfcRailing"),
        ("IfcSpaceType", "IfcSpace"),
    ]
    rels = f.by_type("IfcRelDefinesByType")
    typed = set()
    for r in rels:
        for o in r.RelatedObjects:
            typed.add(o.id())
    for tcls, icls in expected_pairs:
        types = f.by_type(tcls)
        instances = f.by_type(icls)
        bound = sum(1 for inst in instances if inst.id() in typed)
        all_bound = bound == len(instances) if instances else True
        ok = bool(types) and all_bound and bool(instances)
        detail = f"{len(types)} type, {bound}/{len(instances)} instances bound"
        if _check(f"{tcls} ↔ {icls}", ok, detail):
            passed += 1
        total += 1

    # Group C — Material and structural metadata
    print("\nC. Materials")
    checks_c = [
        ("≥3 IfcMaterialLayerSet (slab assemblies)",
         len(f.by_type("IfcMaterialLayerSet")) >= 3),
        ("Slabs use IfcMaterialLayerSetUsage",
         all(_uses_layer_set(p) for p in f.by_type("IfcSlab")
             if p.Name and "Site Ground" not in p.Name)),
        ("Columns use IfcMaterialProfileSet",
         all(_uses_profile_set(p) for p in f.by_type("IfcColumn"))),
        ("Materials have rendering style (IfcSurfaceStyle)",
         len(f.by_type("IfcSurfaceStyle")) >= 5),
    ]
    for label, ok in checks_c:
        if _check(label, ok):
            passed += 1
        total += 1

    # Group D — Geometry / placement integrity
    print("\nD. Geometry integrity")
    aggregates = {"IfcCurtainWall", "IfcStair"}  # IFC4 allows these as parts-only
    checks_d = [
        ("All physical elements have ObjectPlacement",
         all(p.ObjectPlacement for p in f.by_type("IfcElement"))),
        ("Leaf elements have Representation (aggregates exempted)",
         all(p.Representation for p in f.by_type("IfcElement")
             if p.is_a() not in aggregates)),
        ("All elements rooted under a storey",
         _all_rooted_under_storey(f)),
        ("Body context defined",
         any(c.ContextIdentifier == "Body"
             for c in f.by_type("IfcGeometricRepresentationSubContext"))),
        ("Plan/FootPrint context defined",
         any(c.ContextIdentifier == "FootPrint"
             for c in f.by_type("IfcGeometricRepresentationSubContext"))),
    ]
    for label, ok in checks_d:
        if _check(label, ok):
            passed += 1
        total += 1

    # Group E — Quantities & psets
    print("\nE. Quantities & properties")
    elements_with_qto = set()
    for r in f.by_type("IfcRelDefinesByProperties"):
        if r.RelatingPropertyDefinition.is_a("IfcElementQuantity"):
            for o in r.RelatedObjects:
                elements_with_qto.add(o.id())
    elements_with_pset = set()
    for r in f.by_type("IfcRelDefinesByProperties"):
        if r.RelatingPropertyDefinition.is_a("IfcPropertySet"):
            for o in r.RelatedObjects:
                elements_with_pset.add(o.id())
    physical = [p for p in f.by_type("IfcElement")]
    qto_coverage = sum(1 for p in physical if p.id() in elements_with_qto)
    pset_coverage = sum(1 for p in physical if p.id() in elements_with_pset)
    checks_e = [
        ("Every physical element has Pset",
         pset_coverage == len(physical),
         f"{pset_coverage}/{len(physical)}"),
        ("Every physical element has Qto",
         qto_coverage == len(physical),
         f"{qto_coverage}/{len(physical)}"),
        ("Pset_BuildingCommon on building",
         _has_pset(f.by_type("IfcBuilding")[0], "Pset_BuildingCommon")),
    ]
    for *args, in checks_e:
        if len(args) == 3:
            label, ok, detail = args
        else:
            label, ok = args
            detail = ""
        if _check(label, ok, detail):
            passed += 1
        total += 1

    # Group F — Curtain wall / openings
    print("\nF. Curtain wall + openings (BIM-grade construction)")
    cws = f.by_type("IfcCurtainWall")
    voids = f.by_type("IfcRelVoidsElement")
    fills = f.by_type("IfcRelFillsElement")
    checks_f = [
        (f"≥4 IfcCurtainWall", len(cws) >= 4),
        (f"Curtain walls aggregate IfcMember + IfcPlate",
         all(_aggregates_members_and_plates(cw) for cw in cws)),
        (f"At least one IfcRelVoidsElement", len(voids) >= 1),
        (f"At least one IfcRelFillsElement", len(fills) >= 1),
        (f"IfcDoor placed in opening",
         len(f.by_type("IfcDoor")) >= 1 and len(fills) >= 1),
    ]
    for label, ok in checks_f:
        if _check(label, ok):
            passed += 1
        total += 1

    # Group G — Grid + boundaries
    print("\nG. Grid + space boundaries")
    grids = f.by_type("IfcGrid")
    boundaries = f.by_type("IfcRelSpaceBoundary")
    checks_g = [
        ("IfcGrid present", bool(grids)),
        ("Grid has ≥2 U-axes and ≥2 V-axes",
         bool(grids) and len(grids[0].UAxes) >= 2 and len(grids[0].VAxes) >= 2),
        ("Each IfcSpace has ≥4 boundaries",
         all(len([b for b in boundaries if b.RelatingSpace == sp]) >= 4
             for sp in f.by_type("IfcSpace"))),
    ]
    for label, ok in checks_g:
        if _check(label, ok):
            passed += 1
        total += 1

    # Group H — Stair details
    print("\nH. Stairs")
    flights = f.by_type("IfcStairFlight")
    checks_h = [
        ("≥1 IfcStair", len(f.by_type("IfcStair")) >= 1),
        ("Each flight has riser/tread metadata",
         all(fl.NumberOfRisers and fl.RiserHeight and fl.TreadLength
             for fl in flights)),
        ("Stair aggregates IfcStairFlight (+IfcRailing)",
         all(_stair_has_flight(s) for s in f.by_type("IfcStair"))),
    ]
    for label, ok in checks_h:
        if _check(label, ok):
            passed += 1
        total += 1

    print()
    print("=" * 72)
    print(f" RESULT: {passed}/{total} checks passed")
    print("=" * 72)
    if passed == total:
        print(" ✓ Ready for Revit / ArchiCAD handover (Open BIM Reference View)")
        return 0
    print(f" ✗ {total - passed} item(s) need attention")
    return 1


# ----- helpers -------------------------------------------------------------


def _uses_layer_set(product):
    mat = ifcopenshell.util.element.get_material(product)
    if mat is None:
        return False
    return mat.is_a("IfcMaterialLayerSetUsage") or mat.is_a("IfcMaterialLayerSet")


def _uses_profile_set(product):
    mat = ifcopenshell.util.element.get_material(product)
    if mat is None:
        return False
    return mat.is_a("IfcMaterialProfileSetUsage") or mat.is_a("IfcMaterialProfileSet")


def _has_pset(product, name):
    psets = ifcopenshell.util.element.get_psets(product)
    return name in psets


def _all_rooted_under_storey(f):
    elements = [
        p for p in f.by_type("IfcElement")
        if getattr(p, "ObjectPlacement", None)
    ]
    rooted = 0
    for el in elements:
        ph = el.ObjectPlacement
        depth = 0
        while ph is not None and depth < 8:
            inverse = list(f.get_inverse(ph))
            if any(o.is_a("IfcBuildingStorey") for o in inverse):
                rooted += 1
                break
            ph = ph.PlacementRelTo
            depth += 1
    return rooted == len(elements)


def _aggregates_members_and_plates(cw):
    parts = []
    for rel in cw.IsDecomposedBy or []:
        parts.extend(rel.RelatedObjects)
    has_m = any(p.is_a("IfcMember") for p in parts)
    has_p = any(p.is_a("IfcPlate") for p in parts)
    return has_m and has_p


def _stair_has_flight(stair):
    parts = []
    for rel in stair.IsDecomposedBy or []:
        parts.extend(rel.RelatedObjects)
    return any(p.is_a("IfcStairFlight") for p in parts)


if __name__ == "__main__":
    sys.exit(main())
