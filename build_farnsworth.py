"""
Farnsworth House (Mies van der Rohe, 1951) — IFC4 Reference View, LOD 300.
Generates farnsworth_house.ifc using ifcopenshell.

Spatial hierarchy:
  IfcProject "Farnsworth House"
    IfcSite "Plano, Illinois (Fox River)"
      IfcBuilding "Farnsworth House"
        IfcBuildingStorey "Terrace Level"   (+0.80 m)
        IfcBuildingStorey "Main Floor"      (+1.60 m)
        IfcBuildingStorey "Roof"            (+4.75 m)

Coordinate convention: X = long axis (E-W), Y = short axis (N-S), Z = up.
Origin at SW corner of main pavilion floor slab projection on grade.
Units: metres / radians.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.material
import ifcopenshell.api.owner
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.style
import ifcopenshell.api.unit
import ifcopenshell.guid

# ---------------------------------------------------------------------------
# Dimensions (metres) — Farnsworth House, simplified for LOD 300
# ---------------------------------------------------------------------------

# Main pavilion (enclosed)
MAIN_LEN = 23.47          # 77 ft 0 in
MAIN_WID = 8.74           # 28 ft 8 in
MAIN_TOP = 1.60           # main floor finished surface elevation
MAIN_THK = 0.30           # floor slab thickness (incl. travertine + structure)

# Terrace
TERRACE_LEN = 16.76       # 55 ft
TERRACE_WID = 6.71        # 22 ft
TERRACE_TOP = 0.80        # terrace finished surface elevation
TERRACE_THK = 0.25
TERRACE_OFFSET_X = -TERRACE_LEN - 0.0   # placed west (i.e. negative X) of pavilion

# Roof slab
ROOF_BOT = 4.50           # underside of roof = ceiling height 2.90 m above main
ROOF_THK = 0.25

# Steel columns: W-section approximated as H-shape (simplified rectangle of flange box)
COL_DEPTH = 0.21          # depth in long direction
COL_WIDTH = 0.21          # flange width
COL_BASE = 0.0
COL_TOP = ROOF_BOT + ROOF_THK
COL_CANTILEVER = 1.68     # 5 ft 6 in cantilever beyond end columns
COL_BAY = 6.71            # 22 ft bay
N_BAYS = 3                # 4 columns per long side -> 3 bays

# Glass enclosure (sits on main floor, between columns)
GLASS_HEIGHT = 2.90
GLASS_THK = 0.025

# Site
SITE_LEN = 60.0
SITE_WID = 30.0


@dataclass
class Ctx:
    f: ifcopenshell.file
    body: object
    plan: object
    storey_grade: object
    storey_terrace: object
    storey_main: object
    storey_roof: object
    mat_steel: object
    mat_travertine: object
    mat_glass: object
    mat_stone: object
    mat_earth: object


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pt(f, xyz):
    return f.create_entity("IfcCartesianPoint", Coordinates=[float(c) for c in xyz])


def _dir(f, xyz):
    return f.create_entity("IfcDirection", DirectionRatios=[float(c) for c in xyz])


def _axis2_3d(f, location=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)):
    return f.create_entity(
        "IfcAxis2Placement3D",
        Location=_pt(f, location),
        Axis=_dir(f, z),
        RefDirection=_dir(f, x),
    )


def _axis2_2d(f, location=(0, 0), x=(1, 0)):
    return f.create_entity(
        "IfcAxis2Placement2D",
        Location=f.create_entity("IfcCartesianPoint", Coordinates=[float(c) for c in location]),
        RefDirection=f.create_entity("IfcDirection", DirectionRatios=[float(c) for c in x]),
    )


def _local_placement(f, location=(0, 0, 0), parent=None):
    rel = _axis2_3d(f, location=location)
    return f.create_entity("IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=rel)


def _rect_profile(f, name, xdim, ydim):
    return f.create_entity(
        "IfcRectangleProfileDef",
        ProfileType="AREA",
        ProfileName=name,
        Position=_axis2_2d(f),
        XDim=float(xdim),
        YDim=float(ydim),
    )


def _extruded_rect(f, body_ctx, xdim, ydim, height, position=(0, 0, 0)):
    profile = _rect_profile(f, None, xdim, ydim)
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=_axis2_3d(f, location=position),
        ExtrudedDirection=_dir(f, (0, 0, 1)),
        Depth=float(height),
    )
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    return f.create_entity("IfcProductDefinitionShape", Representations=[rep])


def _h_section_profile(f, depth, width, web_thk=0.012, flange_thk=0.018):
    """I/H section as IfcIShapeProfileDef (simplified W8x48)."""
    return f.create_entity(
        "IfcIShapeProfileDef",
        ProfileType="AREA",
        ProfileName="W8x48",
        Position=_axis2_2d(f),
        OverallWidth=float(width),
        OverallDepth=float(depth),
        WebThickness=float(web_thk),
        FlangeThickness=float(flange_thk),
        FilletRadius=0.010,
    )


def _extruded_hsection(f, body_ctx, depth, width, height, position=(0, 0, 0)):
    profile = _h_section_profile(f, depth, width)
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=_axis2_3d(f, location=position),
        ExtrudedDirection=_dir(f, (0, 0, 1)),
        Depth=float(height),
    )
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    return f.create_entity("IfcProductDefinitionShape", Representations=[rep])


def _assign_material(f, element, material):
    ifcopenshell.api.material.assign_material(f, products=[element], material=material)


def _add_pset(f, element, name, props):
    pset = ifcopenshell.api.pset.add_pset(f, product=element, name=name)
    ifcopenshell.api.pset.edit_pset(f, pset=pset, properties=props)


def _create_surface_style(f, name, rgb, transparency=0.0):
    """Attach a coloured surface style to a material so viewers render it."""
    rendering = f.create_entity(
        "IfcSurfaceStyleShading",
        SurfaceColour=f.create_entity(
            "IfcColourRgb",
            Name=name,
            Red=float(rgb[0]),
            Green=float(rgb[1]),
            Blue=float(rgb[2]),
        ),
        Transparency=float(transparency),
    )
    style = f.create_entity(
        "IfcSurfaceStyle",
        Name=name,
        Side="BOTH",
        Styles=[rendering],
    )
    return style


def _attach_style_to_material(f, material, style, body_ctx):
    """Wrap an IfcSurfaceStyle so a material renders coloured in viewers."""
    pres = f.create_entity(
        "IfcPresentationStyleAssignment", Styles=[style]
    )
    styled_item = f.create_entity(
        "IfcStyledItem", Item=None, Styles=[pres], Name=None
    )
    rep = f.create_entity(
        "IfcStyledRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier=None,
        RepresentationType=None,
        Items=[styled_item],
    )
    f.create_entity(
        "IfcMaterialDefinitionRepresentation",
        Name=material.Name,
        RepresentedMaterial=material,
        Representations=[rep],
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build() -> ifcopenshell.file:
    f = ifcopenshell.api.project.create_file(version="IFC4")

    # Owner / application
    person = ifcopenshell.api.owner.add_person(
        f, identification="yuki", given_name="Yuki", family_name="Hamada"
    )
    org = ifcopenshell.api.owner.add_organisation(
        f, identification="ENABLER", name="Enabler Inc."
    )
    ifcopenshell.api.owner.add_person_and_organisation(f, person=person, organisation=org)
    ifcopenshell.api.owner.add_application(
        f,
        application_developer=org,
        version="0.1",
        application_full_name="Farnsworth IFC Generator",
        application_identifier="ENABLER-FARNSWORTH",
    )

    # Project
    project = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcProject", name="Farnsworth House"
    )
    project.LongName = "Farnsworth House — Mies van der Rohe, 1951"
    project.Phase = "As-built (reference model)"

    ifcopenshell.api.unit.assign_unit(
        f,
        length={"is_metric": True, "raw": "METERS"},
        area={"is_metric": True, "raw": "METERS"},
        volume={"is_metric": True, "raw": "METERS"},
    )

    # Geometric contexts
    model_ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        f,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_ctx,
    )
    plan_ctx = ifcopenshell.api.context.add_context(f, context_type="Plan")
    plan = ifcopenshell.api.context.add_context(
        f,
        context_type="Plan",
        context_identifier="Annotation",
        target_view="PLAN_VIEW",
        parent=plan_ctx,
    )

    # Spatial hierarchy
    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="Plano, Illinois")
    site.LongName = "Fox River, Plano, Illinois, USA"
    site.CompositionType = "ELEMENT"
    site.RefLatitude = (41, 39, 49)        # 41°39′49″N
    site.RefLongitude = (-88, -32, -10)    # 88°32′10″W
    site.RefElevation = 173.0              # m approx, Fox River floodplain

    building = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuilding", name="Farnsworth House"
    )
    building.CompositionType = "ELEMENT"

    storey_grade = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuildingStorey", name="Grade"
    )
    storey_grade.Elevation = 0.0
    storey_grade.CompositionType = "ELEMENT"

    storey_terrace = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuildingStorey", name="Terrace Level"
    )
    storey_terrace.Elevation = TERRACE_TOP
    storey_terrace.CompositionType = "ELEMENT"

    storey_main = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuildingStorey", name="Main Floor"
    )
    storey_main.Elevation = MAIN_TOP
    storey_main.CompositionType = "ELEMENT"

    storey_roof = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuildingStorey", name="Roof"
    )
    storey_roof.Elevation = ROOF_BOT + ROOF_THK
    storey_roof.CompositionType = "ELEMENT"

    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=project)
    ifcopenshell.api.aggregate.assign_object(f, products=[building], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(
        f,
        products=[storey_grade, storey_terrace, storey_main, storey_roof],
        relating_object=building,
    )

    # Materials + surface styles
    mat_steel = ifcopenshell.api.material.add_material(
        f, name="Steel — White Enamel", category="steel"
    )
    mat_travertine = ifcopenshell.api.material.add_material(
        f, name="Travertine", category="stone"
    )
    mat_glass = ifcopenshell.api.material.add_material(
        f, name="Float Glass — 25mm", category="glass"
    )
    mat_stone = ifcopenshell.api.material.add_material(
        f, name="Concrete (terrace structure)", category="concrete"
    )
    mat_earth = ifcopenshell.api.material.add_material(
        f, name="Meadow / Topsoil", category="soil"
    )

    style_steel = _create_surface_style(f, "Steel White", (0.95, 0.95, 0.93))
    style_travertine = _create_surface_style(f, "Travertine", (0.86, 0.81, 0.72))
    style_glass = _create_surface_style(f, "Glass", (0.65, 0.78, 0.85), transparency=0.7)
    style_stone = _create_surface_style(f, "Concrete", (0.78, 0.76, 0.72))
    style_earth = _create_surface_style(f, "Meadow", (0.45, 0.55, 0.30))

    _attach_style_to_material(f, mat_steel, style_steel, body)
    _attach_style_to_material(f, mat_travertine, style_travertine, body)
    _attach_style_to_material(f, mat_glass, style_glass, body)
    _attach_style_to_material(f, mat_stone, style_stone, body)
    _attach_style_to_material(f, mat_earth, style_earth, body)

    ctx = Ctx(
        f=f, body=body, plan=plan,
        storey_grade=storey_grade,
        storey_terrace=storey_terrace,
        storey_main=storey_main,
        storey_roof=storey_roof,
        mat_steel=mat_steel,
        mat_travertine=mat_travertine,
        mat_glass=mat_glass,
        mat_stone=mat_stone,
        mat_earth=mat_earth,
    )

    # ---- Slabs ---------------------------------------------------------
    _make_slab(
        ctx, name="Main Floor Slab",
        xdim=MAIN_LEN, ydim=MAIN_WID, thk=MAIN_THK,
        origin=(0, 0, MAIN_TOP - MAIN_THK),
        storey=storey_main, material=mat_travertine,
        predefined="FLOOR",
    )
    _make_slab(
        ctx, name="Roof Slab",
        xdim=MAIN_LEN, ydim=MAIN_WID, thk=ROOF_THK,
        origin=(0, 0, ROOF_BOT),
        storey=storey_roof, material=mat_travertine,
        predefined="ROOF",
    )
    # Terrace, west of main pavilion, lower elevation
    terrace_x0 = -TERRACE_LEN - 0.30
    terrace_y0 = (MAIN_WID - TERRACE_WID) / 2.0
    _make_slab(
        ctx, name="Terrace Slab",
        xdim=TERRACE_LEN, ydim=TERRACE_WID, thk=TERRACE_THK,
        origin=(terrace_x0, terrace_y0, TERRACE_TOP - TERRACE_THK),
        storey=storey_terrace, material=mat_travertine,
        predefined="FLOOR",
    )

    # ---- Columns (8) ---------------------------------------------------
    col_height = COL_TOP - COL_BASE
    long_xs = [
        COL_CANTILEVER + i * COL_BAY for i in range(N_BAYS + 1)
    ]  # 1.68, 8.39, 15.10, 21.81
    # north side y = MAIN_WID + small offset (column outboard of slab)
    north_y = MAIN_WID + COL_WIDTH / 2.0 - 0.02
    south_y = -COL_WIDTH / 2.0 + 0.02

    for i, x in enumerate(long_xs):
        for tag, y in (("N", north_y), ("S", south_y)):
            _make_column(
                ctx,
                name=f"Steel Column {tag}{i + 1}",
                origin=(x, y, COL_BASE),
                height=col_height,
                storey=storey_main,
            )

    # ---- Glass enclosure (4 walls) -------------------------------------
    glass_x0 = long_xs[0] + COL_DEPTH / 2.0
    glass_x1 = long_xs[-1] - COL_DEPTH / 2.0
    glass_len_long = glass_x1 - glass_x0  # ≈ 6.71*3 - small
    glass_len_short = MAIN_WID - 2 * 0.0   # full width

    # South wall (long)
    _make_glass_wall(
        ctx,
        name="Glass Wall — South",
        origin=(glass_x0, 0.0, MAIN_TOP),
        length=glass_len_long, height=GLASS_HEIGHT, thk=GLASS_THK,
        rotation_z=0.0,
    )
    # North wall (long)
    _make_glass_wall(
        ctx,
        name="Glass Wall — North",
        origin=(glass_x0, MAIN_WID - GLASS_THK, MAIN_TOP),
        length=glass_len_long, height=GLASS_HEIGHT, thk=GLASS_THK,
        rotation_z=0.0,
    )
    # West wall (short)
    _make_glass_wall(
        ctx,
        name="Glass Wall — West",
        origin=(glass_x0, 0.0, MAIN_TOP),
        length=glass_len_short, height=GLASS_HEIGHT, thk=GLASS_THK,
        rotation_z=math.pi / 2.0,
    )
    # East wall (short)
    _make_glass_wall(
        ctx,
        name="Glass Wall — East",
        origin=(glass_x1, 0.0, MAIN_TOP),
        length=glass_len_short, height=GLASS_HEIGHT, thk=GLASS_THK,
        rotation_z=math.pi / 2.0,
    )

    # ---- Stairs (grade -> terrace, terrace -> main) --------------------
    # Modelled as IfcStair with a single straight flight (simplified solid).
    _make_stair(
        ctx,
        name="Stair — Grade to Terrace",
        origin=(terrace_x0 - 1.80, terrace_y0 + (TERRACE_WID - 1.50) / 2.0, 0.0),
        length=1.80, width=1.50, rise=TERRACE_TOP,
        storey=storey_grade,
    )
    _make_stair(
        ctx,
        name="Stair — Terrace to Main",
        origin=(-1.80, (MAIN_WID - 1.50) / 2.0, TERRACE_TOP),
        length=1.80, width=1.50, rise=MAIN_TOP - TERRACE_TOP,
        storey=storey_terrace,
    )

    # ---- Site terrain (simple ground plate) ----------------------------
    _make_site_ground(ctx)

    # ---- Project-level Pset --------------------------------------------
    _add_pset(
        f, project, "Pset_ProjectCommon",
        {"Phase": "As-built reference model", "Reference": "IFC4 LOD 300"},
    )
    _add_pset(
        f, building, "Pset_BuildingCommon",
        {
            "BuildingID": "FARNSWORTH-1951",
            "IsLandmarked": True,
            "NumberOfStoreys": 1,
            "GrossPlannedArea": MAIN_LEN * MAIN_WID,
            "OccupancyType": "Single family residence",
        },
    )

    return f


def _make_slab(ctx, *, name, xdim, ydim, thk, origin, storey, material, predefined):
    f = ctx.f
    slab = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcSlab", name=name, predefined_type=predefined
    )
    slab.ObjectPlacement = _local_placement(f, location=origin)
    slab.Representation = _extruded_rect(f, ctx.body, xdim, ydim, thk)
    ifcopenshell.api.spatial.assign_container(
        f, products=[slab], relating_structure=storey
    )
    _assign_material(f, slab, material)
    _add_pset(
        f, slab, "Pset_SlabCommon",
        {
            "IsExternal": True,
            "LoadBearing": True,
            "Reference": name,
            "ThermalTransmittance": 0.30,
        },
    )
    return slab


def _make_column(ctx, *, name, origin, height, storey):
    f = ctx.f
    col = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcColumn", name=name, predefined_type="COLUMN"
    )
    col.ObjectPlacement = _local_placement(f, location=origin)
    col.Representation = _extruded_hsection(
        f, ctx.body, COL_DEPTH, COL_WIDTH, height
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[col], relating_structure=storey
    )
    _assign_material(f, col, ctx.mat_steel)
    _add_pset(
        f, col, "Pset_ColumnCommon",
        {
            "Reference": "W8x48",
            "LoadBearing": True,
            "IsExternal": True,
            "Slope": 0.0,
        },
    )
    return col


def _make_glass_wall(ctx, *, name, origin, length, height, thk, rotation_z):
    f = ctx.f
    wall = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcWall", name=name, predefined_type="SOLIDWALL"
    )
    rel = f.create_entity(
        "IfcAxis2Placement3D",
        Location=_pt(f, origin),
        Axis=_dir(f, (0, 0, 1)),
        RefDirection=_dir(f, (math.cos(rotation_z), math.sin(rotation_z), 0)),
    )
    wall.ObjectPlacement = f.create_entity(
        "IfcLocalPlacement", PlacementRelTo=None, RelativePlacement=rel
    )
    wall.Representation = _extruded_rect(
        f, ctx.body, length, thk, height,
        position=(length / 2.0, thk / 2.0, 0.0),
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[wall], relating_structure=ctx.storey_main
    )
    _assign_material(f, wall, ctx.mat_glass)
    _add_pset(
        f, wall, "Pset_WallCommon",
        {
            "IsExternal": True,
            "LoadBearing": False,
            "Reference": "Single-pane plate glass curtain",
            "ThermalTransmittance": 5.8,
        },
    )
    return wall


def _make_stair(ctx, *, name, origin, length, width, rise, storey):
    """Simplified stair: bounding solid wedge, with riser/run metadata."""
    f = ctx.f
    stair = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcStair", name=name, predefined_type="STRAIGHT_RUN_STAIR"
    )
    stair.ObjectPlacement = _local_placement(f, location=origin)
    # box bounding the stair flight
    stair.Representation = _extruded_rect(
        f, ctx.body, length, width, rise,
        position=(length / 2.0, width / 2.0, rise / 2.0),
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[stair], relating_structure=storey
    )
    _assign_material(f, stair, ctx.mat_travertine)
    riser_count = max(1, int(round(rise / 0.16)))
    _add_pset(
        f, stair, "Pset_StairCommon",
        {
            "Reference": name,
            "IsExternal": True,
            "NumberOfRiser": riser_count,
            "NumberOfTreads": riser_count - 1 if riser_count > 1 else 1,
            "RiserHeight": rise / riser_count,
            "TreadLength": length / max(1, riser_count - 1),
        },
    )
    return stair


def _make_site_ground(ctx):
    f = ctx.f
    geo = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcGeographicElement", name="Ground (Fox River meadow)"
    )
    origin = (-SITE_LEN / 2.0 - 5.0, -SITE_WID / 2.0 + MAIN_WID / 2.0, -0.20)
    geo.ObjectPlacement = _local_placement(f, location=origin)
    geo.Representation = _extruded_rect(f, ctx.body, SITE_LEN, SITE_WID, 0.20)
    ifcopenshell.api.spatial.assign_container(
        f, products=[geo], relating_structure=ctx.storey_grade
    )
    _assign_material(f, geo, ctx.mat_earth)
    _add_pset(
        f, geo, "Pset_SiteCommon",
        {"Reference": "Fox River floodplain meadow"},
    )
    return geo


def main():
    print("Generating Farnsworth House IFC model …")
    t0 = time.time()
    f = build()
    out = os.path.join(os.path.dirname(__file__), "farnsworth_house.ifc")
    f.write(out)
    dt = time.time() - t0
    size_kb = os.path.getsize(out) / 1024.0
    print(f"  written: {out}")
    print(f"  size:    {size_kb:,.1f} KB")
    print(f"  time:    {dt:.2f} s")

    # quick stats
    counts = {}
    for inst in f:
        if inst.is_a("IfcRoot"):
            counts[inst.is_a()] = counts.get(inst.is_a(), 0) + 1
    print("\nElement counts:")
    for k in sorted(counts):
        print(f"  {k:30s} {counts[k]}")


if __name__ == "__main__":
    main()
