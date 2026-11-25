"""The PLAXIS model read from the PLAXIS project itself (its own coordinates), not from a drawing.

Three ways in, all giving the same JSON "model":
  1. live:   the app talks to PLAXIS Input through the remote scripting server (plxscripting, see the PLAXIS
             Reference Manual, appendix F.2 "Python HTTP REST API wrapper") and reads the open / given project;
  2. file:   this file is also a stand-alone script. Run it with PLAXIS' own Python (Expert > Python > Run script,
             or `python plaxis_model.py --port 10000 --password ... --out model.json`) on the PC with PLAXIS, then
             upload model.json in the app (useful when the app runs on another PC);
  3. demo:   with the simulated solver, an example anchored excavation (2D or 3D) so the workflow can be tried.

Coordinates are PLAXIS' own: PLAXIS 2D (x, y) with y up; PLAXIS 3D (x, y, z) with z up.
The script part only needs plxscripting, so keep this module free of app imports at the top level.
"""
import json
import math
import re
from html import escape

# ---------------------------------------------------------------------------------------------------------------
# Mesh: Element distribution, PLAXIS Reference Manual 7.1.2. The value given to the `mesh` command is re x 0.06.
MESH_LEVELS = {
    "2d": [("Very coarse", 2.0), ("Coarse", 1.33), ("Medium", 1.0), ("Fine", 0.67), ("Very fine", 0.5)],
    "3d": [("Very coarse", 2.0), ("Coarse", 1.5), ("Medium", 1.0), ("Fine", 0.7), ("Very fine", 0.5)],
}


def mesh_values(version: str) -> list[str]:
    """Mesh levels as variable values: 'Coarse: 0.08' (label: value passed to g_i.mesh)."""
    return [f"{lab}: {round(re * 0.06, 3):g}" for lab, re in MESH_LEVELS.get(version, MESH_LEVELS["2d"])]


# Parameters offered for optimization, per object type: (property, label, unit, section)
SOIL_PROPS = [("Eref", "Stiffness E'ref", "kN/m²"), ("E50ref", "Stiffness E50ref", "kN/m²"),
              ("Eoedref", "Oedometer stiffness Eoed,ref", "kN/m²"), ("Eurref", "Unloading stiffness Eur,ref", "kN/m²"),
              ("E", "Stiffness E'", "kN/m²"), ("phi", "Friction angle φ'", "°"), ("cref", "Cohesion c'ref", "kPa"),
              ("psi", "Dilatancy angle ψ", "°"), ("gammaUnsat", "Unit weight γunsat", "kN/m³"),
              ("gammaSat", "Unit weight γsat", "kN/m³"), ("nu", "Poisson's ratio ν'", "")]
PLATE_PROPS = [("EA", "Axial stiffness EA", "kN/m"), ("EA1", "Axial stiffness EA1", "kN/m"),
               ("EI", "Bending stiffness EI", "kN·m²/m"), ("E1", "Young's modulus E1", "kN/m²"), ("d", "Thickness d", "m"),
               ("w", "Weight w", "kN/m/m"), ("Gamma", "Unit weight γ", "kN/m³")]
ANCHOR_PROPS = [("EA", "Axial stiffness EA", "kN"), ("Lspacing", "Spacing Ls", "m")]
GEOGRID_PROPS = [("EA", "Axial stiffness EA", "kN/m"), ("EA1", "Axial stiffness EA1", "kN/m")]
BEAM_PROPS = [("E", "Young's modulus E", "kN/m²"), ("Diameter", "Diameter", "m"), ("Lspacing", "Spacing Ls", "m"),
              ("Tskinstartmax", "Skin resistance T,start", "kN/m"), ("Fmax", "Base resistance Fmax", "kN")]
MAT_PROPS = {"soil": SOIL_PROPS, "plate": PLATE_PROPS, "anchor": ANCHOR_PROPS, "geogrid": GEOGRID_PROPS, "cable": ANCHOR_PROPS,
             "embedded_beam": BEAM_PROPS, "beam": BEAM_PROPS}
LOAD_PROPS = {"line": [("qx_start", "qx"), ("qy_start", "qy"), ("qz_start", "qz")],
              "point": [("Fx", "Fx"), ("Fy", "Fy"), ("Fz", "Fz")],
              "surface": [("sigx", "σx"), ("sigy", "σy"), ("sigz", "σz"), ("sigz_ref", "σz,ref")]}
LOAD_UNIT = {("2d", "line"): "kN/m/m", ("2d", "point"): "kN/m", ("3d", "line"): "kN/m", ("3d", "point"): "kN",
             ("3d", "surface"): "kN/m²", ("2d", "surface"): "kN/m²"}


# V26.1.9: PLAXIS names of the well discharge and the drain head (the first one this PLAXIS has is used)
WELL_PROPS = ("Qwell", "Q", "Discharge", "qwell")
DRAIN_PROPS = ("h", "Head", "hmin")
STRUCT_GROUPS = {"plate": ("Plates",), "anchor": ("NodeToNodeAnchors", "FixedEndAnchors"), "geogrid": ("Geogrids",), "cable": ("Cables",),
                 "embedded_beam": ("EmbeddedBeams", "EmbeddedBeamRows"), "beam": ("Beams",)}
DRAINAGE_TYPES = ["Drained: 0", "Undrained (A): 1", "Undrained (B): 2", "Undrained (C): 3", "Non-porous: 4"]
PORE_TYPES = ["Phreatic: 0", "Steady-state groundwater flow: 1", "Use pressures from previous phase: 2"]
CLUSTER_CONDITIONS = ["Global level: 0", "Dry: 1", "Interpolate: 2"]
DESIGN_APPROACHES = ["Characteristic values (no factors): 0", "EC7 DA1 Combination 1: 1", "EC7 DA1 Combination 2: 2",
                     "EC7 DA2*: 3", "EC7 DA3: 4"]


# Labels of PLAXIS input parameters (any other numeric input parameter is shown under its PLAXIS name)
LABELS = {
    "gammaUnsat": "Unit weight γunsat", "gammaSat": "Unit weight γsat", "einit": "Initial void ratio einit",
    "Eref": "Young's modulus E'ref", "E": "Young's modulus E'", "nu": "Poisson's ratio ν'", "Gref": "Shear modulus Gref",
    "Eoed": "Oedometer modulus Eoed", "E50ref": "Secant stiffness E50ref", "Eoedref": "Oedometer stiffness Eoed,ref",
    "Eurref": "Unloading stiffness Eur,ref", "powerm": "Power m", "nuur": "Poisson's ratio νur", "pref": "Reference stress pref",
    "K0nc": "K0nc", "Rf": "Failure ratio Rf", "G0ref": "Small-strain shear modulus G0ref", "gamma07": "Shear strain γ0.7",
    "cref": "Cohesion c'ref", "c": "Cohesion c'", "phi": "Friction angle φ'", "psi": "Dilatancy angle ψ",
    "cinc": "Cohesion increase c'inc", "Einc": "Stiffness increase E'inc", "verticalRef": "Reference level",
    "TensileStrength": "Tension cut-off strength", "Rinter": "Interface strength Rinter", "K0Primary": "K0 (x)",
    "K0Secondary": "K0 (y)", "OCR": "OCR", "POP": "POP", "kx": "Permeability kx", "ky": "Permeability ky",
    "kz": "Permeability kz", "Cc": "Compression index Cc", "Cs": "Swelling index Cs", "lambdaModified": "λ*",
    "kappaModified": "κ*", "muModified": "μ*", "Su": "Undrained shear strength su", "SuRef": "su,ref",
    "EA": "Axial stiffness EA", "EA1": "Axial stiffness EA1", "EA2": "Axial stiffness EA2", "EI": "Bending stiffness EI",
    "d": "Thickness d", "w": "Weight w", "Gamma": "Unit weight γ", "E1": "Young's modulus E1", "E2": "Young's modulus E2",
    "G12": "Shear modulus G12", "G13": "Shear modulus G13", "G23": "Shear modulus G23", "nu12": "Poisson's ratio ν12",
    "Lspacing": "Spacing Ls", "Diameter": "Diameter", "Tskinstartmax": "Skin resistance T,start,max",
    "TskinStartMax": "Skin resistance T,start,max", "Tskinendmax": "Skin resistance T,end,max",
    "TskinEndMax": "Skin resistance T,end,max", "Fmax": "Base resistance Fmax", "Head": "Groundwater head",
    "TimeInterval": "Time interval", "MaxStepsStored": "Max steps stored", "Deform.ToleratedError": "Tolerated error",
    "Deform.MaxSteps": "Max steps", "Deform.MaxIterations": "Max iterations", "Deform.MaxUnloadingSteps": "Max unloading steps",
    "Deform.MaxLoadFractionPerStep": "Max load fraction per step", "PrestressForce": "Prestress force",
    "Cref": "Contraction Cref", "Qwell": "Well discharge |Qwell|", "h": "Drain head h", "DeltaInter": "Virtual thickness factor δinter",
}
SKIP_PROP = re.compile(r"(?i)colou?r|number|index|^id$|identification|comment|type|model|drainage|behaviou?r|method|"
                       r"^use|valid|flag|count|version|^is[A-Z_]|selected|visible|guid|tabledata|determination|default|"
                       r"unit|option|^name$|^x$|^y$|^z$|undrained[A-Z]?$|^solver|^seed|matrix|^dataset|curve")


def unit_of(prop: str, kind: str = "soil", version: str = "2d") -> str:
    """PLAXIS' unit of an input parameter (default PLAXIS units: kN, m, day)."""
    p = prop.split(".")[-1]
    # V26.1.8: the PLAXIS 3D spellings (E50Ref, EOedRef, cRef, pRef, PermVertical…) and the beam section values
    if re.fullmatch(r"(?i)e\w*ref|g\d?ref|cref|pref|eoed\w*|eur\w*", p):
        return "kN/m²"
    if re.fullmatch(r"(?i)perm\w*", p):
        return "m/day"
    if p in ("A",):
        return "m²"
    if re.fullmatch(r"I\d", p):
        return "m⁴"
    if p in ("Height", "Width"):
        return "m"
    if p in ("FMax", "Fmax"):
        return "kN"
    if re.fullmatch(r"(?i)gamma(unsat|sat)?", p) or p == "Gamma":
        return "kN/m³"
    if re.fullmatch(r"phi|psi|phi\w*|psi\w*", p):
        return "°"
    if p in ("cinc", "Einc"):
        return "kN/m²/m"
    if p == "EA" or p.startswith("EA"):
        return "kN" if kind == "anchor" else "kN/m"
    if p == "EI":
        return "kN·m²/m"
    if p == "w":
        return "kN/m/m" if version == "2d" else "kN/m²"
    if p in ("d", "Diameter", "Lspacing", "verticalRef", "zref", "yref", "Head", "Top", "Bottom"):
        return "m"
    if re.fullmatch(r"k[xyz]|perm\w*", p):
        return "m/day"
    if p in ("Fmax", "PrestressForce"):
        return "kN"
    if p.lower().startswith("tskin"):
        return "kN/m"
    if p == "TimeInterval":
        return "day"
    if re.fullmatch(r"E\w*ref|E|E1|E2|Eoed|G\w*ref|G\d\d|cref|c|su\w*|Su\w*|pref|POP|TensileStrength|sig\w*", p):
        return "kN/m²"
    return "-"


_LABELS_CI = {k.lower(): v for k, v in LABELS.items()}
# V26.1.8: soil parameters that are not design inputs (classification, flow-model internals, numerical limits): not
# offered as variables, so the soil list shows what an engineer varies
HIDDEN_SOIL = re.compile(r"(?i)^(ClayFraction|SandFraction|SiltFraction|CrossPermeability|DeltaInter|GroundwaterSoilClass\w*|"
                         r"GwPsiUnsat|Kwn|SkemptonB|eMax|eMin|cK|nInit|nuUEquivalent|VerticalRef|K0Primary|K0Secondary|"
                         r"CC|CS)$")


def label_of(prop: str) -> str:
    p = prop.split(".")[-1]
    return LABELS.get(prop) or LABELS.get(p) or _LABELS_CI.get(p.lower()) or prop


def _numeric_attrs(obj, limit: int = 400) -> dict:
    """Every numeric input parameter PLAXIS exposes for this object (names from the scripting server)."""
    out = {}
    try:
        names = [n for n in dir(obj) if not n.startswith("_")]
    except Exception:  # noqa: BLE001
        return out
    for n in names[:limit]:
        if SKIP_PROP.search(n):
            continue
        try:
            raw = getattr(obj, n)
        except Exception:  # noqa: BLE001
            continue
        if callable(raw) and not hasattr(raw, "value"):
            continue
        val = _v(raw)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
            continue
        out[n] = float(val)
    return out


def _staged(obj, prop, phase):
    """Value of a staged property in a phase (e.g. LineLoad_1_1.qy_start[Phase_3]); None when not staged."""
    try:
        return _num(getattr(obj, prop)[phase])
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------------------------------------------
# 1. Live: read the model through the remote scripting server
def _v(x):
    try:
        return x.value
    except Exception:  # noqa: BLE001
        return x


# QA V26.1.9 D1: PLAXIS 3D 2024.3 gives the calculation type of a phase as a number (1 K0 procedure, 4 Plastic,
# 7 Safety on UA-22); the other numbers follow the order of the PLAXIS list (to confirm on PLAXIS, QA/QC)
CALC_TYPE_CODES = {1: "K0 procedure", 2: "Gravity loading", 3: "Field stress", 4: "Plastic", 5: "Consolidation",
                   6: "Fully coupled flow-deformation", 7: "Safety", 8: "Dynamic", 9: "Dynamic with consolidation"}


def is_safety_phase(ph: dict) -> bool:
    """A phase of the read model (dict) that is a safety phase, by its type (name or PLAXIS number) or its name."""
    return "safety" in f"{calc_type_name(ph.get('type', ''))} {ph.get('id', '')}".lower()


def calc_type_name(x) -> str:
    """The calculation type of a phase as a name, whether PLAXIS gives the name or its number."""
    v = _v(x)
    t = str(v if v is not None else "").strip()
    if isinstance(v, bool):
        return t
    if isinstance(v, (int, float)) or t.isdigit():
        try:
            return CALC_TYPE_CODES.get(int(float(t)), t)
        except ValueError:
            return t
    return t


def _num(x):
    try:
        v = float(_v(x))
        return v if math.isfinite(v) else None
    except Exception:  # noqa: BLE001
        return None


