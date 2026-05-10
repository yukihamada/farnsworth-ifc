"""
Farnsworth House — IFC4, LOD 350 production-grade.

Adds, on top of v1:
  1. IfcXxxType + IfcRelDefinesByType for every element class
  2. IfcCurtainWall with IfcMember (mullions/heads/sills) + IfcPlate (glass)
  3. IfcOpeningElement / IfcRelVoidsElement / IfcRelFillsElement + IfcDoor
  4. IfcGrid with U (1..4) and V (A..B) axes — Mies's structural grid
  5. IfcStairFlight with stepped geometry + riser/tread metadata + IfcRailing
  6. IfcMaterialLayerSetUsage for slabs / IfcMaterialProfileSetUsage for columns
  7. IfcRelSpaceBoundary linking pavilion space to glass walls
  8. Qto_* base quantities on every physical element
  9. Storey-relative IfcLocalPlacement for all elements
"""

from __future__ import annotations

import math
import os
import time

import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.material
import ifcopenshell.api.owner
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.style
import ifcopenshell.api.type
import ifcopenshell.api.unit
import ifcopenshell.guid

# ============================================================================
# Dimensions (metres)
# ============================================================================

MAIN_LEN = 23.470
MAIN_WID = 8.740
MAIN_FFL = 1.600
SLAB_FLOOR_THK = 0.300

ROOF_FFL = 4.750
SLAB_ROOF_THK = 0.250

TERRACE_LEN = 16.760
TERRACE_WID = 6.710
TERRACE_FFL = 0.800
SLAB_TERRACE_THK = 0.250
TERRACE_X0 = -TERRACE_LEN - 0.300
TERRACE_Y0 = (MAIN_WID - TERRACE_WID) / 2.0

# W8x48 (more accurate)
COL_DEPTH = 0.2159
COL_WIDTH = 0.2064
COL_WEB = 0.01016
COL_FLANGE = 0.01740
COL_FILLET = 0.0095
COL_BAY = 6.706
COL_CANTI = 1.676
N_BAYS = 3

GLASS_HEIGHT = 2.900
GLASS_THK = 0.025
FRAME = 0.050  # stainless angle frame (head/sill/jamb)

DOOR_WIDTH = 0.900
DOOR_HEIGHT = 2.700
DOOR_THK = 0.040

STAIR_WIDTH = 1.500
STAIR_RISER = 0.160
STAIR_TREAD = 0.280
STAIR_RISERS_GT = 5  # grade -> terrace, 5 × 160 = 800
STAIR_RISERS_TM = 5  # terrace -> main, 5 × 160 = 800

SITE_LEN = 60.0
SITE_WID = 30.0
GROUND_THK = 0.20

# Layer construction (sums to slab thickness)
LAYERS_FLOOR = [
    ("Travertine", 0.050, "stone"),
    ("Reinforced Concrete", 0.200, "concrete"),
    ("Rigid Insulation", 0.050, "insulation"),
]
LAYERS_ROOF = [
    ("Roof Membrane", 0.010, "other"),
    ("Reinforced Concrete", 0.200, "concrete"),
    ("Rigid Insulation", 0.040, "insulation"),
]
LAYERS_TERRACE = [
    ("Travertine", 0.050, "stone"),
    ("Reinforced Concrete", 0.200, "concrete"),
]


# ============================================================================
# Geometry helpers
# ============================================================================


def P(f, xyz):
    return f.create_entity("IfcCartesianPoint", Coordinates=[float(c) for c in xyz])


def D(f, xyz):
    return f.create_entity("IfcDirection", DirectionRatios=[float(c) for c in xyz])


def AX2(f, loc=(0, 0), x=(1, 0)):
    return f.create_entity(
        "IfcAxis2Placement2D",
        Location=f.create_entity("IfcCartesianPoint", Coordinates=[float(c) for c in loc]),
        RefDirection=f.create_entity("IfcDirection", DirectionRatios=[float(c) for c in x]),
    )


def AX3(f, loc=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)):
    return f.create_entity(
        "IfcAxis2Placement3D",
        Location=P(f, loc),
        Axis=D(f, z),
        RefDirection=D(f, x),
    )


def make_lp(f, location=(0, 0, 0), rotation_z=0.0, parent_placement=None):
    rel = AX3(
        f,
        loc=location,
        x=(math.cos(rotation_z), math.sin(rotation_z), 0),
    )
    return f.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=parent_placement,
        RelativePlacement=rel,
    )


def rect_profile(f, w, d, name=None):
    return f.create_entity(
        "IfcRectangleProfileDef",
        ProfileType="AREA",
        ProfileName=name,
        Position=AX2(f),
        XDim=float(w),
        YDim=float(d),
    )


def i_profile(f, depth, width, web, flange, fillet, name="W8x48"):
    return f.create_entity(
        "IfcIShapeProfileDef",
        ProfileType="AREA",
        ProfileName=name,
        Position=AX2(f),
        OverallWidth=float(width),
        OverallDepth=float(depth),
        WebThickness=float(web),
        FlangeThickness=float(flange),
        FilletRadius=float(fillet),
    )


def extrude_profile(f, body_ctx, profile, height, position=(0, 0, 0)):
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=AX3(f, loc=position),
        ExtrudedDirection=D(f, (0, 0, 1)),
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


def box_repr(f, body_ctx, w, d, h, position=(0, 0, 0)):
    """Corner-anchored extruded rectangle: footprint goes from
    `position` to `position + (w, d)`, extruded up by h.

    IfcRectangleProfileDef is centered on its position; we offset the
    profile placement by (w/2, d/2) so the element's local origin
    coincides with the box's lower-X / lower-Y corner — which is what
    the rest of this script consistently assumes.
    """
    px, py, pz = position
    profile = rect_profile(f, w, d)
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=AX3(f, loc=(px + w / 2.0, py + d / 2.0, pz)),
        ExtrudedDirection=D(f, (0, 0, 1)),
        Depth=float(h),
    )
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    return f.create_entity("IfcProductDefinitionShape", Representations=[rep])


def stair_flight_repr(f, body_ctx, n_risers, riser, tread, width):
    """Stepped side profile, extruded across width (Y)."""
    pts = [(0.0, 0.0)]
    x = 0.0
    y = 0.0
    for _ in range(n_risers):
        y += riser
        pts.append((x, y))
        x += tread
        pts.append((x, y))
    pts.append((x, 0.0))
    pts.append((0.0, 0.0))

    polyline = f.create_entity(
        "IfcPolyline",
        Points=[
            f.create_entity("IfcCartesianPoint", Coordinates=[float(px), float(py)])
            for px, py in pts
        ],
    )
    profile = f.create_entity(
        "IfcArbitraryClosedProfileDef",
        ProfileType="AREA",
        ProfileName="StairFlightSide",
        OuterCurve=polyline,
    )
    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=f.create_entity(
            "IfcAxis2Placement3D",
            Location=P(f, (0, 0, 0)),
            Axis=D(f, (0, -1, 0)),
            RefDirection=D(f, (1, 0, 0)),
        ),
        ExtrudedDirection=D(f, (0, 0, -1)),
        Depth=float(width),
    )
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=body_ctx,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )
    return f.create_entity("IfcProductDefinitionShape", Representations=[rep])


# ============================================================================
# Material & style helpers
# ============================================================================


def make_style(f, name, rgb, transparency=0.0):
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
    return f.create_entity(
        "IfcSurfaceStyle", Name=name, Side="BOTH", Styles=[rendering]
    )


def attach_style(f, material, style, body_ctx):
    pres = f.create_entity("IfcPresentationStyleAssignment", Styles=[style])
    styled = f.create_entity("IfcStyledItem", Item=None, Styles=[pres])
    rep = f.create_entity(
        "IfcStyledRepresentation",
        ContextOfItems=body_ctx,
        Items=[styled],
    )
    f.create_entity(
        "IfcMaterialDefinitionRepresentation",
        Name=material.Name,
        RepresentedMaterial=material,
        Representations=[rep],
    )


def make_layer_set(f, name, layer_specs, materials):
    """layer_specs: list of (mat_name, thickness, _)."""
    layers = []
    for mat_name, thk, _ in layer_specs:
        layers.append(
            f.create_entity(
                "IfcMaterialLayer",
                Material=materials[mat_name],
                LayerThickness=float(thk),
                IsVentilated=False,
                Name=mat_name,
            )
        )
    return f.create_entity(
        "IfcMaterialLayerSet",
        MaterialLayers=layers,
        LayerSetName=name,
    )


def assign_layer_set(f, owner_history, products, layer_set, total_thk, direction_sense="POSITIVE"):
    usage = f.create_entity(
        "IfcMaterialLayerSetUsage",
        ForLayerSet=layer_set,
        LayerSetDirection="AXIS3",
        DirectionSense=direction_sense,
        OffsetFromReferenceLine=-float(total_thk),
    )
    f.create_entity(
        "IfcRelAssociatesMaterial",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        RelatedObjects=list(products),
        RelatingMaterial=usage,
    )
    return usage


def make_profile_set(f, name, profile, material):
    mp = f.create_entity(
        "IfcMaterialProfile",
        Name=name,
        Material=material,
        Profile=profile,
        Priority=0,
        Category="STRUCTURAL",
    )
    return f.create_entity(
        "IfcMaterialProfileSet",
        Name=name,
        MaterialProfiles=[mp],
    )


def assign_profile_set(f, owner_history, products, profile_set):
    usage = f.create_entity(
        "IfcMaterialProfileSetUsage",
        ForProfileSet=profile_set,
    )
    f.create_entity(
        "IfcRelAssociatesMaterial",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        RelatedObjects=list(products),
        RelatingMaterial=usage,
    )
    return usage


def assign_material_simple(f, owner_history, products, material):
    f.create_entity(
        "IfcRelAssociatesMaterial",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        RelatedObjects=list(products),
        RelatingMaterial=material,
    )


# ============================================================================
# Quantity helpers
# ============================================================================


def add_qto(f, owner_history, element, name, quantities):
    """quantities: list of (Q-class, name, value, unit_field)."""
    qs = []
    for qcls, qname, qvalue, qunit in quantities:
        kwargs = {"Name": qname, qunit: float(qvalue)}
        qs.append(f.create_entity(qcls, **kwargs))
    qto = f.create_entity(
        "IfcElementQuantity",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        Name=name,
        Quantities=qs,
    )
    f.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        RelatedObjects=[element],
        RelatingPropertyDefinition=qto,
    )


def slab_quantities(w, d, h):
    return [
        ("IfcQuantityLength", "Length", w, "LengthValue"),
        ("IfcQuantityLength", "Width", d, "LengthValue"),
        ("IfcQuantityLength", "Depth", h, "LengthValue"),
        ("IfcQuantityArea", "GrossArea", w * d, "AreaValue"),
        ("IfcQuantityArea", "NetArea", w * d, "AreaValue"),
        ("IfcQuantityVolume", "GrossVolume", w * d * h, "VolumeValue"),
        ("IfcQuantityVolume", "NetVolume", w * d * h, "VolumeValue"),
        ("IfcQuantityLength", "Perimeter", 2 * (w + d), "LengthValue"),
    ]


def column_quantities(profile_area, height):
    return [
        ("IfcQuantityLength", "Length", height, "LengthValue"),
        ("IfcQuantityArea", "CrossSectionArea", profile_area, "AreaValue"),
        ("IfcQuantityVolume", "GrossVolume", profile_area * height, "VolumeValue"),
        ("IfcQuantityVolume", "NetVolume", profile_area * height, "VolumeValue"),
    ]


def member_quantities(w, d, h):
    return [
        ("IfcQuantityLength", "Length", max(w, h), "LengthValue"),
        ("IfcQuantityArea", "CrossSectionArea", w * d if h > w else d * h, "AreaValue"),
        ("IfcQuantityVolume", "GrossVolume", w * d * h, "VolumeValue"),
    ]


def plate_quantities(w, h, thk):
    return [
        ("IfcQuantityLength", "Width", w, "LengthValue"),
        ("IfcQuantityLength", "Height", h, "LengthValue"),
        ("IfcQuantityLength", "Thickness", thk, "LengthValue"),
        ("IfcQuantityArea", "GrossArea", w * h, "AreaValue"),
        ("IfcQuantityArea", "NetArea", w * h, "AreaValue"),
        ("IfcQuantityVolume", "GrossVolume", w * h * thk, "VolumeValue"),
    ]


def door_quantities():
    return [
        ("IfcQuantityLength", "Width", DOOR_WIDTH, "LengthValue"),
        ("IfcQuantityLength", "Height", DOOR_HEIGHT, "LengthValue"),
        ("IfcQuantityArea", "Area", DOOR_WIDTH * DOOR_HEIGHT, "AreaValue"),
        ("IfcQuantityLength", "Perimeter", 2 * (DOOR_WIDTH + DOOR_HEIGHT), "LengthValue"),
    ]


def stair_flight_quantities(n_risers, riser, tread, width):
    rise = n_risers * riser
    run = n_risers * tread
    return [
        ("IfcQuantityCount", "NumberOfRiser", n_risers, "CountValue"),
        ("IfcQuantityCount", "NumberOfTreads", n_risers - 1, "CountValue"),
        ("IfcQuantityLength", "Length", run, "LengthValue"),
        ("IfcQuantityLength", "Width", width, "LengthValue"),
        ("IfcQuantityLength", "Rise", rise, "LengthValue"),
        ("IfcQuantityArea", "GrossArea", run * width, "AreaValue"),
    ]


def stair_quantities(n_risers, riser, tread, width):
    return [
        ("IfcQuantityCount", "NumberOfRiser", n_risers, "CountValue"),
        ("IfcQuantityLength", "Length", n_risers * tread, "LengthValue"),
        ("IfcQuantityLength", "Width", width, "LengthValue"),
        ("IfcQuantityArea", "GrossArea", n_risers * tread * width, "AreaValue"),
    ]


def curtain_wall_quantities(length, height):
    return [
        ("IfcQuantityLength", "Length", length, "LengthValue"),
        ("IfcQuantityLength", "Height", height, "LengthValue"),
        ("IfcQuantityArea", "GrossSideArea", length * height, "AreaValue"),
        ("IfcQuantityArea", "NetSideArea", length * height, "AreaValue"),
    ]


def railing_quantities(length, height):
    return [
        ("IfcQuantityLength", "Length", length, "LengthValue"),
        ("IfcQuantityLength", "Height", height, "LengthValue"),
    ]


def opening_quantities(w, d, h):
    return [
        ("IfcQuantityLength", "Width", w, "LengthValue"),
        ("IfcQuantityLength", "Height", h, "LengthValue"),
        ("IfcQuantityLength", "Depth", d, "LengthValue"),
        ("IfcQuantityArea", "Area", w * h, "AreaValue"),
        ("IfcQuantityVolume", "Volume", w * d * h, "VolumeValue"),
    ]


def space_quantities(w, d, h):
    return [
        ("IfcQuantityLength", "Height", h, "LengthValue"),
        ("IfcQuantityArea", "GrossFloorArea", w * d, "AreaValue"),
        ("IfcQuantityArea", "NetFloorArea", w * d, "AreaValue"),
        ("IfcQuantityVolume", "GrossVolume", w * d * h, "VolumeValue"),
        ("IfcQuantityVolume", "NetVolume", w * d * h, "VolumeValue"),
        ("IfcQuantityLength", "GrossPerimeter", 2 * (w + d), "LengthValue"),
    ]


# ============================================================================
# Property set helpers
# ============================================================================


def add_pset(f, element, name, props):
    pset = ifcopenshell.api.pset.add_pset(f, product=element, name=name)
    ifcopenshell.api.pset.edit_pset(f, pset=pset, properties=props)


# ============================================================================
# Build
# ============================================================================