def _name(o) -> str:
    try:
        return str(_v(o.Name))
    except Exception:  # noqa: BLE001
        return str(o)


def _list(g, attr: str) -> list:
    try:
        return list(getattr(g, attr))
    except Exception:  # noqa: BLE001
        return []


def _xyz(p, is_3d: bool):
    if isinstance(p, (tuple, list)) and len(p) >= 2:
        vals = [float(c) for c in p]
        return [vals[0], vals[1], vals[2] if len(vals) > 2 else 0.0]
    x, y = _num(getattr(p, "x", None)), _num(getattr(p, "y", None))
    if x is None or y is None:
        return None
    return [x, y, (_num(getattr(p, "z", None)) or 0.0) if is_3d else 0.0]


def read_boreholes(g_i, is_3d: bool) -> list[dict]:
    """The boreholes and their soil layers (top, bottom, material), read in Soil mode."""
    out = []
    holes = _list(g_i, "Boreholes")
    layers = _list(g_i, "Soillayers")
    for bi, bh in enumerate(holes):
        rec = {"name": _name(bh), "x": _num(bh.x) or 0.0, "y": (_num(getattr(bh, "y", None)) or 0.0) if is_3d else 0.0,
               "head": _num(getattr(bh, "Head", None)), "layers": []}
        for li, layer in enumerate(layers):
            try:
                zone = list(layer.Zones)[bi]
            except Exception:  # noqa: BLE001
                continue
            top = bot = None
            for t in (lambda z: z.Top, lambda z: z.Top.z, lambda z: z.Top.y):
                try:
                    top = _num(t(zone))
                    if top is not None:
                        break
                except Exception:  # noqa: BLE001
                    continue
            for b in (lambda z: z.Bottom, lambda z: z.Bottom.z, lambda z: z.Bottom.y):
                try:
                    bot = _num(b(zone))
                    if bot is not None:
                        break
                except Exception:  # noqa: BLE001
                    continue
            soil_mat = ""
            try:
                soil_mat = _name(_v(layer.Soil.Material))
            except Exception:  # noqa: BLE001
                pass
            if top is not None and bot is not None:
                rec["layers"].append({"name": _name(layer), "material": soil_mat, "top": top, "bottom": bot})
        out.append(rec)
    return out


def _obj_points(obj, is_3d: bool) -> list:
    """Corner points of a line / polygon / surface, whatever this PLAXIS version exposes."""
    for get in (lambda o: [o.First, o.Second], lambda o: list(_v(o.Points)), lambda o: list(o)):
        try:
            pts = [_xyz(q, is_3d) for q in get(obj)]
            pts = [q for q in pts if q]
            if len(pts) >= 2:
                return pts
        except Exception:  # noqa: BLE001
            continue
    return []


# QA V26.1.9 re-QA N1: PLAXIS 3D gives the points of a surface (a plate, an interface, a surface load) in the
# surface's own local frame: UA-22's wall came back as (0, 0, 0) - (0, 12, 13.5) instead of x = 20, y 0 to -12, below
# the ground. The points are turned into model coordinates through the surface's origin and local axes; where PLAXIS
# gives more than one reading, the one that meets the anchors / beams and lies in the ground is kept.
ORIGIN_NAMES = ("LocalOrigin", "Origin", "LocalOriginPoint", "Position")
AXIS_NAMES = (("LocalAxis1", "LocalAxis2"), ("Axis1", "Axis2"), ("LocalXAxis", "LocalYAxis"), ("AxisX", "AxisY"))


def _vec(o, name):
    """A vector property of a PLAXIS object (an origin, an axis): an object with x, y, z, a tuple, or name + x / y / z."""
    try:
        v = _xyz(_v(getattr(o, name)), True)
        if v and len(v) == 3:
            return v
    except Exception:  # noqa: BLE001
        pass
    comps = []
    for c in "xyz":
        val = None
        for n in (name + c, name + c.upper(), f"{name}_{c}", f"{name}_{c.upper()}"):
            try:
                val = _num(getattr(o, n))
            except Exception:  # noqa: BLE001
                val = None
            if val is not None:
                break
        comps.append(val)
    return comps if all(c is not None for c in comps) else None


def _frame(o):
    """(origin, axis 1, axis 2) of a PLAXIS 3D surface, or None when this PLAXIS gives none."""
    try:
        names = [n for n in dir(o) if isinstance(n, str)]
    except Exception:  # noqa: BLE001
        names = []
    origins = list(ORIGIN_NAMES) + [n for n in names if "origin" in n.lower() and n not in ORIGIN_NAMES]
    pairs = list(AXIS_NAMES)
    found = sorted(n for n in names if "axis" in n.lower() and n[-1:] in "12")
    if len(found) >= 2:
        pairs.append((found[0], found[1]))
    org = next((v for v in (_vec(o, n) for n in origins) if v), None)
    if org is None:
        return None
    for a, b in pairs:
        a1, a2 = _vec(o, a), _vec(o, b)
        if a1 and a2 and any(a1) and any(a2):
            return org, a1, a2
    return None


def _unit(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return [c / n for c in v]


def _to_global(pts: list, frame) -> list[list]:
    """The local points of a surface in model coordinates, in the two ways PLAXIS may order them (u, v, normal) or
    (normal, u, v)."""
    o, a1, a2 = frame
    a1, a2 = _unit(a1), _unit(a2)
    a3 = [a1[1] * a2[2] - a1[2] * a2[1], a1[2] * a2[0] - a1[0] * a2[2], a1[0] * a2[1] - a1[1] * a2[0]]
    out = []
    for order in ((0, 1, 2), (1, 2, 0)):
        g = []
        for q in pts:
            q = (list(q) + [0.0, 0.0])[:3]
            u, v, w = q[order[0]], q[order[1]], q[order[2]]
            g.append([round(o[i] + u * a1[i] + v * a2[i] + w * a3[i], 6) for i in range(3)])
        out.append(g)
    return out


def _surface_points(o, parent, is_3d: bool) -> list[list]:
    """Every reading of an object's points in model coordinates (3D: through the surface's local frame too); the
    first is the default, the right one is chosen once the whole model is read (_resolve_globals)."""
    raw = _obj_points(parent, is_3d)
    if not is_3d or len(raw) < 3:  # a line (anchor, beam) is given in model coordinates
        return [raw]
    cands = []
    for src in (parent, o):
        pts = raw if src is parent else (_obj_points(o, True) if o is not parent else [])
        if not pts:
            continue
        org = _xyz(src, True)  # re-test N1 (UA-22): the Polygon's own x, y, z is where its local points start
        if org and any(org):
            cands.append([[round(q[i] + org[i], 6) for i in range(3)] for q in pts])
        fr = _frame(src)
        if fr:
            cands += _to_global(pts, fr)
        cands.append(pts)
    uniq = []
    for c in cands:
        if c and c not in uniq:
            uniq.append(c)
    return uniq or [raw]


def _plane_hits(poly: list, ends: list, tol: float = 0.05) -> int:
    """How many line ends (anchors, beams) lie on the surface (its plane and its box)."""
    if len(poly) < 3:
        return 0
    p0 = poly[0]
    n = None
    for i in range(1, len(poly) - 1):
        a = [poly[i][k] - p0[k] for k in range(3)]
        b = [poly[i + 1][k] - p0[k] for k in range(3)]
        c = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
        if math.sqrt(sum(x * x for x in c)) > 1e-9:
            n = _unit(c)
            break
    if n is None:
        return 0
    lo = [min(q[k] for q in poly) - tol for k in range(3)]
    hi = [max(q[k] for q in poly) + tol for k in range(3)]
    hits = 0
    for e in ends:
        if abs(sum((e[k] - p0[k]) * n[k] for k in range(3))) <= tol and all(lo[k] <= e[k] <= hi[k] for k in range(3)):
            hits += 1
    return hits


def _cog(o, parent):
    """PLAXIS' centre of gravity of a surface (Polygon.CenterOfGravity), the check of its reading (re-test N1)."""
    for src in (parent, o):
        for n in ("CenterOfGravity", "CentreOfGravity", "COG"):
            try:
                v = _vec(src, n)
            except Exception:  # noqa: BLE001
                v = None
            if v:
                return v
    return None


def _centre(pts: list) -> list:
    """The centre of a polygon (area-weighted, in its plane; the mean of its corners when degenerate)."""
    mean = [sum(q[k] for q in pts) / len(pts) for k in range(3)]
    if len(pts) < 3:
        return mean
    tot, acc = 0.0, [0.0, 0.0, 0.0]
    p0 = pts[0]
    for i in range(1, len(pts) - 1):
        a = [pts[i][k] - p0[k] for k in range(3)]
        b = [pts[i + 1][k] - p0[k] for k in range(3)]
        cr = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
        ar = math.sqrt(sum(x * x for x in cr)) / 2
        c = [(p0[k] + pts[i][k] + pts[i + 1][k]) / 3 for k in range(3)]
        tot += ar
        acc = [acc[k] + ar * c[k] for k in range(3)]
    return [acc[k] / tot for k in range(3)] if tot > 1e-12 else mean


def _resolve_globals(m: dict) -> None:
    """N1: of the readings of each surface, the one that meets the anchors / beams and lies in the ground."""
    ends = [list(map(float, (list(q) + [0.0])[:3])) for st in m["structures"] if len(st.get("points") or []) == 2
            and not st.get("_alts") for q in st["points"]]
    ends += [[p["x"], p["y"], p.get("z", 0.0)] for p in m["points"]]
    top = max((lay["top"] for b in m["boreholes"] for lay in b.get("layers") or []), default=None)
    flagged = []
    for coll in ("structures", "interfaces", "loads", "contractions", "displacements"):
        for it in m.get(coll) or []:
            alts = it.pop("_alts", None)
            cog = it.pop("_cog", None)
            if not alts:
                continue

            def score(c):
                if cog and len(c) >= 3 and math.dist(_centre(c), cog) < 0.05:  # PLAXIS' own centre of gravity
                    return 1e6
                if top is None:
                    return _plane_hits(c, ends)
                if coll == "loads":  # a surface load lies on the ground
                    return -abs(max(q[2] for q in c) - top)
                return 2 * _plane_hits(c, ends) - sum(1 for q in c if q[2] > top + 0.5)
            best = max(alts, key=lambda c: (score(c), -alts.index(c)))
            it["points"] = best
            if top is not None and coll in ("structures", "interfaces") and len(best) >= 3 and \
                    (min(q[2] for q in best) + max(q[2] for q in best)) / 2 > top:  # its mid level above the ground
                flagged.append(it["name"])
    if flagged:
        m["notes"].append("Read above the ground (their points may be in their surface's local coordinates): "
                          + ", ".join(flagged[:8]) + ". The view may place them wrong; please tell the QA/QC.")


def soil_contour(g_i):
    """The model's soil contour (xmin, xmax, ymin, ymax) from PLAXIS, or None (QA V26.1.9 D7: the soil volume, not
    the points and structures, says whether a result point is in the model)."""
    try:
        c = g_i.SoilContour
    except Exception:  # noqa: BLE001
        return None
    vals = {}
    for key in ("xmin", "xmax", "ymin", "ymax"):
        for n in (key, key.capitalize(), key[0].upper() + key[1:3] + key[3:].capitalize(), key.upper()):
            try:
                v = _num(getattr(c, n))
            except Exception:  # noqa: BLE001
                v = None
            if v is not None:
                vals[key] = v
                break
    if len(vals) == 4 and vals["xmax"] > vals["xmin"] and vals["ymax"] > vals["ymin"]:
        return vals
    pts = _obj_points(c, True)
    if len(pts) >= 3:
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        if max(xs) > min(xs) and max(ys) > min(ys):
            return {"xmin": min(xs), "xmax": max(xs), "ymin": min(ys), "ymax": max(ys)}
    return None


def _kind_of_material(m) -> str:
    t = (str(getattr(m, "_plx_type", "")) + " " + str(_v(getattr(m, "TypeName", "")) or "")).lower()
    for key, kind in (("soil", "soil"), ("plate", "plate"), ("embedded", "embedded_beam"), ("anchor", "anchor"),
                      ("cable", "cable"), ("geogrid", "geogrid"), ("beam", "beam")):
        if key in t:
            return kind
    return "soil" if _num(getattr(m, "phi", None)) is not None else "plate"


def extract(s_i, g_i) -> dict:
    """Everything the optimization needs, from the project open in PLAXIS Input."""
    try:
        is_3d = bool(getattr(s_i, "is_3d"))
    except Exception:  # noqa: BLE001
        is_3d = any(_num(getattr(p, "z", None)) not in (None, 0.0) for p in _list(g_i, "Points")[:50])
    version = "3d" if is_3d else "2d"
    notes = []
    m = {"version": version, "source": "plaxis", "name": "", "points": [], "lines": [], "soil_polygons": [],
         "boreholes": [], "structures": [], "loads": [], "materials": [], "phases": [], "water": [],
         "interfaces": [], "tunnels": [], "contractions": [], "wells": [], "drains": [], "clusters": [],
         "displacements": [], "mesh": {"level": "Medium"}, "notes": notes}
    try:
        m["product"] = f"{s_i.name} {s_i.major_version}.{s_i.minor_version}"
    except Exception:  # noqa: BLE001
        m["product"] = "PLAXIS 3D" if is_3d else "PLAXIS 2D"
    try:
        m["name"] = str(_v(g_i.Project.Title)) or ""
    except Exception:  # noqa: BLE001
        pass
    try:  # the project's units (Project properties › Model › Units): every value read below is in these units
        from .units import from_plaxis
        pr = g_i.Project
        m["units"] = from_plaxis(_v(getattr(pr, "UnitLength", None)), _v(getattr(pr, "UnitForce", None)),
                                 _v(getattr(pr, "UnitTime", None)))
    except Exception:  # noqa: BLE001
        pass
    try:
        g_i.gotostructures()
    except Exception:  # noqa: BLE001
        pass

    for p in _list(g_i, "Points"):
        c = _xyz(p, is_3d)
        if c:
            m["points"].append({"name": _name(p), "x": c[0], "y": c[1], "z": c[2]})
    for ln in _list(g_i, "Lines"):
        try:
            m["lines"].append({"name": _name(ln), "a": _name(ln.First), "b": _name(ln.Second)})
        except Exception:  # noqa: BLE001
            continue

    for mat in _list(g_i, "Materials"):
        kind = _kind_of_material(mat)
        props = {}
        for prop, *_ in MAT_PROPS.get(kind, []):
            val = _num(getattr(mat, prop, None))
            if val is not None:
                props[prop] = val
        for prop, val in _numeric_attrs(mat).items():
            props.setdefault(prop, val)
        ident = str(_v(getattr(mat, "Identification", "")) or "")
        model = ""
        for attr in ("SoilModel", "MaterialType", "Elasticity"):
            try:
                model = str(_v(getattr(mat, attr)))
                if model:
                    break
            except Exception:  # noqa: BLE001
                continue
        rec = {"name": _name(mat), "id": ident or _name(mat), "kind": kind, "props": props, "model": model}
        if kind == "soil":  # V26.1.9: the drainage type (Drained, Undrained A / B / C, Non-porous) is a variable
            try:
                rec["drainage"] = str(_v(mat.DrainageType))
            except Exception:  # noqa: BLE001
                pass
        m["materials"].append(rec)
    mat_by = {x["name"]: x for x in m["materials"]}

    def mat_of(obj):
        try:
            return _name(_v(obj.Material))
        except Exception:  # noqa: BLE001
            return ""

    for group, kind in (("Plates", "plate"), ("NodeToNodeAnchors", "anchor"), ("FixedEndAnchors", "anchor"),
                        ("Geogrids", "geogrid"), ("EmbeddedBeams", "embedded_beam"), ("EmbeddedBeamRows", "embedded_beam"),
                        ("Beams", "beam"), ("Cables", "cable")):  # V26.1.9: cables (PLAXIS 3D 2023.2+)
        for o in _list(g_i, group):
            parent = getattr(o, "Parent", o)
            alts = _surface_points(o, parent, is_3d)
            m["structures"].append({"kind": kind, "name": _name(o), "material": mat_of(o), "points": alts[0],
                                    **({"_alts": alts, "_cog": _cog(o, parent)} if len(alts) > 1 or (is_3d and len(alts[0]) >= 3) else {})})
    # V26.1.9 (QA/QC missing features, P1): interfaces, tunnels, contractions, wells and drains
    for group, side in (("PositiveInterfaces", "positive"), ("NegativeInterfaces", "negative"), ("Interfaces", "")):
        for o in _list(g_i, group):
            if any(x["name"] == _name(o) for x in m["interfaces"]):
                continue
            parent = getattr(o, "Parent", o)
            mode = ""
            try:
                mode = str(_v(o.MaterialMode))
            except Exception:  # noqa: BLE001
                pass
            alts = _surface_points(o, parent, is_3d)
            m["interfaces"].append({"name": _name(o), "side": side, "mode": mode, "material": mat_of(o),
                                    "points": alts[0], **({"_alts": alts, "_cog": _cog(o, parent)} if is_3d else {})})
    for o in _list(g_i, "Tunnels"):
        m["tunnels"].append({"name": _name(o)})
    for group in ("LineContractions", "SurfaceContractions", "Contractions"):
        for o in _list(g_i, group):
            parent = getattr(o, "Parent", o)
            alts = _surface_points(o, parent, is_3d)
            m["contractions"].append({"name": _name(o), "value": _num(getattr(o, "Cref", None)),
                                      "points": alts[0], **({"_alts": alts, "_cog": _cog(o, parent)} if is_3d else {})})
    for group, coll, props in (("Wells", "wells", WELL_PROPS), ("Drains", "drains", DRAIN_PROPS)):
        for o in _list(g_i, group):
            parent = getattr(o, "Parent", o)
            prop = next((pr for pr in props if _num(getattr(o, pr, None)) is not None), props[0])
            m[coll].append({"name": _name(o), "prop": prop, "value": _num(getattr(o, prop, None)),
                            "points": _obj_points(parent, is_3d)})
    for group, kind in DISPL_GROUPS:  # V26.1.9 (P2): prescribed displacements and their values
        for o in _list(g_i, group):
            props = {k: val for k, val in _numeric_attrs(o).items() if re.fullmatch(r"(?i)u[xyz](_start|_end|_ref)?", k)}
            parent = getattr(o, "Parent", o)
            alts = _surface_points(o, parent, is_3d)
            m["displacements"].append({"name": _name(o), "kind": kind, "props": props, "points": alts[0],
                                       **({"_alts": alts, "_cog": _cog(o, parent)} if is_3d else {})})
    for group, kind in (("LineLoads", "line"), ("PointLoads", "point"), ("SurfaceLoads", "surface")):
        for o in _list(g_i, group):
            props = {}
            for prop, _ in LOAD_PROPS[kind]:
                val = _num(getattr(o, prop, None))
                if val is not None:
                    props[prop] = val
            parent = getattr(o, "Parent", o)
            alts = _surface_points(o, parent, is_3d) if kind == "surface" else []
            m["loads"].append({"kind": kind, "name": _name(o), "props": props,
                               "points": alts[0] if alts else _obj_points(parent, is_3d) if kind != "point" else
                               [q for q in [_xyz(parent, is_3d)] if q], **({"_alts": alts, "_cog": _cog(o, parent)} if len(alts) > 1 else {})})

    # stratigraphy: boreholes and soil layer levels (Soil mode)
    try:
        g_i.gotosoil()
    except Exception:  # noqa: BLE001
        pass
    holes = _list(g_i, "Boreholes")
    m["boreholes"] += read_boreholes(g_i, is_3d)
    _resolve_globals(m)  # N1: after the boreholes (the ground level is known)
    sc = soil_contour(g_i)
    if sc:
        m["contour"] = sc
    if not holes:
        notes.append("No boreholes found: the soil layers come from the soil polygons only.")

    for sp in _list(g_i, "SoilPolygons") if not is_3d else []:
        pts = _obj_points(sp, False) or _obj_points(getattr(sp, "Parent", sp), False)
        if len(pts) >= 3:
            m["soil_polygons"].append({"name": _name(sp), "material": mat_of(sp), "points": [[p[0], p[1]] for p in pts]})

    try:
        g_i.gotostages()
    except Exception:  # noqa: BLE001
        pass
    phases = _list(g_i, "Phases")
    for ph in phases:
        settings = {}
        for prop in ("TimeInterval", "MaxStepsStored"):
            val = _num(getattr(ph, prop, None))
            if val is not None:
                settings[prop] = val
        deform = getattr(ph, "Deform", None)
        for prop in ("ToleratedError", "MaxSteps", "MaxIterations", "MaxUnloadingSteps", "MaxLoadFractionPerStep"):
            val = _num(getattr(deform, prop, None)) if deform is not None else None
            if val is not None:
                settings["Deform." + prop] = val
        m["phases"].append({"name": _name(ph), "id": str(_v(getattr(ph, "Identification", "")) or _name(ph)),
                            "type": calc_type_name(getattr(ph, "DeformCalcType", "")), "settings": settings,
                            "pore": str(_v(getattr(ph, "PorePresCalcType", "")) or ""),
                            "thermal": str(_v(getattr(ph, "ThermalCalcType", "")) or "")})
    staged = []
    for group, kind in (("LineLoads", "line"), ("PointLoads", "point"), ("SurfaceLoads", "surface")):
        for o in _list(g_i, group):
            for ph in phases[1:]:
                try:
                    if getattr(o, "Active")[ph] is False or _v(getattr(o, "Active")[ph]) is False:
                        continue
                except Exception:  # noqa: BLE001
                    pass
                for prop, _ in LOAD_PROPS[kind]:
                    val = _staged(o, prop, ph)
                    if val is not None and val != 0:
                        staged.append({"object": _name(o), "prop": prop, "phase": _name(ph), "value": val,
                                       "unit": LOAD_UNIT.get((version, kind), ""), "kind": kind})
    for group in ("NodeToNodeAnchors", "FixedEndAnchors", "Cables"):
        for o in _list(g_i, group):
            for ph in phases[1:]:
                try:
                    if not _v(getattr(o, "AdjustPrestress")[ph]):
                        continue
                except Exception:  # noqa: BLE001
                    continue
                val = _staged(o, "PrestressForce", ph)
                if val is not None:
                    staged.append({"object": _name(o), "prop": "PrestressForce", "phase": _name(ph), "value": val,
                                   "unit": "kN", "kind": "anchor"})
    for group, kind, props in (("Wells", "well", WELL_PROPS), ("Drains", "drain", DRAIN_PROPS),
                               ("LineContractions", "contraction", ("Cref",)), ("SurfaceContractions", "contraction", ("Cref",)),
                               ("Contractions", "contraction", ("Cref",))):
        for o in _list(g_i, group):
            for ph in phases[1:]:
                try:
                    if not _v(getattr(o, "Active")[ph]):
                        continue
                except Exception:  # noqa: BLE001
                    continue
                for prop in props:
                    val = _staged(o, prop, ph)
                    if val is not None:
                        staged.append({"object": _name(o), "prop": prop, "phase": _name(ph), "value": val,
                                       "unit": {"well": "m³/day", "drain": "m", "contraction": "%"}[kind], "kind": kind})
                        break
    m["staged"] = staged
    # V26.1.9: the stages where each structure is active (prestress of an anchor row is offered from its first one)
    for st in m["structures"]:
        act = []
        for group in STRUCT_GROUPS.get(st["kind"], ()):
            for o in _list(g_i, group):
                n = _name(o)
                if n != st["name"] and not n.startswith(st["name"] + "_"):
                    continue
                for ph in phases:
                    try:
                        if _v(getattr(o, "Active")[ph]) and _name(ph) not in act:
                            act.append(_name(ph))
                    except Exception:  # noqa: BLE001
                        continue
        if act:
            order = [_name(ph) for ph in phases]
            st["active"] = sorted(act, key=order.index)
    # V26.1.9: the soil clusters of Staged construction (their water conditions per stage: dry, interpolate...)
    m["clusters"] = [_name(o) for o in _list(g_i, "Soils")][:400]
    for wl in _list(g_i, "WaterLevels"):
        pts = _obj_points(wl, is_3d)
        if pts:
            m["water"].append({"name": _name(wl), "points": pts})
    if not m["points"]:
        notes.append("No geometry points were returned. Is a project open in PLAXIS Input?")
    finish(m)
    unknown = [x["name"] for x in m["materials"] if not mat_by.get(x["name"], {}).get("props")]
    if unknown:
        notes.append("No parameters could be read for material(s) " + ", ".join(unknown[:8]) + ".")
    return m


# ---------------------------------------------------------------------------------------------------------------
# helpers shared by every source
def vert(p: dict, version: str) -> float:
    return p["z"] if version == "3d" else p["y"]


def finish(m: dict) -> dict:
    """Extents, the axis names and counts; also tidies an uploaded / older JSON."""
    v = m.get("version") if m.get("version") in ("2d", "3d") else "2d"
    m["version"] = v
    for k in ("points", "lines", "soil_polygons", "boreholes", "structures", "loads", "materials", "phases", "water",
              "interfaces", "tunnels", "contractions", "wells", "drains", "clusters", "displacements"):
        m.setdefault(k, [])
    m.setdefault("mesh", {"level": "Medium"})
    m.setdefault("notes", [])
    m.setdefault("staged", [])
    xs, ys, zs = [], [], []
    for p in m["points"]:
        xs.append(p["x"]), ys.append(p["y"]), zs.append(p.get("z", 0.0))
    for s in m["structures"] + m["loads"] + m["water"]:
        for q in s.get("points") or []:
            xs.append(q[0]), ys.append(q[1]), zs.append(q[2] if len(q) > 2 else 0.0)
    for poly in m["soil_polygons"]:
        for q in poly["points"]:
            xs.append(q[0]), ys.append(q[1])
    sc = m.get("contour") or {}
    if sc.get("xmax") is not None:  # re-QA N1 / D7: the soil contour bounds the model
        xs += [sc["xmin"], sc["xmax"]]
        if v == "3d":
            ys += [sc["ymin"], sc["ymax"]]
    for b in m["boreholes"]:
        xs.append(b["x"])
        for lay in b["layers"]:
            (zs if v == "3d" else ys).extend([lay["top"], lay["bottom"]])
    if not xs:
        xs, ys, zs = [0, 10], [0, 10], [0, 0]
    m["extents"] = {"xmin": min(xs), "xmax": max(xs), "ymin": min(ys), "ymax": max(ys),
                    "zmin": min(zs or [0]), "zmax": max(zs or [0])}
    m["axes"] = ["x", "y", "z"] if v == "3d" else ["x", "y"]
    m["vertical"] = "z" if v == "3d" else "y"
    m["counts"] = {k: len(m[k]) for k in ("points", "lines", "soil_polygons", "boreholes", "structures", "loads",
                                          "materials", "phases")}
    return m


def from_json(text: str) -> dict:
    """A model.json written by this script (run with PLAXIS' Python)."""
    try:
        m = json.loads(text)
    except ValueError as e:
        raise ValueError(f"Not a model file from plaxis_model.py: {e}") from e
    if not isinstance(m, dict) or not isinstance(m.get("points"), list):
        raise ValueError("Not a model file from plaxis_model.py (no 'points' list).")
    m["source"] = "file"
    return finish(m)


# ---------------------------------------------------------------------------------------------------------------
# 3. Demo model (simulated solver): an anchored diaphragm-wall excavation, as PLAXIS would return it
def demo(version: str = "2d") -> dict:
    is3 = version == "3d"
    W, H, xw, dig, toe = 60.0, 30.0, 20.0, 10.0, 18.0
    L = 20.0  # 3D: model length along y

    def P(name, x, v, y=0.0):
        return {"name": name, "x": x, "y": y if is3 else v, "z": v if is3 else 0.0}

    pts = [P("Point_1", 0, 0), P("Point_2", W, 0), P("Point_3", W, -H), P("Point_4", 0, -H),
           P("Point_5", xw, 0), P("Point_6", xw, -toe), P("Point_7", xw, -2.0), P("Point_8", xw + 12, -8.0),
           P("Point_9", xw + 16, -10.5), P("Point_10", 0, -dig), P("Point_11", xw, -dig), P("Point_12", xw + 2, 0),
           P("Point_13", xw + 12, 0)]
    if is3:
        pts += [{**p, "name": f"Point_{14 + i}", "y": L} for i, p in enumerate(pts[4:7])]

    def c(name):
        p = next(q for q in pts if q["name"] == name)
        return [p["x"], p["y"], p["z"]]

    layers = [("Fill", "Fill", 0.0, -3.0), ("Clay", "Clay", -3.0, -12.0), ("Sand", "Sand", -12.0, -H)]
    holes = [{"name": "Borehole_1", "x": 0.0, "y": 0.0, "head": -4.0,
              "layers": [{"name": f"Soillayer_{i}", "material": mat, "top": t, "bottom": b}
                         for i, (_, mat, t, b) in enumerate(layers, 1)]}]
    polys = []
    if not is3:
        for i, (_, mat, t, b) in enumerate(layers, 1):
            polys.append({"name": f"Soil_{i}", "material": mat, "points": [[0, t], [W, t], [W, b], [0, b]]})
        polys.append({"name": "Soil_4_excavation", "material": "Fill", "excavated": True,
                      "points": [[0, 0], [xw, 0], [xw, -dig], [0, -dig]]})

    def surf(a, b):  # 3D: wall / load as a surface along y
        return [a, b, [b[0], L, b[2]], [a[0], L, a[2]]]

    wall = surf(c("Point_5"), c("Point_6")) if is3 else [c("Point_5"), c("Point_6")]
    structures = [{"kind": "plate", "name": "Plate_1", "material": "DWall", "points": wall},
                  {"kind": "anchor", "name": "NodeToNodeAnchor_1", "material": "Anchor", "points": [c("Point_7"), c("Point_8")]},
                  {"kind": "geogrid", "name": "Geogrid_1", "material": "Grout", "points": [c("Point_8"), c("Point_9")]}]
    if is3:
        loads = [{"kind": "surface", "name": "SurfaceLoad_1_1", "props": {"sigz": -10.0},
                  "points": [c("Point_12"), c("Point_13"), [xw + 12, L, 0], [xw + 2, L, 0]]}]
    else:
        loads = [{"kind": "line", "name": "LineLoad_1_1", "props": {"qy_start": -10.0},
                  "points": [c("Point_12"), c("Point_13")]}]
    mats = [{"name": "Fill", "id": "Fill", "kind": "soil", "model": "Hardening soil", "props": {"gammaUnsat": 17, "gammaSat": 19, "E50ref": 20000,
                                                                     "Eoedref": 20000, "Eurref": 60000, "cref": 1, "phi": 30, "psi": 0}},
            {"name": "Clay", "id": "Clay", "kind": "soil", "model": "Hardening soil", "props": {"gammaUnsat": 16, "gammaSat": 18, "E50ref": 12000,
                                                                     "Eoedref": 8000, "Eurref": 36000, "cref": 10, "phi": 25, "psi": 0}},
            {"name": "Sand", "id": "Sand", "kind": "soil", "model": "Hardening soil", "props": {"gammaUnsat": 18, "gammaSat": 20, "E50ref": 40000,
                                                                     "Eoedref": 40000, "Eurref": 120000, "cref": 0.5, "phi": 34, "psi": 4}},
            {"name": "DWall", "id": "Diaphragm wall", "kind": "plate",
             "props": ({"E1": 3.0e7, "d": 0.8, "Gamma": 25} if is3 else {"EA": 2.4e7, "EI": 1.28e6, "d": 0.8, "w": 8.0})},
            {"name": "Anchor", "id": "Anchor rod", "kind": "anchor", "props": {"EA": 2.5e5, "Lspacing": 2.5}},
            {"name": "Grout", "id": "Grout body", "kind": "geogrid", "props": {"EA": 1.0e5}}]
    phases = [{"name": "InitialPhase", "id": "Initial phase", "type": "K0 procedure"},
              {"name": "Phase_1", "id": "Wall + surcharge", "type": "Plastic"},
              {"name": "Phase_2", "id": "Excavation to -3 m", "type": "Plastic"},
              {"name": "Phase_3", "id": "Anchor + prestress", "type": "Plastic"},
              {"name": "Phase_4", "id": "Excavation to -10 m", "type": "Plastic"},
              {"name": "Phase_5", "id": "Safety", "type": "Safety"}]
    for ph in phases:
        ph["settings"] = {"TimeInterval": 0.0, "Deform.ToleratedError": 0.01, "Deform.MaxSteps": 1000.0}
    lname, lprop = ("SurfaceLoad_1_1", "sigz") if is3 else ("LineLoad_1_1", "qy_start")
    staged = [{"object": lname, "prop": lprop, "phase": f"Phase_{i}", "value": -10.0, "kind": "load",
               "unit": "kN/m²" if is3 else "kN/m/m"} for i in (1, 2, 3, 4)]
    staged.append({"object": "NodeToNodeAnchor_1", "prop": "PrestressForce", "phase": "Phase_3", "value": 300.0,
                   "unit": "kN", "kind": "anchor"})
    water = [{"name": "GeneralWaterLevel", "points": ([[0, 0, -4], [W, 0, -4], [W, L, -4], [0, L, -4]] if is3
                                                      else [[0, -4, 0], [W, -4, 0]])}]
    lines = [{"name": "Line_1", "a": "Point_5", "b": "Point_6"}, {"name": "Line_2", "a": "Point_7", "b": "Point_8"},
             {"name": "Line_3", "a": "Point_8", "b": "Point_9"}, {"name": "Line_4", "a": "Point_12", "b": "Point_13"},
             {"name": "Line_5", "a": "Point_10", "b": "Point_11"}]
    for st in structures:  # V26.1.9: the stages where each structure is active
        st["active"] = [f"Phase_{i}" for i in ((3, 4, 5) if st["kind"] in ("anchor", "geogrid") else (1, 2, 3, 4, 5))]
    for mat in mats:
        if mat["kind"] == "soil":
            mat["drainage"] = "Undrained (A)" if mat["name"] == "Clay" else "Drained"
            mat["props"].setdefault("Rinter", 0.67 if mat["name"] != "Sand" else 0.7)
    interfaces = [{"name": f"{side.capitalize()}Interface_1", "side": side, "mode": "From adjacent soil", "material": "",
                   "points": [list(q) for q in wall]} for side in ("positive", "negative")]
    base = [[0, -dig - 0.5, 0], [xw, -dig - 0.5, 0]] if not is3 else [[0, 0, -dig - 0.5], [xw, 0, -dig - 0.5], [xw, L, -dig - 0.5], [0, L, -dig - 0.5]]
    drains = [{"name": "Drain_1", "prop": "h", "value": -dig - 0.5, "points": base}]
    m = {"version": version, "source": "demo", "name": "Example anchored excavation",
         "product": "PLAXIS 3D (example)" if is3 else "PLAXIS 2D (example)", "points": pts, "lines": lines,
         "soil_polygons": polys, "boreholes": holes, "structures": structures, "loads": loads, "materials": mats,
         "phases": phases, "water": water, "mesh": {"level": "Medium"}, "staged": staged,
         "interfaces": interfaces, "drains": drains, "wells": [], "tunnels": [], "contractions": [],
         "clusters": [] if is3 else [f"Soil_{i}_1" for i in range(1, 5)],
         "contour": {"xmin": 0.0, "xmax": W, "ymin": 0.0, "ymax": L if is3 else 0.0},
         "notes": ["Example model: PLAXIS is not connected (simulated solver). Connect PLAXIS in Settings, or upload "
                   "model.json made by plaxis_model.py, to read your own project."]}
    return finish(m)


# ---------------------------------------------------------------------------------------------------------------
# What can be optimized in this model
def _point_names(m: dict, coords: list) -> list[str]:
    """Names of the model points at the given coordinates (corners of a structure / load)."""
    out = []
    for q in coords or []:
        for p in m["points"]:
            if abs(p["x"] - q[0]) < 1e-6 and abs(p["y"] - q[1]) < 1e-6 and \
                    abs(p.get("z", 0.0) - (q[2] if len(q) > 2 else 0.0)) < 1e-6:
                if p["name"] not in out:
                    out.append(p["name"])
                break
    return out


AREA_REF = __import__("re").compile(r"^area:([xyz])(min|max)$|^corner(\d+):([xyz])$|^areasize(\d+)$")


def area_edit(prop: str, val: float):
    """The edit of a load area that a PLAXIS reference asks for: Load.area:xmax, Load.corner3:y, Load.areasize1 (the
    whole area, in m² (3D) or its length in m (2D), scaled from corner 1). None when it is not an area reference."""
    mm = AREA_REF.match(prop)
    if not mm:
        return None
    if mm.group(1):
        return (mm.group(1), mm.group(2), val)
    if mm.group(3):
        return ("corner", int(mm.group(3)), mm.group(4), val)
    return ("size", int(mm.group(5)), val)


def reshape(corners: list, edits: list[tuple]) -> list[list[float]]:
    """New corners of a load area. edits: ("x", "min", v) puts the edge at the smallest x at v (every corner on that
    edge moves: the area is reduced or extended), ("corner", i, "y", v) puts corner i (from 1) at y = v."""
    new = [list(map(float, q)) + [0.0] * (3 - len(q)) for q in corners]
    old = [q[:] for q in new]
    for e in edits:
        if e[0] == "size":  # the whole area (m²) or length (m): scaled from one corner, which stays
            _, k, v = e
            a = new[min(max(k, 1), len(new)) - 1][:]
            plan = len(new) > 2
            now = area_of(new) if plan else math.dist(new[0][:2], new[-1][:2])
            if now > 1e-12 and float(v) > 0:
                f = math.sqrt(float(v) / now) if plan else float(v) / now
                for q in new:
                    q[0], q[1] = a[0] + f * (q[0] - a[0]), a[1] + f * (q[1] - a[1])
            continue
        if e[0] == "corner":
            _, i, ax, v = e
            if 1 <= i <= len(new):
                new[i - 1]["xyz".index(ax)] = float(v)
            continue
        ax, side, v = e
        k = "xyz".index(ax)
        ref = (min if side == "min" else max)(q[k] for q in old)
        for q, q0 in zip(new, old):
            if abs(q0[k] - ref) < 1e-6:
                q[k] = float(v)
    return new


def load_corner_moves(m: dict, variables: list[dict], inputs: dict) -> dict[str, list[float]]:
    """The corners of the load areas that a run changes: {model point name or 'Load corner i': [x, y, z]}."""
    edits: dict[str, list] = {}
    for v in variables:
        if v["name"] not in inputs:
            continue
        try:
            val = float(inputs[v["name"]])
        except (TypeError, ValueError):
            continue
        for one in (v.get("plaxis_ref") or "").split(";"):
            name, _, prop = one.strip().rpartition(".")
            e = area_edit(prop, val)
            if e:
                edits.setdefault(name, []).append(e)
    out = {}
    for ld in m.get("loads") or []:
        if ld["name"] not in edits:
            continue
        old = ld.get("points") or []
        new = reshape(old, edits[ld["name"]])
        names = {}
        for i, q in enumerate(old, 1):
            hit = _point_names(m, [q])
            names[i] = hit[0] if hit else f"{ld['name']} corner {i}"
        for i, (q0, q) in enumerate(zip(old, new), 1):
            q0 = list(map(float, q0)) + [0.0] * (3 - len(q0))
            if any(abs(a - b) > 1e-9 for a, b in zip(q0, q)):
                out[names[i]] = [round(x, 6) for x in q]
    return out


def load_corners_at(m: dict, p: dict) -> list[tuple[str, int]]:
    """The surcharge loads that have a corner on this model point: [(load name, corner number from 1)]."""
    at = [float(p.get("x", 0)), float(p.get("y", 0)), float(p.get("z", 0) or 0)]
    out = []
    for ld in m.get("loads") or []:
        if ld.get("kind") == "point":
            continue
        for i, q in enumerate(ld.get("points") or [], 1):
            qq = [float(x) for x in q] + [0.0] * (3 - len(q))
            if all(abs(a - b) < 1e-6 for a, b in zip(at, qq)):
                out.append((ld["name"], i))
    return out


def area_of(corners: list) -> float:
    """Plan area of a load polygon (3D: x-y plane; 2D: the length in x)."""
    if len(corners) < 3:
        return abs(float(corners[-1][0]) - float(corners[0][0])) if corners else 0.0
    a = 0.0
    for p, q in zip(corners, corners[1:] + corners[:1]):
        a += float(p[0]) * float(q[1]) - float(q[0]) * float(p[1])
    return abs(a) / 2


def objects(m: dict) -> list[dict]:
    """Clickable things of the drawing: structures, loads and geometry lines, with the points they are made of."""
    out = []
    for st in m["structures"]:
        out.append({"name": st["name"], "kind": st["kind"], "material": st.get("material", ""),
                    "points": _point_names(m, st.get("points"))})
    for ld in m["loads"]:
        out.append({"name": ld["name"], "kind": f"{ld['kind']} load", "material": "",
                    "points": _point_names(m, ld.get("points"))})
    for ln in m["lines"]:
        if ln.get("a") and ln.get("b"):
            out.append({"name": ln["name"], "kind": "line", "material": "", "points": [ln["a"], ln["b"]]})
    out = [o for o in out if o["points"]]
    for o in out:
        o["title"] = describe(o)
    return out


def describe(o: dict) -> str:
    """'Line 3 (Point_11 – Point_12)': the line's number and the points it connects (structures alike)."""
    import re as _re
    pts = [p for p in o.get("points") or [] if p]
    name = o["name"]
    if o.get("kind") == "line":
        mm = _re.fullmatch(r"(?i)line_?(\d+)", name)
        name = f"Line {mm.group(1)}" if mm else name
    if 2 <= len(pts) <= 4:
        return f"{name} ({' – '.join(pts)})"
    return name


KIND_LABEL = {"plate": "Plate", "anchor": "Anchor", "geogrid": "Geogrid", "embedded_beam": "Embedded beam",
              "beam": "Beam", "soil": "Soil", "cable": "Cable"}


SOIL_MODELS = ["MC: 2", "HS: 3", "HSsmall: 4", "SSC: 6"]  # PLAXIS SoilModel codes (V26.1.8)
# V26.1.9 (P2): Soft Soil and Modified Cam-Clay from the same parameters; a Hoek-Brown rock to an equivalent MC
SOIL_MODELS_P2 = ["MC: 2", "HS: 3", "HSsmall: 4", "Soft Soil: 5", "SSC: 6", "MCC: 8"]
HB_PROPS = ("GSI", "SigmaCI", "Sigmaci", "sigmaci", "SigmaCi", "mi", "Mi")
STIFF_PROPS = ("E50Ref", "EOedRef", "EURRef", "G0Ref", "ERef", "Eref", "E50ref", "Eoedref", "Eurref", "G0ref")


def candidates(m: dict) -> list[dict]:
    """Every PLAXIS input parameter of the model the engineer can vary, under the PLAXIS Input mode it belongs to
    (soil, structures, mesh, flow, stages), each with its PLAXIS ref, unit and current value."""
    v = m["version"]
    out = []

    def add(key, section, group, label, ref, unit, current, kind="number", **kw):
        out.append({"key": key, "section": section, "group": group, "label": label, "plaxis_ref": ref,
                    "unit": unit, "current": current, "kind": kind, **kw})

    # Soil: soil materials (every numeric parameter) and the soil layers' levels in the boreholes
    for mat in m["materials"]:
        if mat["kind"] != "soil":
            continue
        curated = [p for p, *_ in SOIL_PROPS]
        for prop in sorted(mat.get("props", {}), key=lambda p: (curated.index(p) if p in curated else 99, p)):
            if HIDDEN_SOIL.match(prop):
                continue
            add(f"mat:{mat['name']}.{prop}", "soil", f"Soil material · {mat['name']}"
                + (f" ({mat['model']})" if mat.get("model") else ""), f"{mat['name']} {label_of(prop)}",
                f"{mat['name']}.{prop}", unit_of(prop, "soil", v), mat["props"][prop])
        # V26.1.8: the constitutive model itself is a variable (MC / HS / HSsmall / SSC), and the creep index μ*
        # of Soft Soil Creep (the material becomes SSC, its other parameters from the existing ones)
        p = mat.get("props", {})
        code = str(mat.get("model") or "").strip()
        rock = any(k in p for k in HB_PROPS) or "hoek" in code.lower()
        models = (["Hoek-Brown: 10", "MC (equivalent to Hoek-Brown): 2"] if rock else list(SOIL_MODELS_P2))
        cur = next((x for x in models if x.split(": ")[1] == code or x.split(": ")[0].lower() == code.lower()), None)
        add(f"smodel:{mat['name']}", "soil", f"Soil material · {mat['name']}", f"{mat['name']} soil model",
            f"{mat['name']}.SoilModel", "", cur or code or "", kind="values", values=models)
        dcur = _code_value(DRAINAGE_TYPES, mat.get("drainage"))  # V26.1.9: short / long term behaviour
        add(f"drainage:{mat['name']}", "soil", f"Soil material · {mat['name']}", f"{mat['name']} drainage type",
            f"{mat['name']}.DrainageType", "", dcur, kind="values", values=list(DRAINAGE_TYPES))
        if any(p.get(k) for k in STIFF_PROPS):  # the whole stiffness set scaled together (ratios kept valid)
            add(f"stiff:{mat['name']}", "soil", f"Soil material · {mat['name']}",
                f"{mat['name']} stiffness set × (E50, Eoed, Eur, G0 together)", f"{mat['name']}.StiffnessFactor", "×", 1.0)
        if p.get("EOedRef") and p.get("pRef"):
            lam = p["pRef"] / p["EOedRef"]
            add(f"sscmu:{mat['name']}", "soil", f"Soil material · {mat['name']}", f"{mat['name']} creep index μ* (SSC)",
                f"{mat['name']}.SSCmu", "-", float(f"{lam / 25:.4g}"))
    for bi, b in enumerate(m["boreholes"]):
        for lay in b.get("layers", []):
            add(f"layer:{lay['name']}.{bi}.Bottom", "soil", f"Soil layers · {b['name']}",
                f"{lay['name']} ({lay.get('material', '')}) bottom level", f"{lay['name']}.Zones[{bi}].Bottom", "m",
                lay["bottom"])

    # Structures: geometry points, structures and their materials, loads
    axis = "z" if v == "3d" else "y"
    pts = {p["name"]: p for p in m["points"]}
    for st in m["structures"]:
        if st["kind"] in ("plate", "embedded_beam") and st.get("points"):
            low = min(st["points"], key=lambda q: q[2] if v == "3d" else q[1])
            name = next((n for n, p in pts.items() if abs(p["x"] - low[0]) < 1e-6 and abs(p["y"] - low[1]) < 1e-6
                         and abs(p.get("z", 0) - (low[2] if len(low) > 2 else 0)) < 1e-6), None)
            if name:
                add(f"geo:{st['name']}.toe", "structures", "Structure geometry", f"{st['name']} toe level",
                    f"{name}.{axis}", "m", pts[name][axis])
    for mat in m["materials"]:
        if mat["kind"] == "soil":
            continue
        for prop, val in mat.get("props", {}).items():
            add(f"mat:{mat['name']}.{prop}", "structures",
                f"{KIND_LABEL.get(mat['kind'], mat['kind'])} material · {mat['name']}",
                f"{mat['name']} {label_of(prop)}", f"{mat['name']}.{prop}", unit_of(prop, mat["kind"], v), val)
    for ld in m["loads"]:
        for prop, short in LOAD_PROPS.get(ld["kind"], []):
            if prop not in ld.get("props", {}):
                continue
            add(f"load:{ld['name']}.{prop}", "structures", "Loads", f"{ld['name']} {short}",
                f"{ld['name']}.{prop}", LOAD_UNIT.get((v, ld["kind"]), ""), ld["props"][prop])
    p1_structures(m, add)
    for ld in m["loads"]:  # the surcharge area: its edges (reduce / extend it) and each corner, even off the model points
        cs = ld.get("points") or []
        if ld["kind"] == "point" or len(cs) < 2:
            continue
        grp = f"Surcharge area · {ld['name']}"
        plan = v == "3d" and len(cs) > 2
        size = area_of(cs) if plan else math.dist([float(x) for x in cs[0][:2]], [float(x) for x in cs[-1][:2]])
        walls = [q for st in m["structures"] if st["kind"] == "plate" for q in st.get("points") or []] or \
            [q for st in m["structures"] for q in st.get("points") or []]  # the corner by the wall stays
        k0 = min(range(len(cs)), key=lambda i: min((math.dist([float(x) for x in cs[i][:2]], [float(x) for x in w[:2]])
                                                    for w in walls), default=0)) + 1 if walls else 1
        if size > 1e-9:
            add(f"area:{ld['name']}.size", "structures", grp,
                f"{ld['name']} {'area' if plan else 'length'} (kept at C{k0})", f"{ld['name']}.areasize{k0}",
                "m²" if plan else "m", round(size, 4), corners=cs, anchor=k0)
        for k, ax in enumerate(m["axes"][:2] if v == "3d" else m["axes"][:1]):
            vals = [float(q[k]) for q in cs]
            if max(vals) - min(vals) < 1e-9:
                continue
            for side, word in (("min", "start" if v != "3d" else "min"), ("max", "end" if v != "3d" else "max")):
                add(f"area:{ld['name']}.{ax}{side}", "structures", grp, f"{ld['name']} area edge {ax} {word}",
                    f"{ld['name']}.area:{ax}{side}", "m", min(vals) if side == "min" else max(vals), corners=cs)
        for i, q in enumerate(cs, 1):
            for k, ax in enumerate(m["axes"]):
                if k < len(q):
                    add(f"corner:{ld['name']}.{i}.{ax}", "structures", grp, f"{ld['name']} corner {i} {ax}",
                        f"{ld['name']}.corner{i}:{ax}", "m", float(q[k]), corners=cs)
    for o in objects(m):  # move a whole structure / load / line: every point shifts by the same amount
        if len(o["points"]) < 2:
            continue
        for ax in m["axes"]:
            add(f"move:{o['name']}.{ax}", "structures", "Move a whole structure, load or line",
                f"{describe(o)} shift in {ax}", "; ".join(f"{n}.{ax}+" for n in o["points"]), "m", 0.0,
                points=o["points"])
    for p in m["points"]:
        on = load_corners_at(m, p)  # a corner of a surcharge load: the load follows the point
        for ax in m["axes"]:
            refs = [f"{p['name']}.{ax}"] + [f"{n}.corner{i}:{ax}" for n, i in on]
            add(f"pt:{p['name']}.{ax}", "structures", "Geometry points",
                f"{p['name']} {ax}" + (f" (corner C{on[0][1]} of {on[0][0]})" if on else ""),
                "; ".join(refs), "m", p[ax], **({"loads": [list(x) for x in on]} if on else {}))

    # Remove an item in a run: a structure or load (deactivated in every stage), a point (deleted with its lines),
    # a stage (deleted; only one without later stages)
    for o in m["structures"] + m["loads"]:
        add(f"del:{o['name']}", "structures", "Remove", f"Remove {o['name']}", f"{o['name']}.remove", "", "keep",
            kind="remove")
    for p in m["points"]:
        add(f"del:{p['name']}", "structures", "Remove a point", f"Remove {p['name']} (and the lines on it)",
            f"{p['name']}.remove", "", "keep", kind="remove")
    for ph in (m.get("phases") or [])[1:]:
        add(f"del:{ph['name']}", "stages", "Remove", f"Remove stage {ph['name']} ({ph.get('id', '')})",
            f"{ph['name']}.remove", "", "keep", kind="remove")

    # Mesh
    add("mesh", "mesh", "Mesh", "Global mesh (element distribution)", "Mesh.size", "",
        m.get("mesh", {}).get("level", "Medium"), "mesh", values=mesh_values(v))

    # Flow conditions: groundwater heads
    for b in m["boreholes"]:
        if b.get("head") is not None:
            add(f"water:{b['name']}.Head", "flow", "Groundwater", f"Groundwater head at {b['name']}",
                f"{b['name']}.Head", "m", b["head"])

    p1_flow(m, add)

    # Staged construction: phase settings and the values that change per phase
    ids = {ph["name"]: ph.get("id") or ph["name"] for ph in m["phases"]}
    for ph in m["phases"]:
        for prop, val in (ph.get("settings") or {}).items():
            add(f"phase:{ph['name']}.{prop}", "stages", f"Phase · {ph.get('id') or ph['name']}",
                f"{ph['name']} {label_of(prop)}", f"{ph['name']}.{prop}", unit_of(prop, "phase", v), val)
    for sv in m.get("staged") or []:
        named = sv["prop"] in ("PrestressForce", "Cref", "Qwell", "h") or sv.get("kind") in ("well", "drain", "contraction")
        add(f"staged:{sv['object']}.{sv['prop']}@{sv['phase']}",
            "flow" if sv.get("kind") in ("well", "drain") else "stages", f"Phase · {ids.get(sv['phase'], sv['phase'])}",
            f"{sv['object']} {label_of(sv['prop']) if named else sv['prop']} in {sv['phase']}",
            f"{sv['object']}.{sv['prop']}@{sv['phase']}", sv.get("unit", ""), sv["value"])
    p1_stages(m, add)
    p2_candidates(m, add)
    return out


# ---------------------------------------------------------------------------------------------------------------
# V26.1.9: the P1 features of the QA/QC report for missing features (excavation and wall design like UA-22)
def _code_value(values: list[str], current) -> str:
    """The 'label: code' entry of a values list matching what PLAXIS returned (a code or a name)."""
    cur = str(current if current is not None else "").strip().lower()
    for x in values:
        lab, _, code = x.rpartition(": ")
        if cur and (cur == code or cur == lab.lower() or cur.replace(" ", "") == lab.lower().replace(" ", "").replace("(", "").replace(")", "")):
            return x
    return values[0]


def _stage_phases(m: dict) -> list[dict]:
    """The construction stages (not the initial phase, not a safety phase)."""
    return [ph for ph in (m.get("phases") or [])[1:] if not is_safety_phase(ph)]


def host_of(m: dict, pts: list) -> str:
    """The structure an interface lies along (same corner points), for its label."""
    key = lambda q: tuple(round(float(x), 4) for x in list(q)[:3])
    mine = {key(q) for q in pts or []}
    for st in m.get("structures") or []:
        if mine and mine <= {key(q) for q in st.get("points") or []}:
            return st["name"]
    return ""


def adjacent_soil(m: dict, itf: dict) -> str:
    """QA V26.1.9 D4: the soil an interface 'from adjacent soil' takes its properties from: the layer at the mid level
    of the interface (or of the structure it lies along); in 2D the soil polygon next to it on its side."""
    pts = itf.get("points") or next((st.get("points") for st in m.get("structures") or []
                                     if st["name"] == host_of(m, itf.get("points"))), None) or []
    if m.get("version") != "3d" and len(pts) >= 2 and m.get("soil_polygons"):
        (x1, y1), (x2, y2) = [(float(q[0]), float(q[1])) for q in pts[:2]]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        L = math.hypot(x2 - x1, y2 - y1) or 1.0
        nx, ny = -(y2 - y1) / L, (x2 - x1) / L  # PLAXIS: the positive side is on the left of the line
        sgn = -1.0 if itf.get("side") == "negative" else 1.0
        for d in (0.05, 0.25, 1.0):
            px, py = mx + sgn * nx * d, my + sgn * ny * d
            for sp in m["soil_polygons"]:
                if _inside(px, py, [(float(q[0]), float(q[1])) for q in sp.get("points") or []]):
                    return sp.get("material") or ""
    return soil_at(m, pts)


def adjacent_soils(m: dict, itf: dict) -> list[str]:
    """QA V26.1.9 E1: every soil layer the interface runs along, top to bottom (first borehole; in 2D the soil
    polygons next to it on its side): 'from adjacent soil' gives each part of the interface its own layer."""
    pts = itf.get("points") or next((st.get("points") for st in m.get("structures") or []
                                     if st["name"] == host_of(m, itf.get("points"))), None) or []
    out: list[str] = []
    if m.get("version") != "3d" and len(pts) >= 2 and m.get("soil_polygons"):
        (x1, y1), (x2, y2) = [(float(q[0]), float(q[1])) for q in pts[:2]]
        L = math.hypot(x2 - x1, y2 - y1) or 1.0
        nx, ny = -(y2 - y1) / L, (x2 - x1) / L
        sgn = -1.0 if itf.get("side") == "negative" else 1.0
        if y2 > y1:
            (x1, y1), (x2, y2) = (x2, y2), (x1, y1)
        for k in range(41):  # along the interface, top to bottom
            t = k / 40
            px, py = x1 + t * (x2 - x1) + sgn * nx * 0.05, y1 + t * (y2 - y1) + sgn * ny * 0.05
            for sp in m["soil_polygons"]:
                if _inside(px, py, [(float(q[0]), float(q[1])) for q in sp.get("points") or []]):
                    if sp.get("material") and sp["material"] not in out:
                        out.append(sp["material"])
                    break
        if out:
            return out
    if pts and m.get("boreholes"):
        vi = 2 if m.get("version") == "3d" else 1
        lv = [float(q[vi]) for q in pts if len(q) > vi]
        lo, hi = min(lv), max(lv)
        for lay in sorted(m["boreholes"][0].get("layers") or [], key=lambda x: -x["top"]):
            if min(hi, lay["top"]) - max(lo, lay["bottom"]) > 1e-6 and lay.get("material") and lay["material"] not in out:
                out.append(lay["material"])
    return out or [x for x in [adjacent_soil(m, itf)] if x]


def _inside(x: float, y: float, poly: list) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / ((y2 - y1) or 1e-12):
            inside = not inside
    return inside


def soil_at(m: dict, pts: list) -> str:
    """The soil material at the mid level of a structure (first borehole): the interface's 'adjacent soil'."""
    if not pts or not m.get("boreholes"):
        return ""
    vi = 2 if m.get("version") == "3d" else 1
    lv = [float(q[vi]) for q in pts if len(q) > vi]
    mid = (min(lv) + max(lv)) / 2 if lv else 0.0
    for lay in m["boreholes"][0].get("layers") or []:
        if lay["bottom"] - 1e-9 <= mid <= lay["top"] + 1e-9:
            return lay.get("material") or ""
    return ""


def p1_structures(m: dict, add) -> None:
    """Prestress per anchor row and stage, interface objects (own Rinter and virtual thickness), tunnel contraction."""
    ids = {ph["name"]: ph.get("id") or ph["name"] for ph in m.get("phases") or []}
    stages = _stage_phases(m)
    rows: dict[str, list] = {}
    for st in m.get("structures") or []:
        if st["kind"] in ("anchor", "cable"):
            rows.setdefault(st.get("material") or st["name"], []).append(st)
    for mat, anchors in rows.items():
        active = [n for a in anchors for n in a.get("active") or []]
        first = next((i for i, ph in enumerate(stages) if ph["name"] in active), 0 if not active else None)
        if first is None:
            continue
        for ph in stages[first:]:
            mine = lambda o: any(o == a["name"] or o.startswith(a["name"] + "_") for a in anchors)
            cur = next((sv["value"] for sv in m.get("staged") or [] if sv["prop"] == "PrestressForce"
                        and sv["phase"] == ph["name"] and mine(sv["object"])), 0.0)
            n = len(anchors)
            add(f"prestress:{mat}@{ph['name']}", "stages", f"Anchor prestress · row {mat}",
                f"Anchor row {mat} ({n} anchor{'s' if n > 1 else ''}) prestress in {ids.get(ph['name'], ph['name'])}",
                "; ".join(f"{a['name']}.PrestressForce@{ph['name']}" for a in anchors), "kN", float(cur))
    for itf in m.get("interfaces") or []:
        host = host_of(m, itf.get("points"))
        custom = "custom" in str(itf.get("mode") or "").lower() and itf.get("material")
        bases = [itf["material"]] if custom else adjacent_soils(m, itf)  # E1: the layers along it
        mats = {x["name"]: x for x in m.get("materials") or []}
        bmat = mats.get(bases[0], {}) if bases else {}
        ids = ", ".join(mats.get(b, {}).get("id") or b for b in bases)
        grp = f"Interface · {itf['name']}" + (f" along {host}" if host else "")
        for prop, lab, unit, dflt in (("Rinter", "strength reduction Rinter", "-", 1.0), ("DeltaInter", "virtual thickness factor δinter", "-", 0.1)):
            cur = (bmat.get("props") or {}).get(prop, dflt)
            per = f" (its material {ids})" if custom else (f" (along {ids}: a copy of the layer at its mid level)" if len(bases) > 1 else f" ({ids})" if ids else "")
            add(f"iface:{itf['name']}.{prop}", "structures", grp, f"{itf['name']}{' (' + host + ')' if host else ''} own {lab}{per}",
                f"{itf['name']}|{','.join(bases)}.iface{prop}", unit, float(cur))
    for c in m.get("contractions") or []:
        if c.get("value") is not None and not any(sv["object"] == c["name"] for sv in m.get("staged") or []):
            add(f"contraction:{c['name']}", "structures", "Tunnel contraction (volume loss)", f"{c['name']} contraction Cref",
                f"{c['name']}.Cref", "%", float(c["value"]))


def p1_flow(m: dict, add) -> None:
    """Water level per stage (dewatering steps), cluster water conditions, groundwater flow calculation, wells and
    drains (dewatering design)."""
    ids = {ph["name"]: ph.get("id") or ph["name"] for ph in m.get("phases") or []}
    v = m.get("version")
    head = next((b["head"] for b in m.get("boreholes") or [] if b.get("head") is not None), None)
    if head is None and m.get("water"):
        q = (m["water"][0].get("points") or [[0, 0, 0]])[0]
        head = float(q[2] if v == "3d" else q[1])
    for ph in _stage_phases(m):
        add(f"wlevel:@{ph['name']}", "flow", "Water level per stage (dewatering)",
            f"Phreatic level in {ids.get(ph['name'], ph['name'])} (horizontal, whole model)", f"WaterLevel.level@{ph['name']}",
            "m", float(head if head is not None else 0.0))
    clusters = m.get("clusters") or [p["name"] for p in m.get("soil_polygons") or []]
    if len(clusters) <= 60:
        for ph in _stage_phases(m):
            for cl in clusters:
                add(f"wcond:{cl}@{ph['name']}", "flow", f"Cluster water conditions · {ids.get(ph['name'], ph['name'])}",
                    f"{cl} water condition in {ph['name']}", f"{cl}.WaterCondition@{ph['name']}", "",
                    CLUSTER_CONDITIONS[0], kind="values", values=list(CLUSTER_CONDITIONS))
    for ph in (m.get("phases") or []):
        if is_safety_phase(ph):
            continue
        add(f"pore:{ph['name']}", "flow", "Groundwater flow calculation",
            f"Pore pressure calculation in {ids.get(ph['name'], ph['name'])}", f"{ph['name']}.PorePresCalcType", "",
            _code_value(PORE_TYPES, ph.get("pore")), kind="values", values=list(PORE_TYPES))
    staged = {sv["object"] for sv in m.get("staged") or [] if sv.get("kind") in ("well", "drain")}
    for coll, lab, unit in (("wells", "discharge |Qwell|", "m³/day"), ("drains", "head h", "m")):
        for w in m.get(coll) or []:
            if w["name"] in staged:
                continue  # its stage values are offered under the stages (Phase · ...)
            for ph in _stage_phases(m):
                add(f"{coll[:-1]}:{w['name']}@{ph['name']}", "flow", f"{'Wells' if coll == 'wells' else 'Drains'} (dewatering design)",
                    f"{w['name']} {lab} in {ids.get(ph['name'], ph['name'])}", f"{w['name']}.{w.get('prop') or ('Qwell' if coll == 'wells' else 'h')}@{ph['name']}",
                    unit, float(w.get("value") or 0.0))


def p1_stages(m: dict, add) -> None:
    """The design approach of a run (EC7 partial factors on variable loads and soil strength)."""
    add("design:approach", "stages", "Design approach (EC7 partial factors)", "Design approach of the run",
        "Design.approach", "", DESIGN_APPROACHES[0], kind="values", values=list(DESIGN_APPROACHES))


def apply_inputs(m: dict, variables: list[dict], inputs: dict) -> tuple[dict, list[dict], list[str]]:
    """The model as it is in one run: (model copy, changes, moved point names). Point coordinates move the points
    and every structure, load, line and soil polygon corner at the same place; soil layer bottoms and heads move
    the layers and the water level; material, load and phase values are listed as changes."""
    import copy
    r = copy.deepcopy(m)
    v3 = r["version"] == "3d"
    vi = 2 if v3 else 1
    changes, moved = [], []
    byname = {v["name"]: v for v in variables}
    for name, raw in (inputs or {}).items():
        var = byname.get(name)
        if not var:
            continue
        val = raw[1] if isinstance(raw, (list, tuple)) and len(raw) == 2 else raw
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        for ref in [x.strip() for x in str(var.get("plaxis_ref") or "").split(";") if x.strip()]:
            base, _, phase = ref.partition("@")
            obj, _, prop = base.rpartition(".")
            old = None
            if obj.lower() == "mesh":
                level = raw[0] if isinstance(raw, (list, tuple)) else val
                old, r["mesh"] = r.get("mesh", {}).get("level"), {"level": level}
                changes.append({"label": "Mesh", "from": old, "to": level})
                continue
            shift = prop.endswith("+")
            prop = prop.rstrip("+")
            pt = next((p for p in r["points"] if p["name"] == obj), None)
            if pt is not None and prop in ("x", "y", "z") and not phase:
                old = pt[prop]
                ai = "xyz".index(prop)
                before = [pt["x"], pt["y"], pt.get("z", 0.0)]
                new_val = old + val if shift else val
                pt[prop] = new_val
                if shift:
                    old, val_shown = 0.0, val
                val = new_val
                for coll in (r["structures"], r["loads"], r["water"]):
                    for o in coll:
                        for q in o.get("points") or []:
                            if all(abs(q[i] - before[i]) < 1e-6 for i in range(min(3, len(q)))):
                                q[ai] = val
                if not v3 and ai < 2:
                    for poly in r["soil_polygons"]:
                        for q in poly["points"]:
                            if abs(q[0] - before[0]) < 1e-6 and abs(q[1] - before[1]) < 1e-6:
                                q[ai] = val
                moved.append(obj)
            elif obj.startswith("Soillayer") and prop in ("Bottom", "Top"):
                lname = obj.split(".")[0]
                bi = int(re.search(r"\[(\d+)\]", obj).group(1)) if "[" in obj else 0
                hole = r["boreholes"][bi] if bi < len(r["boreholes"]) else None
                lays = hole["layers"] if hole else []
                for i, lay in enumerate(lays):
                    if lay["name"] == lname:
                        key = prop.lower()
                        old = lay[key]
                        lay[key] = val
                        nxt = lays[i + 1] if key == "bottom" and i + 1 < len(lays) else None
                        if nxt:
                            nxt["top"] = val
                if old is not None and not v3:
                    for poly in r["soil_polygons"]:
                        for q in poly["points"]:
                            if abs(q[1] - old) < 1e-6:
                                q[1] = val
            elif obj.startswith("Borehole") and prop == "Head":
                hole = next((b for b in r["boreholes"] if b["name"] == obj), None)
                if hole:
                    old, hole["head"] = hole.get("head"), val
                    for w in r["water"]:
                        for q in w.get("points") or []:
                            if old is not None and abs(q[vi] - old) < 1e-6:
                                q[vi] = val
            else:
                mat = next((x for x in r["materials"] if x["name"] == obj), None)
                if mat is not None and not phase:
                    old = mat.get("props", {}).get(prop)
                    mat.setdefault("props", {})[prop] = val
                ld = next((x for x in r["loads"] if x["name"] == obj), None)
                if ld is not None and not phase:
                    old = ld.get("props", {}).get(prop)
                    ld.setdefault("props", {})[prop] = val
                for sv in r.get("staged") or []:
                    if phase and sv["object"] == obj and sv["prop"] == prop and sv["phase"] == phase:
                        old, sv["value"] = sv["value"], val
                for ph in r["phases"]:
                    if ph["name"] == obj.split(".")[0] and ph.get("settings") is not None:
                        key = obj.split(".", 1)[1] + "." + prop if "." in obj else prop
                        if key in ph["settings"]:
                            old, ph["settings"][key] = ph["settings"][key], val
            if shift:
                if not any(c.get("var") == name for c in changes):
                    changes.append({"label": var.get("label") or ref, "ref": var.get("plaxis_ref"), "var": name,
                                    "unit": var.get("unit") or "", "from": 0.0, "to": val_shown})
                val = float(raw[1] if isinstance(raw, (list, tuple)) and len(raw) == 2 else raw)
                continue
            changes.append({"label": var.get("label") or ref, "ref": ref, "unit": var.get("unit") or "",
                            "from": old, "to": val})
    finish(r)
    return r, changes, moved


def variable_from(item: dict, name: str, version: str) -> dict:
    """An item ticked in the picker, as a row of the Variables table (range ±20 % around the current value)."""
    base = {"name": name, "label": item["label"], "unit": item.get("unit", ""), "plaxis_ref": item["plaxis_ref"],
            "section": item["section"], "item": item["key"], "step": None}
    if item["kind"] == "remove":
        vals = ["keep: 0", "remove: 1"]
        return {**base, "kind": "values", "values": vals, "min": None, "max": None, "current": vals[0]}
    if item["kind"] == "mesh":
        vals = mesh_values(version)
        cur = next((x for x in vals if x.split(":")[0] == item.get("current")), vals[2])
        return {**base, "kind": "values", "values": vals, "min": None, "max": None, "current": cur}
    if item["kind"] == "values":  # e.g. the soil model (V26.1.8)
        vals = list(item.get("values") or [])
        return {**base, "kind": "values", "values": vals, "min": None, "max": None,
                "current": item.get("current") if item.get("current") in vals else (vals[0] if vals else "")}
    cur = float(item["current"])
    if item["key"].startswith("move:"):  # a shift: from the existing position, metres up / down
        return {**base, "kind": "range", "values": [], "min": -2.0, "max": 2.0, "current": "0"}
    span = abs(cur) * 0.2 or 1.0
    if item["key"].startswith(("geo:", "pt:", "water:", "layer:", "area:", "corner:")):
        span = max(1.0, round(abs(cur) * 0.2, 1)) if cur else 1.0
    lo, hi = cur - span, cur + span
    return {**base, "kind": "range", "values": [], "min": _r(lo), "max": _r(hi), "current": f"{_r(cur):g}"}


def _r(x: float) -> float:
    return float(f"{x:.6g}")


# ---------------------------------------------------------------------------------------------------------------
# Drawing (SVG). 2D: the section as in PLAXIS Input. 3D: an oblique view of the soil block and structures.
SOIL_COLORS = ["#d9b46a", "#a7c47a", "#e7c9a0", "#8fb996", "#c9a27a", "#b7d4a8", "#e3b07c", "#9fbf8a"]
STRUCT = {"plate": ("#1d3557", 5), "anchor": ("#e07a1f", 3), "geogrid": ("#2e8b57", 3.5), "embedded_beam": ("#6b3fa0", 4),
          "beam": ("#6b3fa0", 4)}


def svg(m: dict, labels: bool = True, selected: list[str] | None = None, view: str = "iso") -> str:
    """The model drawing. Points (class pt), structures, loads and lines (class obj) carry data-name for clicks;
    names in selected are drawn in orange. 3D: view = iso, front (x-z), side (y-z) or top (x-y)."""
    if m["version"] == "3d":
        return _svg3d(m, labels, selected or [], view if view in ("iso", "front", "side", "top") else "iso")
    return _svg2d(m, labels, selected or [])


def _mat_colors(m):
    soils = [x["name"] for x in m["materials"] if x["kind"] == "soil"]
    return {n: SOIL_COLORS[i % len(SOIL_COLORS)] for i, n in enumerate(soils)}


def _svg2d(m, labels, selected):
    e = m["extents"]
    w_m, h_m = max(1e-6, e["xmax"] - e["xmin"]), max(1e-6, e["ymax"] - e["ymin"])
    W = 1000
    k = (W - 120) / w_m
    H = int(h_m * k + 150)
    X = lambda x: 60 + (x - e["xmin"]) * k  # noqa: E731
    Y = lambda y: 90 + (e["ymax"] - y) * k  # noqa: E731
    col = _mat_colors(m)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Segoe UI, Arial, sans-serif" font-size="20">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>']
    polys = m["soil_polygons"]
    if not polys and m["boreholes"]:  # layers across the model from the borehole levels
        b = m["boreholes"][0]
        polys = [{"name": lay["name"], "material": lay["material"],
                  "points": [[e["xmin"], lay["top"]], [e["xmax"], lay["top"]], [e["xmax"], lay["bottom"]], [e["xmin"], lay["bottom"]]]}
                 for lay in b["layers"]]
    for poly in polys:
        pts = " ".join(f"{X(p[0]):.1f},{Y(p[1]):.1f}" for p in poly["points"])
        fill = "url(#hatch)" if poly.get("excavated") else col.get(poly.get("material"), "#e8dcc0")
        o.append(f'<polygon points="{pts}" fill="{fill}" stroke="#5b4a2f" stroke-width="1" opacity=".9">'
                 f'<title>{escape(poly["name"])}: {escape(poly.get("material") or "")}</title></polygon>')
    o.insert(2, '<defs><pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#fff"/>'
                '<path d="M0 8L8 0" stroke="#c9b48a" stroke-width="1"/></pattern></defs>')
    for wl in m["water"]:
        pts = " ".join(f"{X(q[0]):.1f},{Y(q[1]):.1f}" for q in wl["points"])
        o.append(f'<polyline points="{pts}" fill="none" stroke="#1f77d0" stroke-width="2" stroke-dasharray="8 5"/>')
        q = wl["points"][0]
        o.append(f'<path d="M{X(q[0]) + 18:.1f} {Y(q[1]) - 12:.1f} l8 0 l-4 8z" fill="#1f77d0"/>')
    for ld in m["loads"]:
        if len(ld["points"]) >= 2:
            a, b = ld["points"][0], ld["points"][-1]
            n = max(2, int(abs(X(b[0]) - X(a[0])) / 18))
            for i in range(n + 1):
                x = X(a[0] + (b[0] - a[0]) * i / n)
                y = Y(a[1] + (b[1] - a[1]) * i / n)
                o.append(f'<path d="M{x:.1f} {y - 26:.1f} V{y - 3:.1f} M{x - 4:.1f} {y - 9:.1f} L{x:.1f} {y - 2:.1f} L{x + 4:.1f} {y - 9:.1f}" stroke="#d1495b" stroke-width="1.6" fill="none"/>')
            val = next(iter(ld["props"].values()), "")
            o.append(f'<text class="obj" data-name="{escape(ld["name"])}" x="{X(a[0]):.1f}" y="{Y(a[1]) - 32:.1f}" fill="{"#2f7fd0" if ld["name"] in selected else "#d1495b"}" font-weight="600">{escape(ld["name"])} {val:g}</text>')
    pmap = {p["name"]: p for p in m["points"]}
    for ln in m["lines"]:
        a, b = pmap.get(ln.get("a")), pmap.get(ln.get("b"))
        if not a or not b:
            continue
        sel = ln["name"] in selected
        o.append(f'<line class="obj" data-name="{escape(ln["name"])}" x1="{X(a["x"]):.1f}" y1="{Y(a["y"]):.1f}" x2="{X(b["x"]):.1f}" '
                 f'y2="{Y(b["y"]):.1f}" stroke="{"#2f7fd0" if sel else "#6d7f73"}" stroke-width="{4 if sel else 1.2}" '
                 f'stroke-dasharray="{"" if sel else "3 3"}"><title>{escape(ln["name"])}</title></line>')
    for s in m["structures"]:
        if len(s["points"]) < 2:
            continue
        c, wdt = STRUCT.get(s["kind"], ("#333", 3))
        pts = " ".join(f"{X(q[0]):.1f},{Y(q[1]):.1f}" for q in s["points"])
        dash = ' stroke-dasharray="7 4"' if s["kind"] == "geogrid" else ""
        if s["name"] in selected:
            o.append(f'<polyline points="{pts}" fill="none" stroke="#2f7fd0" stroke-width="{wdt + 8}" opacity=".55" stroke-linecap="round"/>')
        o.append(f'<polyline class="obj" data-name="{escape(s["name"])}" points="{pts}" fill="none" stroke="{c}" stroke-width="{wdt}" stroke-linecap="round"{dash}>'
                 f'<title>{escape(s["name"])} ({escape(s["material"])})</title></polyline>')
        o.append(f'<polyline class="obj hit" data-name="{escape(s["name"])}" points="{pts}" fill="none" stroke="transparent" stroke-width="16"/>')
    for p in m["points"]:
        sel = p["name"] in selected
        o.append(f'<circle class="pt{" sel" if sel else ""}" data-name="{escape(p["name"])}" cx="{X(p["x"]):.1f}" cy="{Y(p["y"]):.1f}" '
                 f'r="{7 if sel else 4.5}" fill="{"#2f7fd0" if sel else "#1f8a4c"}" stroke="#fff" stroke-width="1.5">'
                 f'<title>{escape(p["name"])} ({p["x"]:g}, {p["y"]:g})</title></circle>')
        if labels:
            o.append(f'<text x="{X(p["x"]) + 7:.1f}" y="{Y(p["y"]) - 6:.1f}" fill="#123" font-size="15">{escape(p["name"].replace("Point_", "P"))} ({p["x"]:g}, {p["y"]:g})</text>')
    # axes
    o.append(f'<path d="M40 {H - 30} h40 M40 {H - 30} v-40" stroke="#333" stroke-width="1.5" fill="none"/>'
             f'<text x="84" y="{H - 26}">x</text><text x="34" y="{H - 74}">y</text>')
    o.append(f'<text x="{W - 20}" y="{H - 14}" text-anchor="end" fill="#555">{escape(m.get("product", ""))} · '
             f'x {e["xmin"]:g} … {e["xmax"]:g} m, y {e["ymin"]:g} … {e["ymax"]:g} m</text>')
    o.append(_legend(m, col, 60, 30))
    o.append("</svg>")
    return "\n".join(o)


def _legend(m, col, x, y):
    items = [(c, n) for n, c in col.items()]
    out, cx = [], x
    for c, n in items:
        out.append(f'<rect x="{cx}" y="{y - 16}" width="20" height="20" fill="{c}" stroke="#5b4a2f"/><text x="{cx + 26}" y="{y + 1}">{escape(n)}</text>')
        cx += 44 + 11 * len(n)
    return "".join(out)


def _svg3d(m, labels, selected, view="iso"):
    e = m["extents"]
    ang = math.radians(30)
    ky = 0.55  # depth foreshortening

    def proj(x, y, z):
        if view == "front":
            return x, -z
        if view == "side":
            return y, -z
        if view == "top":
            return x, -y
        return x + ky * y * math.cos(ang), -z + ky * y * math.sin(ang) * -1

    corners = [proj(x, y, z) for x in (e["xmin"], e["xmax"]) for y in (e["ymin"], e["ymax"]) for z in (e["zmin"], e["zmax"])]
    umin, umax = min(c[0] for c in corners), max(c[0] for c in corners)
    vmin, vmax = min(c[1] for c in corners), max(c[1] for c in corners)
    W = 1000
    k = (W - 140) / max(1e-6, umax - umin)
    H = int((vmax - vmin) * k + 130)
    T = lambda x, y, z: (70 + (proj(x, y, z)[0] - umin) * k, 70 + (proj(x, y, z)[1] - vmin) * k)  # noqa: E731
    col = _mat_colors(m)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Segoe UI, Arial, sans-serif" font-size="20">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>']

    def face(pts, fill, op=".85", stroke="#5b4a2f", title=""):
        s = " ".join("%.1f,%.1f" % T(*p) for p in pts)
        t = f"<title>{escape(title)}</title>" if title else ""
        return f'<polygon points="{s}" fill="{fill}" stroke="{stroke}" stroke-width="1" opacity="{op}">{t}</polygon>'

    x0, x1, y0, y1 = e["xmin"], e["xmax"], e["ymin"], e["ymax"]
    b = m["boreholes"][0] if m["boreholes"] else None
    for lay in (b["layers"] if b else []):
        c = col.get(lay["material"], "#e8dcc0")
        t, bt = lay["top"], lay["bottom"]
        o.append(face([(x0, y1, t), (x1, y1, t), (x1, y1, bt), (x0, y1, bt)], c, ".55"))  # back
        o.append(face([(x0, y0, t), (x1, y0, t), (x1, y0, bt), (x0, y0, bt)], c, ".9", title=f"{lay['name']}: {lay['material']}"))
        o.append(face([(x1, y0, t), (x1, y1, t), (x1, y1, bt), (x1, y0, bt)], c, ".75"))
    if b and b["layers"]:
        top = b["layers"][0]["top"]
        o.append(face([(x0, y0, top), (x1, y0, top), (x1, y1, top), (x0, y1, top)], "#cfe3c4", ".55"))
    for wl in m["water"]:
        if len(wl["points"]) >= 3:
            o.append(face([tuple(q) for q in wl["points"]], "#7fb6f0", ".35", "#1f77d0"))
    for ld in m["loads"]:
        if len(ld["points"]) >= 3:
            o.append(face([tuple(q) for q in ld["points"]], "#2f7fd0" if ld["name"] in selected else "#f4a3ad", ".7",
                          "#d1495b", ld["name"]).replace("<polygon ", f'<polygon class="obj" data-name="{escape(ld["name"])}" ', 1))
            cx = sum(q[0] for q in ld["points"]) / len(ld["points"])
            cy = sum(q[1] for q in ld["points"]) / len(ld["points"])
            u, v = T(cx, cy, ld["points"][0][2])
            o.append(f'<text x="{u:.1f}" y="{v - 14:.1f}" fill="#d1495b" font-weight="600" text-anchor="middle">{escape(ld["name"])} {next(iter(ld["props"].values()), 0):g}</text>')
    pmap = {p["name"]: p for p in m["points"]}
    for ln in m["lines"]:
        a, b2 = pmap.get(ln.get("a")), pmap.get(ln.get("b"))
        if not a or not b2:
            continue
        (u1, v1), (u2, v2) = T(a["x"], a["y"], a["z"]), T(b2["x"], b2["y"], b2["z"])
        sel = ln["name"] in selected
        o.append(f'<line class="obj" data-name="{escape(ln["name"])}" x1="{u1:.1f}" y1="{v1:.1f}" x2="{u2:.1f}" y2="{v2:.1f}" '
                 f'stroke="{"#2f7fd0" if sel else "#6d7f73"}" stroke-width="{4 if sel else 1.2}" stroke-dasharray="{"" if sel else "3 3"}">'
                 f'<title>{escape(ln["name"])}</title></line>')
    for s in m["structures"]:
        c, wdt = STRUCT.get(s["kind"], ("#333", 3))
        sel = s["name"] in selected
        if len(s["points"]) >= 3:
            poly = face([tuple(q) for q in s["points"]], "#2f7fd0" if sel else c, ".8", "#2f7fd0" if sel else c,
                        f"{s['name']} ({s['material']})")
            o.append(poly.replace("<polygon ", f'<polygon class="obj" data-name="{escape(s["name"])}" ', 1))
        elif len(s["points"]) == 2:
            (u1, v1), (u2, v2) = T(*s["points"][0]), T(*s["points"][1])
            if sel:
                o.append(f'<line x1="{u1:.1f}" y1="{v1:.1f}" x2="{u2:.1f}" y2="{v2:.1f}" stroke="#2f7fd0" stroke-width="{wdt + 8}" opacity=".55" stroke-linecap="round"/>')
            o.append(f'<line class="obj" data-name="{escape(s["name"])}" x1="{u1:.1f}" y1="{v1:.1f}" x2="{u2:.1f}" y2="{v2:.1f}" stroke="{c}" stroke-width="{wdt}" stroke-linecap="round"><title>{escape(s["name"])} ({escape(s.get("material", ""))})</title></line>')
            o.append(f'<line class="obj hit" data-name="{escape(s["name"])}" x1="{u1:.1f}" y1="{v1:.1f}" x2="{u2:.1f}" y2="{v2:.1f}" stroke="transparent" stroke-width="16"/>')
    for p in m["points"]:
        u, v = T(p["x"], p["y"], p["z"])
        sel = p["name"] in selected
        o.append(f'<circle class="pt{" sel" if sel else ""}" data-name="{escape(p["name"])}" cx="{u:.1f}" cy="{v:.1f}" r="{7 if sel else 4.5}" '
                 f'fill="{"#2f7fd0" if sel else "#1f8a4c"}" stroke="#fff" stroke-width="1.5"><title>{escape(p["name"])} ({p["x"]:g}, {p["y"]:g}, {p["z"]:g})</title></circle>')
        if labels:
            o.append(f'<text x="{u + 7:.1f}" y="{v - 6:.1f}" font-size="15" fill="#123">{escape(p["name"].replace("Point_", "P"))}</text>')
    ox, oy = 50, H - 40
    if view == "iso":
        o.append(f'<path d="M{ox} {oy} h44 M{ox} {oy} v-44 M{ox} {oy} l{30 * math.cos(ang):.1f} {-30 * math.sin(ang):.1f}" stroke="#333" stroke-width="1.5" fill="none"/>'
                 f'<text x="{ox + 48}" y="{oy + 4}">x</text><text x="{ox - 4}" y="{oy - 48}">z</text><text x="{ox + 30}" y="{oy - 22}">y</text>')
    else:
        hx, vy = {"front": ("x", "z"), "side": ("y", "z"), "top": ("x", "y")}[view]
        o.append(f'<path d="M{ox} {oy} h44 M{ox} {oy} v-44" stroke="#333" stroke-width="1.5" fill="none"/>'
                 f'<text x="{ox + 48}" y="{oy + 4}">{hx}</text><text x="{ox - 4}" y="{oy - 48}">{vy}</text>')
    o.append(f'<text x="{W - 20}" y="{H - 14}" text-anchor="end" fill="#555">{escape(m.get("product", ""))} · x {x0:g} … {x1:g}, '
             f'y {y0:g} … {y1:g}, z {e["zmin"]:g} … {e["zmax"]:g} m</text>')
    o.append(_legend(m, col, 60, 30))
    o.append("</svg>")
    return "\n".join(o)


# ---------------------------------------------------------------------------------------------------------------
# Results that can be extracted at every run (PLAXIS Output, g_o.ResultTypes.<Group>.<Type>, max absolute value at
# nodes; "| Y = max" keeps the nodes at the top of the model, e.g. surface settlement).
BASIC_KEYS = ("fos", "utot", "ux", "uv", "settle", "wall_ux", "plate_m", "plate_q", "plate_n", "anchor", "pile_n", "pexcess",
              "file")  # the results of the full run of the Original File (V26.1.8)


def outputs(version: str) -> list[dict]:
    is3 = version == "3d"
    up = "Z" if is3 else "Y"
    o = [
        ("fos", "Factor of safety ΣMsf", "FoS", "", "safety", ">=", 1.3),
        ("utot", "Max total displacement |u|", "Utot_max", "m", "", "report", None),
        ("ux", "Max horizontal displacement ux", "Ux_max", "m", "", "report", None),
        ("uv", "Max vertical displacement " + ("uz" if is3 else "uy"), ("Uz_max" if is3 else "Uy_max"), "m", "", "report", None),
        ("settle", "Surface settlement (top of model)", f"Soil.U{up.lower()}|{up} = max", "m", "", "report", None),
        ("wall_ux", "Wall horizontal deflection", "Plate.Ux", "m", "", "report", None),
        ("plate_m", "Max wall bending moment", "Plate.M11" if is3 else "Plate.M2D", "kN·m/m", "", "report", None),
        ("plate_q", "Max wall shear force", "Plate.Q13" if is3 else "Plate.Q2D", "kN/m", "", "report", None),
        ("plate_n", "Max wall axial force", "Plate.N11" if is3 else "Plate.Nx2D", "kN/m", "", "report", None),
        ("anchor", "Max anchor force", "NodeToNodeAnchor.N" if is3 else "NodeToNodeAnchor.AnchorForce2D",
         "kN" if is3 else "kN/m", "", "report", None),
        ("pile_n", "Max embedded beam axial force", "EmbeddedBeam.N" if is3 else "EmbeddedBeamRow.Nx2D",
         "kN", "", "report", None),
        ("pexcess", "Max excess pore pressure", "Soil.PExcess", "kN/m²", "", "report", None),
        # V26.1.9 (QA/QC missing features, P1): structure results, groundwater flow and the surface trough
        ("geogrid_n", "Max geogrid (grout body) axial force", "Geogrid.N1" if is3 else "Geogrid.Nx2D", "kN/m", "", "report", None),
        ("skin", "Max embedded beam skin friction", "EmbeddedBeam.Tskin" if is3 else "EmbeddedBeamRow.Tskin2D", "kN/m", "", "report", None),
        ("iface_sn", "Max interface effective normal stress", "Interface.InterfaceEffectiveNormalStress", "kN/m²", "", "report", None),
        ("iface_tau", "Max interface shear stress", "Interface.InterfaceShearStress", "kN/m²", "", "report", None),
        ("iface_rel", "Max interface relative shear stress (mobilised)", "Interface.InterfaceRelativeShearStress", "-", "", "report", None),
        ("q", "Max specific discharge |q|", "Soil.Qtot", "m/day", "", "report", None),
        ("head", "Max groundwater head", "Soil.GroundwaterHead", "m", "", "report", None),
        ("psteady", "Max steady-state pore pressure", "Soil.PSteady", "kN/m²", "", "report", None),
        ("pactive", "Max active pore pressure", "Soil.PActive", "kN/m²", "", "report", None),
        ("trough", "Surface settlement trough volume Vs" + (" (section at mid y)" if is3 else ""),
         f"Soil.U{up.lower()}|trough", "m³/m", "", "report", None),
        # V26.1.9 (batch 2): the settlement and wall deflection profiles (curves in the reports), the temperature
        ("settle_profile", "Surface settlement profile (max on the curve)", f"Soil.U{up.lower()}|profile:surface", "m", "", "report", None),
        ("wall_profile", "Wall deflection profile ux (max on the curve)", "Plate.Ux|profile:wall", "m", "", "report", None),
        ("temp", "Max temperature (PLAXIS Thermal)", "Soil.Temperature", "°C", "", "report", None),
        ("file", "PLAXIS project file of the run", "file", "", "", "report", None),
    ]
    if is3:  # 3D beams: the capping beam / waling forces
        o[-1:-1] = [("beam_n", "Max beam axial force (capping beam)", "Beam.N", "kN", "", "report", None),
                    ("beam_m", "Max beam bending moment (capping beam)", "Beam.M2", "kN·m", "", "report", None),
                    ("beam_m3", "Max beam bending moment M3 (capping beam)", "Beam.M3", "kN·m", "", "report", None)]
    return [{"key": k, "label": lab, "result": res, "unit": u, "phase_hint": ph, "operator": op, "value": val}
            for k, lab, res, u, ph, op, val in o]


# ---------------------------------------------------------------------------------------------------------------
# 2. Stand-alone: run with PLAXIS' Python to write model.json
def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Read the geometry, materials, loads and phases of a PLAXIS 2D / 3D "
                                             "project through the remote scripting server and save them as JSON.")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=10000, help="PLAXIS Input remote scripting port")
    ap.add_argument("--password", default="")
    ap.add_argument("--project", default="", help="optional .p2dx / .p3d to open first (else the open project)")
    ap.add_argument("--out", default="model.json")
    a = ap.parse_args(argv)
    from plxscripting.easy import new_server  # ships with PLAXIS
    s_i, g_i = new_server(a.host, a.port, password=a.password)
    if a.project:
        s_i.open(a.project)
    m = extract(s_i, g_i)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)
    print(f"Saved {a.out}: {m['counts']}")