def build():
    f = ifcopenshell.api.project.create_file(version="IFC4")

    # ---- Owner / application ----
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
        version="0.2",
        application_full_name="Farnsworth IFC Generator v2",
        application_identifier="ENABLER-FARNSWORTH-V2",
    )
    owner_history = ifcopenshell.api.owner.create_owner_history(f)

    # ---- Project ----
    project = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcProject", name="Farnsworth House"
    )
    project.LongName = "Farnsworth House — Mies van der Rohe, 1951"
    project.Phase = "As-built reference (LOD 350)"

    ifcopenshell.api.unit.assign_unit(
        f,
        length={"is_metric": True, "raw": "METERS"},
        area={"is_metric": True, "raw": "METERS"},
        volume={"is_metric": True, "raw": "METERS"},
    )

    # ---- Geometric contexts ----
    model_ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        f, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=model_ctx,
    )
    plan_top = ifcopenshell.api.context.add_context(f, context_type="Plan")
    plan_fp = ifcopenshell.api.context.add_context(
        f, context_type="Plan", context_identifier="FootPrint",
        target_view="PLAN_VIEW", parent=plan_top,
    )
    annot = ifcopenshell.api.context.add_context(
        f, context_type="Plan", context_identifier="Annotation",
        target_view="PLAN_VIEW", parent=plan_top,
    )

    # ---- Spatial hierarchy + storey-relative placements ----
    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="Plano, Illinois")
    site.LongName = "Fox River, Plano, Illinois, USA"
    site.CompositionType = "ELEMENT"
    site.RefLatitude = (41, 39, 49)
    site.RefLongitude = (-88, -32, -10)
    site.RefElevation = 173.0
    site_lp = make_lp(f, location=(0, 0, 0))
    site.ObjectPlacement = site_lp

    building = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcBuilding", name="Farnsworth House"
    )
    building.CompositionType = "ELEMENT"
    building_lp = make_lp(f, location=(0, 0, 0), parent_placement=site_lp)
    building.ObjectPlacement = building_lp

    storeys = {}
    for name, elev in [
        ("Grade", 0.0),
        ("Terrace Level", TERRACE_FFL),
        ("Main Floor", MAIN_FFL),
        ("Roof", ROOF_FFL),
    ]:
        s = ifcopenshell.api.root.create_entity(
            f, ifc_class="IfcBuildingStorey", name=name
        )
        s.Elevation = float(elev)
        s.CompositionType = "ELEMENT"
        s.ObjectPlacement = make_lp(f, location=(0, 0, elev), parent_placement=building_lp)
        storeys[name] = s

    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=project)
    ifcopenshell.api.aggregate.assign_object(f, products=[building], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(
        f, products=list(storeys.values()), relating_object=building
    )

    # ---- Materials + styles ----
    materials = {}
    for n, cat in [
        ("Travertine", "stone"),
        ("Reinforced Concrete", "concrete"),
        ("Structural Steel - White", "steel"),
        ("Float Glass 25mm", "glass"),
        ("Stainless Steel", "steel"),
        ("Roof Membrane", "other"),
        ("Rigid Insulation", "insulation"),
        ("Topsoil/Meadow", "soil"),
        ("White Oak", "wood"),
        ("Primavera Wood Veneer", "wood"),  # iconic blonde tropical hardwood
        ("Hearth Stone (Roman travertine)", "stone"),
        ("Black Leather (cowhide)", "fabric"),
        ("White Italian Marble", "stone"),
        ("Linen Cream", "fabric"),
        ("Vitreous Ceramic", "ceramic"),
        ("Brushed Stainless 304", "steel"),
        ("Foliage", "other"),
        ("Tree Trunk Bark", "wood"),
        ("Shantung Silk (cream)", "fabric"),
        ("Brass", "metal"),
        ("Fox River Water", "water"),
        ("Crushed Limestone Gravel", "stone"),
        ("Bluestone Path", "stone"),
        ("Travertine Floor Finish", "stone"),
        ("Plaster Ceiling", "other"),
    ]:
        materials[n] = ifcopenshell.api.material.add_material(f, name=n, category=cat)

    style_map = {
        "Travertine": ((0.86, 0.81, 0.72), 0.0),
        "Reinforced Concrete": ((0.78, 0.76, 0.72), 0.0),
        "Structural Steel - White": ((0.95, 0.95, 0.93), 0.0),
        "Float Glass 25mm": ((0.65, 0.78, 0.85), 0.7),
        "Stainless Steel": ((0.82, 0.83, 0.85), 0.0),
        "Roof Membrane": ((0.30, 0.30, 0.32), 0.0),
        "Rigid Insulation": ((1.0, 0.95, 0.62), 0.0),
        "Topsoil/Meadow": ((0.45, 0.55, 0.30), 0.0),
        "White Oak": ((0.78, 0.62, 0.38), 0.0),
        "Primavera Wood Veneer": ((0.85, 0.66, 0.42), 0.0),
        "Hearth Stone (Roman travertine)": ((0.62, 0.58, 0.50), 0.0),
        "Black Leather (cowhide)": ((0.10, 0.10, 0.10), 0.0),
        "White Italian Marble": ((0.93, 0.91, 0.86), 0.0),
        "Linen Cream": ((0.91, 0.86, 0.78), 0.0),
        "Vitreous Ceramic": ((0.97, 0.97, 0.96), 0.0),
        "Brushed Stainless 304": ((0.78, 0.79, 0.81), 0.0),
        "Foliage": ((0.30, 0.50, 0.25), 0.2),
        "Tree Trunk Bark": ((0.36, 0.28, 0.20), 0.0),
        "Shantung Silk (cream)": ((0.93, 0.88, 0.76), 0.25),
        "Brass": ((0.78, 0.62, 0.30), 0.0),
        "Fox River Water": ((0.30, 0.45, 0.55), 0.4),
        "Crushed Limestone Gravel": ((0.78, 0.74, 0.66), 0.0),
        "Bluestone Path": ((0.55, 0.58, 0.60), 0.0),
        "Travertine Floor Finish": ((0.86, 0.81, 0.72), 0.0),
        "Plaster Ceiling": ((0.96, 0.95, 0.92), 0.0),
    }
    for n, (rgb, t) in style_map.items():
        attach_style(f, materials[n], make_style(f, n, rgb, t), body)

    # Layer sets
    ls_floor = make_layer_set(f, "FLOOR-300", LAYERS_FLOOR, materials)
    ls_roof = make_layer_set(f, "ROOF-250", LAYERS_ROOF, materials)
    ls_terrace = make_layer_set(f, "TERRACE-250", LAYERS_TERRACE, materials)

    # Profile set for column (W8x48 steel)
    col_profile = i_profile(
        f, COL_DEPTH, COL_WIDTH, COL_WEB, COL_FLANGE, COL_FILLET, "W8x48"
    )
    col_profile_set = make_profile_set(
        f, "W8x48-STEEL", col_profile, materials["Structural Steel - White"]
    )

    # ---- Type definitions ----
    types = {}

    def make_type(cls_type, name, predefined=None, applicable_occurrence=None):
        t = ifcopenshell.api.root.create_entity(
            f, ifc_class=cls_type, name=name,
            predefined_type=predefined,
        )
        if applicable_occurrence:
            t.ApplicableOccurrence = applicable_occurrence
        return t

    types["slab_floor"] = make_type("IfcSlabType", "FLOOR-300", "FLOOR")
    types["slab_roof"] = make_type("IfcSlabType", "ROOF-250", "ROOF")
    types["slab_terrace"] = make_type("IfcSlabType", "TERRACE-250", "FLOOR")
    types["slab_ground"] = make_type("IfcSlabType", "GROUND-200", "BASESLAB")
    types["column"] = make_type("IfcColumnType", "W8x48-WHITE", "COLUMN")
    types["curtain_wall"] = make_type("IfcCurtainWallType", "GLASS-CW-2900", "NOTDEFINED")
    types["member_mullion"] = make_type("IfcMemberType", "SS-MULLION-50", "MULLION")
    types["plate_glass"] = make_type("IfcPlateType", "GLASS-PLATE-25", "CURTAIN_PANEL")
    types["door"] = make_type("IfcDoorType", "PIVOT-DOOR-900", "DOOR")
    types["stair"] = make_type("IfcStairType", "STAIR-FLOATING", "STRAIGHT_RUN_STAIR")
    types["stair_flight"] = make_type(
        "IfcStairFlightType", "FLIGHT-160-280", "STRAIGHT"
    )
    types["railing"] = make_type("IfcRailingType", "STAINLESS-RAILING", "GUARDRAIL")
    types["space"] = make_type("IfcSpaceType", "PAVILION-LIVING", "SPACE")
    # LOD 400 — interior types
    types["furn_core"] = make_type("IfcFurnishingElementType", "PRIMAVERA-CORE", None)
    types["furn_chair"] = make_type("IfcFurnishingElementType", "MIES-BARCELONA-CHAIR", None)
    types["furn_diningchair"] = make_type("IfcFurnishingElementType", "MIES-BRNO-CHAIR", None)
    types["furn_dining_table"] = make_type("IfcFurnishingElementType", "MIES-DINING-TABLE", None)
    types["furn_coffee_table"] = make_type("IfcFurnishingElementType", "MIES-COFFEE-TABLE", None)
    types["furn_bed"] = make_type("IfcFurnishingElementType", "BED-DAYBED", None)
    types["furn_wardrobe"] = make_type("IfcFurnishingElementType", "WARDROBE", None)
    types["furn_kitchen"] = make_type("IfcFurnishingElementType", "KITCHEN-COUNTER", None)
    types["furn_appl"] = make_type("IfcFurnishingElementType", "STAINLESS-APPLIANCE", None)
    types["furn_fireplace"] = make_type("IfcFurnishingElementType", "FIREPLACE-HEARTH", None)
    types["sani_toilet"] = make_type("IfcSanitaryTerminalType", "WC-WALL-HUNG", "TOILETPAN")
    types["sani_sink"] = make_type("IfcSanitaryTerminalType", "WALL-HUNG-SINK", "WASHHANDBASIN")
    types["sani_shower"] = make_type("IfcSanitaryTerminalType", "SHOWER-STALL", "SHOWER")
    types["sani_kitchen_sink"] = make_type("IfcSanitaryTerminalType", "KITCHEN-SINK", "SINK")
    types["geo_tree"] = make_type("IfcGeographicElementType", "TREE-DECIDUOUS", None)
    # New LOD-400/500 types
    types["covering_floor"] = make_type("IfcCoveringType", "TRAVERTINE-FLOORING-50", "FLOORING")
    types["covering_ceiling"] = make_type("IfcCoveringType", "PLASTER-CEILING-15", "CEILING")
    types["covering_curtain"] = make_type("IfcCoveringType", "SHANTUNG-CURTAIN", "MOLDING")
    types["light_floor"] = make_type("IfcLightFixtureType", "FLOOR-LAMP-MIES", "DIRECTIONSOURCE")
    types["light_table"] = make_type("IfcLightFixtureType", "TABLE-LAMP", "POINTSOURCE")
    types["light_ceiling"] = make_type("IfcLightFixtureType", "CEILING-RECESSED", "DIRECTIONSOURCE")
    types["furn_sideboard"] = make_type("IfcFurnishingElementType", "MIES-SIDEBOARD", None)
    types["furn_bookshelf"] = make_type("IfcFurnishingElementType", "BOOKSHELF-PRIMAVERA", None)
    types["geo_water"] = make_type("IfcGeographicElementType", "WATER-BODY", None)
    types["geo_path"] = make_type("IfcGeographicElementType", "STONE-PATH", None)
    types["geo_drive"] = make_type("IfcGeographicElementType", "GRAVEL-DRIVE", None)
    types["window_stop"] = make_type("IfcMemberType", "WINDOW-STOP-ANGLE", "MULLION")

    # Type → material associations
    assign_layer_set(
        f, owner_history, [types["slab_floor"]], ls_floor, SLAB_FLOOR_THK,
    )
    assign_layer_set(
        f, owner_history, [types["slab_roof"]], ls_roof, SLAB_ROOF_THK,
    )
    assign_layer_set(
        f, owner_history, [types["slab_terrace"]], ls_terrace, SLAB_TERRACE_THK,
    )
    assign_material_simple(
        f, owner_history, [types["slab_ground"]], materials["Topsoil/Meadow"],
    )
    assign_profile_set(f, owner_history, [types["column"]], col_profile_set)
    assign_material_simple(
        f, owner_history, [types["member_mullion"]], materials["Stainless Steel"],
    )
    assign_material_simple(
        f, owner_history, [types["plate_glass"]], materials["Float Glass 25mm"],
    )
    assign_material_simple(
        f, owner_history, [types["door"]], materials["Stainless Steel"],
    )
    assign_material_simple(
        f, owner_history, [types["stair_flight"]], materials["Travertine"],
    )
    assign_material_simple(
        f, owner_history, [types["railing"]], materials["Stainless Steel"],
    )
    # Interior type → material
    assign_material_simple(f, owner_history, [types["furn_core"]], materials["Primavera Wood Veneer"])
    assign_material_simple(f, owner_history, [types["furn_chair"]], materials["Black Leather (cowhide)"])
    assign_material_simple(f, owner_history, [types["furn_diningchair"]], materials["Black Leather (cowhide)"])
    assign_material_simple(f, owner_history, [types["furn_dining_table"]], materials["White Italian Marble"])
    assign_material_simple(f, owner_history, [types["furn_coffee_table"]], materials["Float Glass 25mm"])
    assign_material_simple(f, owner_history, [types["furn_bed"]], materials["Linen Cream"])
    assign_material_simple(f, owner_history, [types["furn_wardrobe"]], materials["Primavera Wood Veneer"])
    assign_material_simple(f, owner_history, [types["furn_kitchen"]], materials["Brushed Stainless 304"])
    assign_material_simple(f, owner_history, [types["furn_appl"]], materials["Brushed Stainless 304"])
    assign_material_simple(f, owner_history, [types["furn_fireplace"]], materials["Hearth Stone (Roman travertine)"])
    assign_material_simple(f, owner_history, [types["sani_toilet"]], materials["Vitreous Ceramic"])
    assign_material_simple(f, owner_history, [types["sani_sink"]], materials["Vitreous Ceramic"])
    assign_material_simple(f, owner_history, [types["sani_shower"]], materials["Float Glass 25mm"])
    assign_material_simple(f, owner_history, [types["sani_kitchen_sink"]], materials["Brushed Stainless 304"])
    assign_material_simple(f, owner_history, [types["geo_tree"]], materials["Foliage"])
    assign_material_simple(f, owner_history, [types["covering_floor"]], materials["Travertine Floor Finish"])
    assign_material_simple(f, owner_history, [types["covering_ceiling"]], materials["Plaster Ceiling"])
    assign_material_simple(f, owner_history, [types["covering_curtain"]], materials["Shantung Silk (cream)"])
    assign_material_simple(f, owner_history, [types["light_floor"]], materials["Brass"])
    assign_material_simple(f, owner_history, [types["light_table"]], materials["Brass"])
    assign_material_simple(f, owner_history, [types["light_ceiling"]], materials["Stainless Steel"])
    assign_material_simple(f, owner_history, [types["furn_sideboard"]], materials["Primavera Wood Veneer"])
    assign_material_simple(f, owner_history, [types["furn_bookshelf"]], materials["Primavera Wood Veneer"])
    assign_material_simple(f, owner_history, [types["geo_water"]], materials["Fox River Water"])
    assign_material_simple(f, owner_history, [types["geo_path"]], materials["Bluestone Path"])
    assign_material_simple(f, owner_history, [types["geo_drive"]], materials["Crushed Limestone Gravel"])
    assign_material_simple(f, owner_history, [types["window_stop"]], materials["Stainless Steel"])

    # ---- Structural grid (Mies's iconic 4×2 grid) ----
    long_xs = [COL_CANTI + i * COL_BAY for i in range(4)]      # 1.68, 8.39, 15.10, 21.81
    transverse_ys = [-COL_DEPTH / 2.0 + 0.020, MAIN_WID + COL_DEPTH / 2.0 - 0.020]
    grid = make_grid(f, owner_history, plan_fp, building, long_xs, transverse_ys)

    # ---- Site ground ----
    ground = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcSlab", name="Site Ground (meadow)",
        predefined_type="BASESLAB",
    )
    ground.ObjectPlacement = make_lp(
        f, location=(-SITE_LEN / 2.0 - 5.0, -SITE_WID / 2.0 + MAIN_WID / 2.0, -GROUND_THK),
        parent_placement=storeys["Grade"].ObjectPlacement,
    )
    ground.Representation = box_repr(f, body, SITE_LEN, SITE_WID, GROUND_THK)
    ifcopenshell.api.spatial.assign_container(
        f, products=[ground], relating_structure=storeys["Grade"]
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[ground], relating_type=types["slab_ground"]
    )
    add_qto(f, owner_history, ground, "Qto_SlabBaseQuantities",
            slab_quantities(SITE_LEN, SITE_WID, GROUND_THK))
    add_pset(f, ground, "Pset_SlabCommon", {
        "IsExternal": True, "LoadBearing": False,
        "Reference": "Site terrain"})

    # ---- Slabs (terrace, main, roof) — storey-relative, type-bound, layered ----
    terrace_slab = make_slab(
        f, body, owner_history, storeys["Terrace Level"], types["slab_terrace"],
        name="Terrace Slab",
        local_xyz=(TERRACE_X0, TERRACE_Y0, -SLAB_TERRACE_THK),
        size=(TERRACE_LEN, TERRACE_WID, SLAB_TERRACE_THK),
    )
    main_slab = make_slab(
        f, body, owner_history, storeys["Main Floor"], types["slab_floor"],
        name="Main Floor Slab",
        local_xyz=(0, 0, -SLAB_FLOOR_THK),
        size=(MAIN_LEN, MAIN_WID, SLAB_FLOOR_THK),
    )
    roof_slab = make_slab(
        f, body, owner_history, storeys["Roof"], types["slab_roof"],
        name="Roof Slab",
        local_xyz=(0, 0, -SLAB_ROOF_THK),
        size=(MAIN_LEN, MAIN_WID, SLAB_ROOF_THK),
    )

    # ---- Columns (8) — typed, profile-set, profile-extruded ----
    col_height = ROOF_FFL - 0.0
    col_profile_area = (
        2 * (COL_WIDTH * COL_FLANGE) +
        ((COL_DEPTH - 2 * COL_FLANGE) * COL_WEB)
    )
    columns = []
    for i, cx in enumerate(long_xs):
        for tag, cy in (("S", -COL_DEPTH / 2.0 + 0.020),
                        ("N", MAIN_WID + COL_DEPTH / 2.0 - 0.020)):
            col = ifcopenshell.api.root.create_entity(
                f, ifc_class="IfcColumn", name=f"Column {tag}{i + 1}",
                predefined_type="COLUMN",
            )
            col.ObjectPlacement = make_lp(
                f, location=(cx, cy, 0),
                parent_placement=storeys["Grade"].ObjectPlacement,
            )
            col.Representation = extrude_profile(
                f, body, i_profile(f, COL_DEPTH, COL_WIDTH, COL_WEB, COL_FLANGE, COL_FILLET),
                col_height,
            )
            ifcopenshell.api.spatial.assign_container(
                f, products=[col], relating_structure=storeys["Grade"]
            )
            ifcopenshell.api.type.assign_type(
                f, related_objects=[col], relating_type=types["column"]
            )
            add_qto(
                f, owner_history, col, "Qto_ColumnBaseQuantities",
                column_quantities(col_profile_area, col_height),
            )
            add_pset(f, col, "Pset_ColumnCommon", {
                "Reference": "W8x48 ASTM A36",
                "LoadBearing": True, "IsExternal": True, "Slope": 0.0,
            })
            columns.append(col)

    # ---- Curtain walls (4 sides) with members + plates ----
    glass_x0 = long_xs[0] + COL_DEPTH / 2.0
    glass_x1 = long_xs[-1] - COL_DEPTH / 2.0
    glass_long = glass_x1 - glass_x0  # ≈ 19.92 m

    # 4 jambs on long sides: 2 at outer edges (against column inner flanges)
    # + 2 inner jambs centered on the middle columns.
    long_jamb_xs = [
        0.0,
        (long_xs[1] - glass_x0) - FRAME / 2.0,
        (long_xs[2] - glass_x0) - FRAME / 2.0,
        glass_long - FRAME,
    ]
    south_cw = make_curtain_wall_long(
        f, body, owner_history, storeys["Main Floor"], types,
        name="Curtain Wall — South",
        origin_local=(glass_x0, 0, 0),
        length=glass_long, height=GLASS_HEIGHT, thk=GLASS_THK,
        jamb_xs=long_jamb_xs,
        rotation_z=0.0,
    )
    north_cw = make_curtain_wall_long(
        f, body, owner_history, storeys["Main Floor"], types,
        name="Curtain Wall — North",
        origin_local=(glass_x0, MAIN_WID - GLASS_THK, 0),
        length=glass_long, height=GLASS_HEIGHT, thk=GLASS_THK,
        jamb_xs=long_jamb_xs,
        rotation_z=0.0,
    )
    west_cw = make_curtain_wall_short(
        f, body, owner_history, storeys["Main Floor"], types,
        name="Curtain Wall — West",
        origin_local=(glass_x0, 0, 0),
        length=MAIN_WID, height=GLASS_HEIGHT, thk=GLASS_THK,
        rotation_z=math.pi / 2.0,
    )
    east_cw = make_curtain_wall_short(
        f, body, owner_history, storeys["Main Floor"], types,
        name="Curtain Wall — East",
        origin_local=(glass_x1 - GLASS_THK, 0, 0),
        length=MAIN_WID, height=GLASS_HEIGHT, thk=GLASS_THK,
        rotation_z=math.pi / 2.0,
    )

    # ---- Door on south curtain wall middle bay ----
    door, door_opening = make_door(
        f, body, owner_history, storeys["Main Floor"], types,
        host_curtain_wall=south_cw["wall"],
        host_panel=south_cw["panels"][1],  # middle panel
    )

    # ---- Stairs (with flights, railings) ----
    grade_terrace_stair = make_stair(
        f, body, owner_history, storeys["Grade"], types,
        name="Stair — Grade to Terrace",
        local_xyz=(TERRACE_X0 - STAIR_RISERS_GT * STAIR_TREAD,
                   TERRACE_Y0 + (TERRACE_WID - STAIR_WIDTH) / 2.0,
                   0),
        n_risers=STAIR_RISERS_GT,
    )
    terrace_main_stair = make_stair(
        f, body, owner_history, storeys["Terrace Level"], types,
        name="Stair — Terrace to Main",
        local_xyz=(-STAIR_RISERS_TM * STAIR_TREAD,
                   (MAIN_WID - STAIR_WIDTH) / 2.0,
                   0),
        n_risers=STAIR_RISERS_TM,
    )

    # ---- LOD 400 interior: primavera core, kitchen, fireplace, furniture ----
    interior_elements = []
    interior_elements += _make_interior(
        f, body, owner_history, storeys["Main Floor"], types,
    )
    # Floor & ceiling finishes
    interior_elements += _make_finishes(
        f, body, owner_history, storeys, types,
    )
    # Lighting
    interior_elements += _make_lighting(
        f, body, owner_history, storeys["Main Floor"], types,
    )
    # Sideboard, bookshelf, console
    interior_elements += _make_more_furniture(
        f, body, owner_history, storeys["Main Floor"], types,
    )
    # Shantung silk curtains in west bay
    interior_elements += _make_curtains(
        f, body, owner_history, storeys["Main Floor"], types,
        glass_x0=glass_x0, glass_long=glass_long,
    )
    # Steel angle stops at column inner faces (LOD 500 hardware)
    interior_elements += _make_window_stops(
        f, body, owner_history, storeys["Main Floor"], types, long_xs,
    )

    # ---- Site context: trees + landscape ----
    interior_elements += _make_trees(
        f, body, owner_history, storeys["Grade"], types,
    )
    interior_elements += _make_landscape(
        f, body, owner_history, storeys["Grade"], types,
    )

    # ---- Pavilion space ----
    space = make_space(
        f, body, owner_history, storeys["Main Floor"], types["space"],
        local_xyz=(glass_x0 + GLASS_THK, GLASS_THK, 0),
        size=(glass_long - 2 * GLASS_THK,
              MAIN_WID - 2 * GLASS_THK,
              GLASS_HEIGHT),
    )

    # ---- Space boundaries ----
    for cw_dict, side in [
        (south_cw, "south"), (north_cw, "north"),
        (west_cw, "west"), (east_cw, "east"),
    ]:
        make_space_boundary(
            f, owner_history, space, cw_dict["wall"], side
        )
    # Floor & ceiling boundaries
    make_space_boundary(f, owner_history, space, main_slab, "floor",
                        internal=True)
    make_space_boundary(f, owner_history, space, roof_slab, "ceiling",
                        internal=True)

    # ---- Project metadata Pset ----
    add_pset(f, project, "Pset_ProjectCommon", {
        "Phase": "As-built reference",
        "Reference": "IFC4 LOD 350 — full BIM",
    })
    add_pset(f, building, "Pset_BuildingCommon", {
        "BuildingID": "FARNSWORTH-1951",
        "IsLandmarked": True,
        "NumberOfStoreys": 1,
        "GrossPlannedArea": MAIN_LEN * MAIN_WID,
        "OccupancyType": "Single family residence",
    })

    return f


# ============================================================================
# Sub-builders
# ============================================================================


def make_slab(f, body, owner_history, storey, slab_type, *, name, local_xyz, size):
    w, d, h = size
    slab = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcSlab", name=name,
        predefined_type=slab_type.PredefinedType,
    )
    slab.ObjectPlacement = make_lp(
        f, location=local_xyz, parent_placement=storey.ObjectPlacement
    )
    slab.Representation = box_repr(f, body, w, d, h)
    ifcopenshell.api.spatial.assign_container(
        f, products=[slab], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[slab], relating_type=slab_type
    )
    add_qto(f, owner_history, slab, "Qto_SlabBaseQuantities",
            slab_quantities(w, d, h))
    add_pset(f, slab, "Pset_SlabCommon", {
        "IsExternal": True, "LoadBearing": True,
        "Reference": name, "ThermalTransmittance": 0.30,
    })
    return slab


def make_grid(f, owner_history, plan_ctx, building, long_xs, transverse_ys):
    def pt2d(xy):
        return f.create_entity(
            "IfcCartesianPoint",
            Coordinates=[float(xy[0]), float(xy[1])],
        )

    u_axes = []
    for i, x in enumerate(long_xs):
        line = f.create_entity(
            "IfcPolyline",
            Points=[
                pt2d((x, -2.0)),
                pt2d((x, MAIN_WID + 2.0)),
            ],
        )
        u_axes.append(f.create_entity(
            "IfcGridAxis",
            AxisTag=str(i + 1),
            AxisCurve=line,
            SameSense=True,
        ))
    v_axes = []
    for i, y in enumerate(transverse_ys):
        tag = chr(ord("A") + i)
        line = f.create_entity(
            "IfcPolyline",
            Points=[
                pt2d((-2.0, y)),
                pt2d((MAIN_LEN + 2.0, y)),
            ],
        )
        v_axes.append(f.create_entity(
            "IfcGridAxis",
            AxisTag=tag,
            AxisCurve=line,
            SameSense=True,
        ))

    grid_lp = make_lp(f, location=(0, 0, 0), parent_placement=building.ObjectPlacement)

    all_curves = [a.AxisCurve for a in u_axes + v_axes]
    geom_set = f.create_entity("IfcGeometricCurveSet", Elements=all_curves)
    rep = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=plan_ctx,
        RepresentationIdentifier="FootPrint",
        RepresentationType="GeometricCurveSet",
        Items=[geom_set],
    )
    grid_repr = f.create_entity(
        "IfcProductDefinitionShape", Representations=[rep]
    )

    grid = f.create_entity(
        "IfcGrid",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        Name="Structural Grid",
        Description="Mies's grid: 3 bays × 1 bay (22ft × 28'8\")",
        ObjectPlacement=grid_lp,
        Representation=grid_repr,
        UAxes=u_axes,
        VAxes=v_axes,
        WAxes=None,
        PredefinedType="RECTANGULAR",
    )

    ifcopenshell.api.spatial.assign_container(
        f, products=[grid], relating_structure=building
    )
    add_pset(f, grid, "Pset_GridCommon", {
        "Reference": "Structural grid 1-4 / A-B",
        "IsExternal": False,
    })
    return grid