if __name__ == "__main__":
    _main()


# ---------------------------------------------------------------------------------------------------------------
# V26.1.9 (P2 of the QA/QC report for missing features): other common projects
CALC_TYPES = ["Plastic: 0", "Consolidation: 1", "Fully coupled flow-deformation: 2"]
INITIAL_TYPES = {"2d": ["K0 procedure: 0", "Gravity loading: 1"], "3d": ["K0 procedure: 0", "Gravity loading: 1", "Field stress: 2"]}
THERMAL_TYPES = ["Ignore temperature: 0", "Earth gradient: 1", "Use temperatures from previous phase: 2", "Steady state thermal flow: 3"]
DISPL_GROUPS = (("LineDisplacements", "line"), ("SurfaceDisplacements", "surface"), ("PointDisplacements", "point"))


def _stage_label(ph: dict) -> str:
    return str(ph.get("id") or ph["name"]).replace(":", " -")


def p2_candidates(m: dict, add) -> None:
    """Lengths of anchors / piles / bolts / cables, slope angle and fill height of inclined lines, the stage a soil
    cluster is placed or excavated in (staged fill / excavation sequence), local mesh refinement, prescribed
    displacements, the calculation type of each phase (consolidation, fully coupled), the initial stress method,
    thermal calculation, rainfall per stage and a load factor per stage."""
    v = m["version"]
    vi = 2 if v == "3d" else 1
    axes = m["axes"]
    pts = {p["name"]: p for p in m["points"]}
    xyz = lambda p: [float(p["x"]), float(p["y"]), float(p.get("z", 0.0))]
    walls = {n for st in m["structures"] if st["kind"] == "plate" for n in _point_names(m, st.get("points"))}
    ids = {ph["name"]: _stage_label(ph) for ph in m.get("phases") or []}
    # 1. length of a two-point structure: its free end moves along the structure (the end on a wall stays)
    for st in m["structures"]:
        if st["kind"] not in ("anchor", "embedded_beam", "geogrid", "cable", "beam"):
            continue
        names = _point_names(m, st.get("points"))
        if len(names) != 2 or len(st.get("points") or []) != 2:
            continue
        a, b = names if names[1] not in walls else names[::-1]  # b = the free end
        pa, pb = xyz(pts[a]), xyz(pts[b])
        L0 = math.dist(pa, pb)
        if L0 < 1e-6:
            continue
        u = [(pb[k] - pa[k]) / L0 for k in range(3)]
        # a grouted anchor: the grout body (geogrid / embedded beam) at the free end moves with it, its length kept
        carried = [] if st["kind"] not in ("anchor", "cable") else \
            [n for o in m["structures"] if o is not st and o["kind"] in ("geogrid", "embedded_beam")
             for ends in [_point_names(m, o.get("points"))] if len(ends) == 2 and b in ends for n in ends if n != b]
        refs = [f"{p}.{ax}+*{u[k]:.6g}~{L0:.6g}" for p in [b] + carried for k, ax in enumerate("xyz") if ax in axes and abs(u[k]) > 1e-9]
        add(f"len:{st['name']}", "structures", "Length of anchors, piles, bolts and cables",
            f"{st['name']} {'free ' if carried else ''}length (end {b} moves{', with the grout body' if carried else ''}; {a} stays)",
            "; ".join(refs), "m", round(L0, 4))
    # 2. slopes and embankments: the angle and the height of an inclined line (its toe stays)
    on_struct = [set(_point_names(m, st.get("points"))) for st in m["structures"]]
    for ln in m.get("lines") or []:
        a, b = pts.get(ln.get("a")), pts.get(ln.get("b"))
        if not a or not b:
            continue
        if any({ln["a"], ln["b"]} <= sp for sp in on_struct if len(sp) >= 2):
            continue  # an anchor / strut / geogrid line is not a slope (its length is its own input)
        pa, pb = xyz(a), xyz(b)
        dh = math.hypot(pb[0] - pa[0], pb[1] - pa[1]) if v == "3d" else abs(pb[0] - pa[0])
        dv = pb[vi] - pa[vi]
        if dh < 1e-6 or abs(dv) < 1e-6 or (v == "3d" and abs(pb[1] - pa[1]) > 1e-6):
            continue  # vertical, horizontal, or (3D) not in an x-z section
        top, toe = (ln["b"], ln["a"]) if dv > 0 else (ln["a"], ln["b"])
        pt, po = xyz(pts[top]), xyz(pts[toe])
        h0, ang = abs(dv), math.degrees(math.atan2(abs(dv), dh))
        sx = 1.0 if pt[0] > po[0] else -1.0
        add(f"slope:{ln['name']}", "structures", "Embankments and slopes", f"{describe({'name': ln['name'], 'kind': 'line', 'points': [toe, top]})} slope angle",
            f"{top}|{toe}.slopeangle", "°", round(ang, 3))
        add(f"height:{ln['name']}", "structures", "Embankments and slopes",
            f"{describe({'name': ln['name'], 'kind': 'line', 'points': [toe, top]})} height (angle kept)",
            f"{top}.{axes[vi]}+~{h0:.6g}; {top}.x+*{sx / math.tan(math.radians(ang)):.6g}~{h0:.6g}", "m", round(h0, 4))
    # 3. staged fill / excavation sequence: the stage a soil cluster is placed in or excavated in
    stages = [ph for ph in (m.get("phases") or [])[1:] if not is_safety_phase(ph)]
    clusters = m.get("clusters") or []
    if stages and len(clusters) <= 60:
        vals = ["as in the model: 0"] + [f"{_stage_label(ph)}: {i}" for i, ph in enumerate(stages, 1)]
        for cl in clusters:
            add(f"place:{cl}", "stages", "Staged fill: stage each cluster is placed in", f"{cl} placed in stage",
                f"{cl}.PlacedIn", "", vals[0], kind="values", values=vals)
            add(f"dig:{cl}", "stages", "Excavation sequence: stage each cluster is excavated in", f"{cl} excavated in stage",
                f"{cl}.ExcavatedIn", "", vals[0], kind="values", values=vals)
    # 4. local mesh refinement around a structure (coarseness factor)
    for st in m["structures"]:
        add(f"cf:{st['name']}", "mesh", "Local mesh refinement (coarseness factor)", f"{st['name']} coarseness factor",
            f"{st['name']}.CoarsenessFactor", "×", 1.0)
    # 5. prescribed displacements
    for d in m.get("displacements") or []:
        for prop, val in (d.get("props") or {}).items():
            add(f"displ:{d['name']}.{prop}", "structures", "Prescribed displacements", f"{d['name']} {prop}",
                f"{d['name']}.displ:{prop}", "m", val)
    # 6. phases: calculation type, initial stresses, thermal, rainfall, load factor
    ph0 = (m.get("phases") or [None])[0]
    if ph0:
        it = INITIAL_TYPES[v]
        cur = next((x for x in it if x.split(":")[0].lower() in calc_type_name(ph0.get("type")).lower()), it[0])
        add("init:calc", "stages", "Initial stresses", "Initial stress calculation (initial phase)",
            f"{ph0['name']}.InitialCalcType", "", cur, kind="values", values=list(it))
    for ph in stages:
        cur = next((x for x in CALC_TYPES if x.split(":")[0].lower() in calc_type_name(ph.get("type")).lower()), CALC_TYPES[0])
        add(f"calc:{ph['name']}", "stages", "Calculation type per stage", f"Calculation type of {ids[ph['name']]}",
            f"{ph['name']}.CalcType", "", cur, kind="values", values=list(CALC_TYPES))
        add(f"lfactor:@{ph['name']}", "stages", "Load factor per stage (scenarios)", f"Load factor in {ids[ph['name']]} (all loads)",
            f"Loads.factor@{ph['name']}", "×", 1.0)
        add(f"rain:@{ph['name']}", "flow", "Rainfall (precipitation) per stage", f"Rainfall q in {ids[ph['name']]}",
            f"Precipitation.q@{ph['name']}", "m/day", 0.0)
    for ph in [ph0] + stages if ph0 else []:
        cur = next((x for x in THERMAL_TYPES if x.split(":")[0].lower() == str(ph.get("thermal") or "").lower()), THERMAL_TYPES[0])
        add(f"thermal:{ph['name']}", "stages", "Thermal calculation (PLAXIS Thermal)", f"Thermal calculation in {ids[ph['name']]}",
            f"{ph['name']}.ThermalCalcType", "", cur, kind="values", values=list(THERMAL_TYPES))