def _add_member(f, body, owner_history, storey, type_member, *,
                name, parent_placement, location, size, predefined="MULLION"):
    w, d, h = size
    m = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcMember", name=name, predefined_type=predefined,
    )
    m.ObjectPlacement = make_lp(
        f, location=location, parent_placement=parent_placement,
    )
    m.Representation = box_repr(f, body, w, d, h)
    ifcopenshell.api.spatial.assign_container(
        f, products=[m], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[m], relating_type=type_member
    )
    add_qto(f, owner_history, m, "Qto_MemberBaseQuantities",
            member_quantities(w, d, h))
    add_pset(f, m, "Pset_MemberCommon", {
        "Reference": "SS angle 50×50",
        "LoadBearing": False, "IsExternal": True,
    })
    return m


def _add_plate(f, body, owner_history, storey, type_plate, *,
               name, parent_placement, location, size):
    w, d, h = size
    p = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcPlate", name=name, predefined_type="CURTAIN_PANEL",
    )
    p.ObjectPlacement = make_lp(
        f, location=location, parent_placement=parent_placement,
    )
    p.Representation = box_repr(f, body, w, d, h)
    ifcopenshell.api.spatial.assign_container(
        f, products=[p], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[p], relating_type=type_plate
    )
    add_qto(f, owner_history, p, "Qto_PlateBaseQuantities",
            plate_quantities(w, h, d))
    add_pset(f, p, "Pset_PlateCommon", {
        "Reference": "Single-pane plate glass 25mm",
        "LoadBearing": False, "IsExternal": True,
        "ThermalTransmittance": 5.8,
    })
    return p


def make_curtain_wall_long(f, body, owner_history, storey, types, *,
                           name, origin_local, length, height, thk,
                           jamb_xs, rotation_z):
    """Long-side curtain wall: jambs at given corner positions,
    plates between adjacent jambs."""
    cw = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcCurtainWall", name=name, predefined_type="NOTDEFINED",
    )
    cw.ObjectPlacement = make_lp(
        f, location=origin_local, rotation_z=rotation_z,
        parent_placement=storey.ObjectPlacement,
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[cw], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[cw], relating_type=types["curtain_wall"]
    )

    parts = []

    # Sill (full length, FRAME tall) at z=0
    parts.append(_add_member(
        f, body, owner_history, storey, types["member_mullion"],
        name=f"{name} Sill",
        parent_placement=cw.ObjectPlacement,
        location=(0, 0, 0),
        size=(length, thk, FRAME),
    ))
    # Head at top
    parts.append(_add_member(
        f, body, owner_history, storey, types["member_mullion"],
        name=f"{name} Head",
        parent_placement=cw.ObjectPlacement,
        location=(0, 0, height - FRAME),
        size=(length, thk, FRAME),
    ))

    # Vertical jambs at given corner positions
    for i, jx in enumerate(jamb_xs):
        parts.append(_add_member(
            f, body, owner_history, storey, types["member_mullion"],
            name=f"{name} Jamb {i + 1}",
            parent_placement=cw.ObjectPlacement,
            location=(jx, 0, FRAME),
            size=(FRAME, thk, height - 2 * FRAME),
        ))

    # Glass plates between adjacent jambs
    panels = []
    for i in range(len(jamb_xs) - 1):
        x0 = jamb_xs[i] + FRAME
        x1 = jamb_xs[i + 1]
        if x1 <= x0:
            continue
        panels.append(_add_plate(
            f, body, owner_history, storey, types["plate_glass"],
            name=f"{name} Glass Panel {i + 1}",
            parent_placement=cw.ObjectPlacement,
            location=(x0, 0, FRAME),
            size=(x1 - x0, thk, height - 2 * FRAME),
        ))
    parts.extend(panels)

    ifcopenshell.api.aggregate.assign_object(
        f, products=parts, relating_object=cw
    )
    add_pset(f, cw, "Pset_CurtainWallCommon", {
        "Reference": "GLASS-CW-2900",
        "IsExternal": True,
        "LoadBearing": False,
        "ThermalTransmittance": 5.8,
    })
    add_qto(f, owner_history, cw, "Qto_WallBaseQuantities",
            curtain_wall_quantities(length, height))
    return {"wall": cw, "members": [p for p in parts if p.is_a("IfcMember")],
            "panels": panels}


def make_curtain_wall_short(f, body, owner_history, storey, types, *,
                            name, origin_local, length, height, thk, rotation_z):
    """Short-side curtain wall: 1 panel between 2 jambs."""
    cw = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcCurtainWall", name=name, predefined_type="NOTDEFINED",
    )
    cw.ObjectPlacement = make_lp(
        f, location=origin_local, rotation_z=rotation_z,
        parent_placement=storey.ObjectPlacement,
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[cw], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[cw], relating_type=types["curtain_wall"]
    )

    parts = []
    parts.append(_add_member(
        f, body, owner_history, storey, types["member_mullion"],
        name=f"{name} Sill",
        parent_placement=cw.ObjectPlacement,
        location=(0, 0, 0),
        size=(length, thk, FRAME),
    ))
    parts.append(_add_member(
        f, body, owner_history, storey, types["member_mullion"],
        name=f"{name} Head",
        parent_placement=cw.ObjectPlacement,
        location=(0, 0, height - FRAME),
        size=(length, thk, FRAME),
    ))
    parts.append(_add_member(
        f, body, owner_history, storey, types["member_mullion"],
        name=f"{name} Jamb L",
        parent_placement=cw.ObjectPlacement,
        location=(0, 0, FRAME),
        size=(FRAME, thk, height - 2 * FRAME),
    ))
    parts.append(_add_member(
        f, body, owner_history, storey, types["member_mullion"],
        name=f"{name} Jamb R",
        parent_placement=cw.ObjectPlacement,
        location=(length - FRAME, 0, FRAME),
        size=(FRAME, thk, height - 2 * FRAME),
    ))
    panel = _add_plate(
        f, body, owner_history, storey, types["plate_glass"],
        name=f"{name} Glass Panel",
        parent_placement=cw.ObjectPlacement,
        location=(FRAME, 0, FRAME),
        size=(length - 2 * FRAME, thk, height - 2 * FRAME),
    )
    parts.append(panel)
    ifcopenshell.api.aggregate.assign_object(
        f, products=parts, relating_object=cw
    )
    add_pset(f, cw, "Pset_CurtainWallCommon", {
        "Reference": "GLASS-CW-2900",
        "IsExternal": True,
        "LoadBearing": False,
    })
    add_qto(f, owner_history, cw, "Qto_WallBaseQuantities",
            curtain_wall_quantities(length, height))
    return {"wall": cw, "members": parts[:-1], "panels": [panel]}


def make_door(f, body, owner_history, storey, types, *, host_curtain_wall, host_panel):
    """Pivot door on south curtain wall, voids the middle glass panel."""
    door = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcDoor", name="South Pivot Door",
        predefined_type="DOOR",
    )
    door.OverallHeight = float(DOOR_HEIGHT)
    door.OverallWidth = float(DOOR_WIDTH)

    # Place door at center of host panel (horizontally), at floor level (z=0 main storey)
    panel_lp = host_panel.ObjectPlacement
    # We'll place door relative to the same parent as the panel (curtain wall)
    cw_lp = host_curtain_wall.ObjectPlacement

    # Compute panel midpoint: panel placement is at (panel_x, 0, FRAME) relative to CW
    panel_x = host_panel.ObjectPlacement.RelativePlacement.Location.Coordinates[0]
    panel_w = host_panel.Representation.Representations[0].Items[0].SweptArea.XDim
    door_x = panel_x + (panel_w - DOOR_WIDTH) / 2.0

    door.ObjectPlacement = make_lp(
        f, location=(door_x, -DOOR_THK / 2.0, 0.0),
        parent_placement=cw_lp,
    )
    door.Representation = box_repr(
        f, body, DOOR_WIDTH, DOOR_THK, DOOR_HEIGHT,
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[door], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[door], relating_type=types["door"]
    )
    add_qto(f, owner_history, door, "Qto_DoorBaseQuantities", door_quantities())
    add_pset(f, door, "Pset_DoorCommon", {
        "Reference": "Pivot — stainless frame + glass",
        "IsExternal": True, "FireRating": "",
        "AcousticRating": "Rw 30 dB",
        "ThermalTransmittance": 5.5,
        "Infiltration": 0.5,
    })

    # Opening voids the panel (and conceptually, the curtain wall)
    opening = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcOpeningElement", name="Door Opening",
        predefined_type="OPENING",
    )
    opening.ObjectPlacement = make_lp(
        f, location=(door_x, -GLASS_THK, 0),
        parent_placement=cw_lp,
    )
    opening.Representation = box_repr(
        f, body, DOOR_WIDTH, GLASS_THK * 3, DOOR_HEIGHT,
    )
    f.create_entity(
        "IfcRelVoidsElement",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        RelatingBuildingElement=host_panel,
        RelatedOpeningElement=opening,
    )
    f.create_entity(
        "IfcRelFillsElement",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        RelatingOpeningElement=opening,
        RelatedBuildingElement=door,
    )
    add_pset(f, opening, "Pset_OpeningElementCommon", {
        "Reference": "Door opening",
        "PurposeOfOpening": "Pivot door for entry",
    })
    add_qto(f, owner_history, opening, "Qto_OpeningElementBaseQuantities",
            opening_quantities(DOOR_WIDTH, GLASS_THK * 3, DOOR_HEIGHT))
    return door, opening


def make_stair(f, body, owner_history, storey, types, *,
               name, local_xyz, n_risers):
    stair = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcStair", name=name,
        predefined_type="STRAIGHT_RUN_STAIR",
    )
    stair.ObjectPlacement = make_lp(
        f, location=local_xyz, parent_placement=storey.ObjectPlacement,
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[stair], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[stair], relating_type=types["stair"]
    )

    flight = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcStairFlight", name=f"{name} — Flight 1",
        predefined_type="STRAIGHT",
    )
    flight.NumberOfRisers = n_risers
    flight.NumberOfTreads = n_risers - 1
    flight.RiserHeight = float(STAIR_RISER)
    flight.TreadLength = float(STAIR_TREAD)
    flight.ObjectPlacement = make_lp(
        f, location=(0, 0, 0), parent_placement=stair.ObjectPlacement,
    )
    flight.Representation = stair_flight_repr(
        f, body, n_risers, STAIR_RISER, STAIR_TREAD, STAIR_WIDTH,
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[flight], relating_type=types["stair_flight"]
    )
    add_qto(
        f, owner_history, flight, "Qto_StairFlightBaseQuantities",
        stair_flight_quantities(n_risers, STAIR_RISER, STAIR_TREAD, STAIR_WIDTH),
    )
    add_pset(f, flight, "Pset_StairFlightCommon", {
        "Reference": "Travertine treads, single flight",
    })

    # Railing (one side only — Mies kept it minimal; we add 1)
    rail_h = 0.90
    rail = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcRailing", name=f"{name} — Handrail",
        predefined_type="GUARDRAIL",
    )
    rail.ObjectPlacement = make_lp(
        f, location=(0, STAIR_WIDTH - 0.04, n_risers * STAIR_RISER + rail_h - 0.04),
        parent_placement=stair.ObjectPlacement,
    )
    rail.Representation = box_repr(
        f, body, n_risers * STAIR_TREAD, 0.04, 0.04,
    )
    ifcopenshell.api.spatial.assign_container(
        f, products=[rail], relating_structure=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[rail], relating_type=types["railing"]
    )
    add_pset(f, rail, "Pset_RailingCommon", {
        "Reference": "Stainless rail 40×40",
        "Height": rail_h, "IsExternal": True,
    })
    add_qto(f, owner_history, rail, "Qto_RailingBaseQuantities",
            railing_quantities(n_risers * STAIR_TREAD, rail_h))

    ifcopenshell.api.aggregate.assign_object(
        f, products=[flight, rail], relating_object=stair,
    )
    add_pset(f, stair, "Pset_StairCommon", {
        "Reference": name, "IsExternal": True,
        "NumberOfRiser": n_risers,
        "NumberOfTreads": n_risers - 1,
        "RiserHeight": float(STAIR_RISER),
        "TreadLength": float(STAIR_TREAD),
    })
    add_qto(f, owner_history, stair, "Qto_StairBaseQuantities",
            stair_quantities(n_risers, STAIR_RISER, STAIR_TREAD, STAIR_WIDTH))
    return stair


def make_space(f, body, owner_history, storey, space_type, *, local_xyz, size):
    w, d, h = size
    space = ifcopenshell.api.root.create_entity(
        f, ifc_class="IfcSpace", name="Pavilion (universal space)",
        predefined_type="SPACE",
    )
    space.LongName = "Mies's universal space — single open living volume"
    space.ObjectPlacement = make_lp(
        f, location=local_xyz, parent_placement=storey.ObjectPlacement,
    )
    space.Representation = box_repr(f, body, w, d, h)
    # IfcSpace is a spatial element; aggregated under storey (not contained).
    ifcopenshell.api.aggregate.assign_object(
        f, products=[space], relating_object=storey
    )
    ifcopenshell.api.type.assign_type(
        f, related_objects=[space], relating_type=space_type
    )
    add_qto(f, owner_history, space, "Qto_SpaceBaseQuantities",
            space_quantities(w, d, h))
    add_pset(f, space, "Pset_SpaceCommon", {
        "Reference": "Living/Dining/Kitchen open plan",
        "IsExternal": False,
        "PubliclyAccessible": False,
        "HandicapAccessible": False,
    })
    return space


def _make_simple_box(
    f, body, owner_history, storey, *,
    ifc_class, name, predefined_type, location, size,
    type_relation=None, pset_name=None, pset_props=None,
):
    """Generic LOD-400 furnishing/fixture/site element placed as a box."""
    el = ifcopenshell.api.root.create_entity(
        f, ifc_class=ifc_class, name=name, predefined_type=predefined_type,
    )
    el.ObjectPlacement = make_lp(
        f, location=location, parent_placement=storey.ObjectPlacement,
    )
    w, d, h = size
    el.Representation = box_repr(f, body, w, d, h)
    ifcopenshell.api.spatial.assign_container(
        f, products=[el], relating_structure=storey
    )
    if type_relation is not None:
        ifcopenshell.api.type.assign_type(
            f, related_objects=[el], relating_type=type_relation
        )
    add_qto(f, owner_history, el, "Qto_BodyGeometryValidation", [
        ("IfcQuantityLength", "Width", w, "LengthValue"),
        ("IfcQuantityLength", "Depth", d, "LengthValue"),
        ("IfcQuantityLength", "Height", h, "LengthValue"),
        ("IfcQuantityVolume", "Volume", w * d * h, "VolumeValue"),
    ])
    if pset_name:
        add_pset(f, el, pset_name, pset_props or {"Reference": name})
    else:
        add_pset(f, el, "Pset_FurnitureTypeCommon", {"Reference": name})
    return el


def _make_interior(f, body, owner_history, storey_main, types):
    """Build the iconic Mies primavera core + kitchen + fireplace + furniture
    inside the pavilion. All coordinates are local to Main Floor storey
    (so Z=0 = main floor surface)."""
    items = []
    F = lambda **kw: _make_simple_box(
        f, body, owner_history, storey_main, ifc_class="IfcFurnishingElement", **kw,
    )
    S = lambda **kw: _make_simple_box(
        f, body, owner_history, storey_main, ifc_class="IfcSanitaryTerminal", **kw,
    )

    # ----- Primavera wood core (the family-room-sized box that contains
    # kitchen, bath, mech, and sleeping nook screen) ---------------------
    items.append(F(
        name="Primavera Wood Core",
        predefined_type=None,
        type_relation=types["furn_core"],
        location=(4.50, 3.20, 0.0),
        size=(5.50, 2.30, 2.40),
        pset_name="Pset_FurnitureTypeCommon",
        pset_props={"Reference": "Primavera-veneered freestanding core volume"},
    ))

    # ----- Stone fireplace hearth + chimney -----------------------------
    items.append(F(
        name="Fireplace Hearth (Roman travertine)",
        predefined_type=None,
        type_relation=types["furn_fireplace"],
        location=(6.00, 0.40, 0.0),
        size=(3.00, 0.80, 0.45),
    ))
    items.append(F(
        name="Fireplace Chimney Block",
        predefined_type=None,
        type_relation=types["furn_fireplace"],
        location=(7.00, 1.00, 0.45),
        size=(1.00, 0.40, 1.95),
    ))

    # ----- Kitchen counter (north of core) ------------------------------
    items.append(F(
        name="Kitchen Counter (stainless + primavera)",
        predefined_type=None,
        type_relation=types["furn_kitchen"],
        location=(4.50, 5.55, 0.0),
        size=(5.50, 0.65, 0.85),
    ))
    items.append(F(
        name="Refrigerator",
        predefined_type=None,
        type_relation=types["furn_appl"],
        location=(9.30, 5.55, 0.0),
        size=(0.65, 0.65, 1.80),
    ))
    items.append(F(
        name="Cooktop / Range",
        predefined_type=None,
        type_relation=types["furn_appl"],
        location=(7.00, 5.55, 0.85),
        size=(0.80, 0.65, 0.10),
    ))
    items.append(F(
        name="Range Hood",
        predefined_type=None,
        type_relation=types["furn_appl"],
        location=(7.00, 5.65, 1.50),
        size=(0.80, 0.45, 0.30),
    ))

    # ----- Bedroom (west of core, screened by wardrobe) -----------------
    items.append(F(
        name="Bed (Mies design, daybed)",
        predefined_type=None,
        type_relation=types["furn_bed"],
        location=(2.10, 4.20, 0.10),
        size=(2.00, 1.60, 0.45),
    ))
    items.append(F(
        name="Wardrobe (primavera screen)",
        predefined_type=None,
        type_relation=types["furn_wardrobe"],
        location=(2.00, 1.40, 0.0),
        size=(2.20, 0.55, 1.80),
    ))
    items.append(F(
        name="Nightstand",
        predefined_type=None,
        type_relation=types["furn_wardrobe"],
        location=(4.20, 6.00, 0.0),
        size=(0.55, 0.40, 0.55),
    ))

    # ----- Dining (between core and east end) ---------------------------
    items.append(F(
        name="Mies Dining Table (Italian marble)",
        predefined_type=None,
        type_relation=types["furn_dining_table"],
        location=(11.00, 3.40, 0.0),
        size=(1.80, 0.95, 0.74),
    ))
    for i, (cx, cy) in enumerate([
        (10.85, 2.55), (10.85, 4.55),
        (12.50, 2.55), (12.50, 4.55),
    ]):
        items.append(F(
            name=f"Mies Brno Dining Chair {i + 1}",
            predefined_type=None,
            type_relation=types["furn_diningchair"],
            location=(cx, cy, 0.0),
            size=(0.55, 0.55, 0.85),
        ))

    # ----- Living room (east end, around coffee table) -----------------
    for i, (cx, cy, w, d) in enumerate([
        (15.50, 1.40, 0.78, 0.78),
        (15.50, 5.20, 0.78, 0.78),
        (18.50, 1.40, 0.78, 0.78),
        (18.50, 5.20, 0.78, 0.78),
    ]):
        items.append(F(
            name=f"Barcelona Chair {i + 1}",
            predefined_type=None,
            type_relation=types["furn_chair"],
            location=(cx, cy, 0.0),
            size=(w, d, 0.76),
        ))
    items.append(F(
        name="Mies Coffee Table (glass)",
        predefined_type=None,
        type_relation=types["furn_coffee_table"],
        location=(16.40, 3.20, 0.0),
        size=(1.30, 1.10, 0.42),
    ))

    # ----- Bathroom inside the core (south side) ------------------------
    items.append(S(
        name="Wall-hung WC",
        predefined_type="TOILETPAN",
        type_relation=types["sani_toilet"],
        location=(5.00, 3.40, 0.0),
        size=(0.65, 0.40, 0.42),
        pset_name="Pset_SanitaryTerminalTypeToiletPan",
        pset_props={"Reference": "Wall-hung WC"},
    ))
    items.append(S(
        name="Wall-hung Sink",
        predefined_type="WASHHANDBASIN",
        type_relation=types["sani_sink"],
        location=(5.00, 4.10, 0.85),
        size=(0.55, 0.40, 0.20),
        pset_name="Pset_SanitaryTerminalTypeWashHandBasin",
        pset_props={"Reference": "Wall-hung lavatory"},
    ))
    items.append(S(
        name="Shower Stall",
        predefined_type="SHOWER",
        type_relation=types["sani_shower"],
        location=(8.50, 3.40, 0.0),
        size=(1.00, 1.00, 2.10),
        pset_name="Pset_SanitaryTerminalTypeShower",
        pset_props={"Reference": "Glass shower stall"},
    ))
    # Kitchen sink (drop-in stainless basin in counter)
    items.append(S(
        name="Kitchen Sink (stainless double basin)",
        predefined_type="SINK",
        type_relation=types["sani_kitchen_sink"],
        location=(5.50, 5.65, 0.85),
        size=(0.85, 0.50, 0.20),
        pset_name="Pset_SanitaryTerminalTypeSink",
        pset_props={"Reference": "Stainless steel double basin, undermount"},
    ))

    return items


def _make_finishes(f, body, owner_history, storeys, types):
    """Travertine floor finish + plaster ceiling + terrace finish."""
    items = []
    # Main floor travertine (50mm above slab top, so visible above the slab)
    main = _make_simple_box(
        f, body, owner_history, storeys["Main Floor"],
        ifc_class="IfcCovering",
        name="Travertine Floor Finish (main)",
        predefined_type="FLOORING",
        type_relation=types["covering_floor"],
        location=(0.0, 0.0, 0.0),
        size=(MAIN_LEN, MAIN_WID, 0.005),  # 5mm visible bump
        pset_name="Pset_CoveringCommon",
        pset_props={"Reference": "Travertine pavers 1.524 m grid"},
    )
    items.append(main)
    # Terrace travertine
    items.append(_make_simple_box(
        f, body, owner_history, storeys["Terrace Level"],
        ifc_class="IfcCovering",
        name="Travertine Floor Finish (terrace)",
        predefined_type="FLOORING",
        type_relation=types["covering_floor"],
        location=(TERRACE_X0, TERRACE_Y0, 0.0),
        size=(TERRACE_LEN, TERRACE_WID, 0.005),
        pset_name="Pset_CoveringCommon",
        pset_props={"Reference": "Travertine terrace pavers"},
    ))
    # Plaster ceiling under roof
    items.append(_make_simple_box(
        f, body, owner_history, storeys["Roof"],
        ifc_class="IfcCovering",
        name="Plaster Ceiling",
        predefined_type="CEILING",
        type_relation=types["covering_ceiling"],
        location=(0.0, 0.0, -0.015),
        size=(MAIN_LEN, MAIN_WID, 0.015),
        pset_name="Pset_CoveringCommon",
        pset_props={"Reference": "White plaster, 15mm"},
    ))
    return items


def _make_lighting(f, body, owner_history, storey_main, types):
    """3 light fixtures: floor lamp, 2 table lamps, plus 2 ceiling lights."""
    items = []
    L = lambda **kw: _make_simple_box(
        f, body, owner_history, storey_main,
        ifc_class="IfcLightFixture", **kw,
    )
    # Floor lamp next to Barcelona chairs
    items.append(L(
        name="Floor Lamp (Mies design)",
        predefined_type="DIRECTIONSOURCE",
        type_relation=types["light_floor"],
        location=(15.20, 3.30, 0.0),
        size=(0.20, 0.20, 1.65),
        pset_name="Pset_LightFixtureTypeCommon",
        pset_props={"Reference": "Brass uplighter"},
    ))
    # Table lamp on sideboard 1
    items.append(L(
        name="Table Lamp (sideboard E)",
        predefined_type="POINTSOURCE",
        type_relation=types["light_table"],
        location=(20.50, 7.10, 0.85),
        size=(0.30, 0.30, 0.55),
        pset_name="Pset_LightFixtureTypeCommon",
        pset_props={"Reference": "Brass + linen shade"},
    ))
    # Table lamp on dining sideboard
    items.append(L(
        name="Table Lamp (dining sideboard)",
        predefined_type="POINTSOURCE",
        type_relation=types["light_table"],
        location=(13.10, 7.10, 0.85),
        size=(0.30, 0.30, 0.55),
        pset_name="Pset_LightFixtureTypeCommon",
        pset_props={"Reference": "Brass + linen shade"},
    ))
    # Two recessed ceiling lights
    for i, x in enumerate([7.0, 16.0]):
        items.append(L(
            name=f"Ceiling Recessed Downlight {i + 1}",
            predefined_type="DIRECTIONSOURCE",
            type_relation=types["light_ceiling"],
            location=(x - 0.15, 4.30, 2.85),
            size=(0.30, 0.30, 0.05),
            pset_name="Pset_LightFixtureTypeCommon",
            pset_props={"Reference": "100mm recessed downlight"},
        ))
    return items


def _make_more_furniture(f, body, owner_history, storey_main, types):
    """Sideboards, bookshelf, console — fills the empty east wall."""
    items = []
    F = lambda **kw: _make_simple_box(
        f, body, owner_history, storey_main,
        ifc_class="IfcFurnishingElement", **kw,
    )
    # East-wall low credenza/sideboard
    items.append(F(
        name="Sideboard (Mies, primavera)",
        predefined_type=None,
        type_relation=types["furn_sideboard"],
        location=(19.50, 6.80, 0.0),
        size=(2.00, 0.50, 0.85),
        pset_name="Pset_FurnitureTypeCommon",
        pset_props={"Reference": "Primavera credenza, brass legs"},
    ))
    # Dining-zone sideboard
    items.append(F(
        name="Sideboard (dining)",
        predefined_type=None,
        type_relation=types["furn_sideboard"],
        location=(12.20, 6.80, 0.0),
        size=(1.80, 0.50, 0.85),
        pset_name="Pset_FurnitureTypeCommon",
        pset_props={"Reference": "Primavera credenza for dining service"},
    ))
    # Bookshelf along east end
    items.append(F(
        name="Bookshelf (primavera, full height)",
        predefined_type=None,
        type_relation=types["furn_bookshelf"],
        location=(20.50, 1.50, 0.0),
        size=(1.20, 0.40, 1.80),
        pset_name="Pset_FurnitureTypeCommon",
        pset_props={"Reference": "Built-in primavera bookshelf"},
    ))
    return items


def _make_curtains(f, body, owner_history, storey_main, types, *,
                   glass_x0, glass_long):
    """Two Shantung silk curtain panels — drawn open at panel ends."""
    items = []
    # Curtain 1: covers west portion of south bay 1
    items.append(_make_simple_box(
        f, body, owner_history, storey_main,
        ifc_class="IfcCovering",
        name="Shantung Curtain — South West",
        predefined_type="MOLDING",
        type_relation=types["covering_curtain"],
        location=(glass_x0 + 0.40, 0.10, 0.0),
        size=(0.60, 0.05, 2.85),
        pset_name="Pset_CoveringCommon",
        pset_props={"Reference": "Hand-woven Shantung silk, cream"},
    ))
    # Curtain 2: covers west portion of north bay 1
    items.append(_make_simple_box(
        f, body, owner_history, storey_main,
        ifc_class="IfcCovering",
        name="Shantung Curtain — North West",
        predefined_type="MOLDING",
        type_relation=types["covering_curtain"],
        location=(glass_x0 + 0.40, MAIN_WID - 0.15, 0.0),
        size=(0.60, 0.05, 2.85),
        pset_name="Pset_CoveringCommon",
        pset_props={"Reference": "Hand-woven Shantung silk, cream"},
    ))
    # Curtain track (long) on south
    items.append(_make_simple_box(
        f, body, owner_history, storey_main,
        ifc_class="IfcCovering",
        name="Curtain Track — South",
        predefined_type="MOLDING",
        type_relation=types["covering_curtain"],
        location=(glass_x0, 0.04, 2.85),
        size=(glass_long, 0.04, 0.05),
        pset_name="Pset_CoveringCommon",
        pset_props={"Reference": "Brass curtain track"},
    ))
    # Curtain track (long) on north
    items.append(_make_simple_box(
        f, body, owner_history, storey_main,
        ifc_class="IfcCovering",
        name="Curtain Track — North",
        predefined_type="MOLDING",
        type_relation=types["covering_curtain"],
        location=(glass_x0, MAIN_WID - 0.08, 2.85),
        size=(glass_long, 0.04, 0.05),
        pset_name="Pset_CoveringCommon",
        pset_props={"Reference": "Brass curtain track"},
    ))
    return items


def _make_window_stops(f, body, owner_history, storey_main, types, long_xs):
    """Steel angle stops at column inner faces — LOD 500 hardware that
    actually holds the curtain wall glass to the columns."""
    items = []
    # Two stops per column (south face + north face), each is a thin
    # vertical angle running the full glass height.
    for i, cx in enumerate(long_xs):
        for tag, cy_inner in (
            ("S", -0.0),
            ("N", MAIN_WID - 0.04),
        ):
            items.append(_make_simple_box(
                f, body, owner_history, storey_main,
                ifc_class="IfcMember",
                name=f"Window Stop {tag}{i + 1}",
                predefined_type="MULLION",
                type_relation=types["window_stop"],
                location=(cx - 0.04, cy_inner, 0.05),
                size=(0.08, 0.04, 2.80),
                pset_name="Pset_MemberCommon",
                pset_props={"Reference": "SS angle 50×50, glass clamp"},
            ))
    return items


def _make_landscape(f, body, owner_history, storey_grade, types):
    """Site context: Fox River, gravel drive, bluestone path, big maple."""
    items = []
    # Fox River — wide water body to the north (positive Y).
    items.append(_make_simple_box(
        f, body, owner_history, storey_grade,
        ifc_class="IfcGeographicElement",
        name="Fox River",
        predefined_type=None,
        type_relation=types["geo_water"],
        location=(-50.0, 24.0, -0.50),
        size=(120.0, 35.0, 0.40),
        pset_name="Pset_SiteCommon",
        pset_props={"Reference": "Fox River — flood plain water surface"},
    ))
    # Gravel driveway approaching from the south
    items.append(_make_simple_box(
        f, body, owner_history, storey_grade,
        ifc_class="IfcGeographicElement",
        name="Gravel Driveway",
        predefined_type=None,
        type_relation=types["geo_drive"],
        location=(-2.0, -25.0, 0.0),
        size=(4.0, 18.0, 0.05),
        pset_name="Pset_SiteCommon",
        pset_props={"Reference": "Crushed limestone driveway, 4 m wide"},
    ))
    # Bluestone path from drive to terrace stair
    items.append(_make_simple_box(
        f, body, owner_history, storey_grade,
        ifc_class="IfcGeographicElement",
        name="Bluestone Path (drive → terrace)",
        predefined_type=None,
        type_relation=types["geo_path"],
        location=(-22.0, -7.0, 0.0),
        size=(3.0, 12.0, 0.05),
        pset_name="Pset_SiteCommon",
        pset_props={"Reference": "Bluestone path, 1.2 m wide"},
    ))
    # The big black maple Mies designed around (the "Black Maple")
    items.append(_make_simple_box(
        f, body, owner_history, storey_grade,
        ifc_class="IfcGeographicElement",
        name="Black Maple (specimen)",
        predefined_type=None,
        type_relation=types["geo_tree"],
        location=(-2.0, 16.0, 0.0),
        size=(0.80, 0.80, 12.0),
        pset_name="Pset_SiteCommon",
        pset_props={"Reference": "Acer nigrum — the famous specimen"},
    ))
    # Black maple canopy — wide
    items.append(_make_simple_box(
        f, body, owner_history, storey_grade,
        ifc_class="IfcGeographicElement",
        name="Black Maple Canopy",
        predefined_type=None,
        type_relation=types["geo_tree"],
        location=(-7.0, 11.0, 8.0),
        size=(11.0, 11.0, 7.0),
        pset_name="Pset_SiteCommon",
        pset_props={"Reference": "Acer nigrum canopy ~11 m diameter"},
    ))
    return items


def _make_trees(f, body, owner_history, storey_grade, types):
    """A few black-locust / black-walnut trees around the site for context."""
    items = []
    # Each tree = trunk + foliage (simplified)
    tree_positions = [
        (-30, -8, "trunk1"),
        (-25, 18, "trunk2"),
        (15, -8, "trunk3"),
        (28, 14, "trunk4"),
        (-12, 22, "trunk5"),
        (10, 22, "trunk6"),
    ]
    for tx, ty, tag in tree_positions:
        # Trunk
        items.append(_make_simple_box(
            f, body, owner_history, storey_grade,
            ifc_class="IfcGeographicElement",
            name=f"Tree Trunk {tag}",
            predefined_type=None,
            type_relation=types["geo_tree"],
            location=(tx - 0.20, ty - 0.20, 0.0),
            size=(0.40, 0.40, 4.50),
            pset_name="Pset_SiteCommon",
            pset_props={"Reference": "Black walnut trunk"},
        ))
        # Canopy
        items.append(_make_simple_box(
            f, body, owner_history, storey_grade,
            ifc_class="IfcGeographicElement",
            name=f"Tree Canopy {tag}",
            predefined_type=None,
            type_relation=types["geo_tree"],
            location=(tx - 2.0, ty - 2.0, 4.0),
            size=(4.00, 4.00, 4.00),
            pset_name="Pset_SiteCommon",
            pset_props={"Reference": "Black walnut canopy"},
        ))
    return items


def make_space_boundary(f, owner_history, space, related_element, side, *,
                        internal=False):
    f.create_entity(
        "IfcRelSpaceBoundary",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner_history,
        Name=f"Boundary: {space.Name} <-> {related_element.Name}",
        Description=f"side={side}",
        RelatingSpace=space,
        RelatedBuildingElement=related_element,
        ConnectionGeometry=None,
        PhysicalOrVirtualBoundary="PHYSICAL",
        InternalOrExternalBoundary="INTERNAL" if internal else "EXTERNAL",
    )


# ============================================================================
# Main
# ============================================================================


def main():
    print("Generating Farnsworth House v2 (LOD 350) …")
    t0 = time.time()
    f = build()
    out = os.path.join(os.path.dirname(__file__), "farnsworth_house_v2.ifc")
    f.write(out)
    dt = time.time() - t0

    counts = {}
    for inst in f:
        if inst.is_a("IfcRoot"):
            counts[inst.is_a()] = counts.get(inst.is_a(), 0) + 1
    print(f"  size: {os.path.getsize(out) / 1024:.1f} KB   time: {dt:.2f}s")
    print(f"  total IfcRoot entities: {sum(counts.values())}")
    print()
    for k in sorted(counts):
        print(f"  {k:34s} {counts[k]}")


if __name__ == "__main__":
    main()
