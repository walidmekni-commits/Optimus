"""V26.1.9: PLAXIS features chosen for a study (QA/QC "Report for Missing Features", P1 items for excavation and
wall design like UA-22).

They are kept in the study (setup["study"]) and give extra input parameters and extra PLAXIS results:
    safety     {"after": [phase names]}                      a safety phase (φ/c reduction) added after each chosen
                                                             stage in every run: the factor of safety per stage
    probes     [{"name", "x", "y", "z"}]                     results at chosen points (e.g. a building corner)
    added      [{"id", "kind", "a", "b", "material", "phase", "width", "prestress"}]
                                                             new structures (anchor row, strut level, extra pile)
                                                             drawn from two points; a variable adds them in a run
    dynamic    {"on", "after", "source", "amplitude", "frequency", "duration"}
                                                             a dynamic phase (harmonic load or base shaking)
    tunnel     {"d"}                                         tunnel diameter, for the volume loss VL
The PLAXIS side is in app.solvers (PlaxisSolver.safety_after / additions / dynamic).
"""
import re

KINDS = {"anchor": "Node-to-node anchor", "plate": "Plate (wall / strut level)", "embedded_beam": "Embedded beam (pile)",
         "geogrid": "Geogrid (grout body)", "beam": "Beam (strut / waling, 3D)"}
SAFETY_ID = "OPTIMUS safety after"   # Identification of the safety phases OPTIMUS adds
DYNAMIC_ID = "OPTIMUS dynamic"        # Identification of the dynamic phase OPTIMUS adds
SAFETY_PHASE = "@safety:"             # target phase: the safety phase added after the stage that follows
DYNAMIC_PHASE = "@dynamic"            # target phase: the dynamic phase added by OPTIMUS
PROBE_QTY = [("utot", "|u|", "Soil.Utot"), ("ux", "ux", "Soil.Ux"), ("uv", None, None)]


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _xyz(p: dict, is3: bool) -> list[float] | None:
    vals = [_f((p or {}).get(k)) for k in ("x", "y", "z")]
    if vals[0] is None or vals[1] is None or (is3 and vals[2] is None):
        return None
    return [vals[0], vals[1], vals[2] or 0.0]


def clean(s: dict) -> dict:
    """The feature options of a study, tidied (unknown kinds, empty points and bad numbers dropped)."""
    out = {}
    sf = s.get("safety") or {}
    out["safety"] = {"after": [str(x) for x in sf.get("after") or [] if x]}
    out["probes"] = []
    for i, p in enumerate(s.get("probes") or []):
        if _f(p.get("x")) is None or _f(p.get("y")) is None:
            continue
        out["probes"].append({"name": str(p.get("name") or f"Point {i + 1}")[:40], "x": _f(p.get("x")), "y": _f(p.get("y")),
                              "z": _f(p.get("z")) or 0.0})
    out["added"] = []
    for i, a in enumerate(s.get("added") or []):
        if a.get("kind") not in KINDS:
            continue
        out["added"].append({"id": re.sub(r"\W", "", str(a.get("id") or f"N{i + 1}"))[:12] or f"N{i + 1}", "kind": a["kind"],
                             "a": a.get("a") or {}, "b": a.get("b") or {}, "material": str(a.get("material") or ""),
                             "phase": str(a.get("phase") or ""), "width": _f(a.get("width")), "prestress": _f(a.get("prestress"))})
    d = s.get("dynamic") or {}
    out["dynamic"] = {"on": bool(d.get("on")), "after": str(d.get("after") or ""), "source": str(d.get("source") or "base"),
                      "amplitude": _f(d.get("amplitude")) if _f(d.get("amplitude")) is not None else 0.01,
                      "frequency": _f(d.get("frequency")) or 2.0, "duration": _f(d.get("duration")) or 5.0}
    out["tunnel"] = {"d": _f((s.get("tunnel") or {}).get("d"))}
    return out


def added_spec(m: dict, a: dict) -> dict | None:
    """A new structure as the solver draws it: kind, two points in PLAXIS coordinates, material, first stage."""
    is3 = m.get("version") == "3d"
    pa, pb = _xyz(a.get("a"), is3), _xyz(a.get("b"), is3)
    if not pa or not pb or pa == pb:
        return None
    phases = [ph["name"] for ph in (m.get("phases") or [])[1:]]
    return {"id": a["id"], "name": f"New_{a['id']}", "kind": a["kind"], "a": pa, "b": pb, "material": a.get("material") or "",
            "phase": a.get("phase") if a.get("phase") in phases else (phases[0] if phases else ""),
            "width": a.get("width"), "prestress": a.get("prestress")}


def candidates(m: dict, s: dict) -> list[dict]:
    """Input parameters given by the study's features: add each new structure (and shift its level), the dynamic
    load's amplitude, frequency and duration."""
    o = clean(s)
    is3 = m.get("version") == "3d"
    up = "z" if is3 else "y"
    out = []

    def add(key, group, label, ref, unit, current, kind="number", **kw):
        out.append({"key": key, "section": kw.pop("section", "structures"), "group": group, "label": label,
                    "plaxis_ref": ref, "unit": unit, "current": current, "kind": kind, **kw})
    ids = {ph["name"]: ph.get("id") or ph["name"] for ph in m.get("phases") or []}
    for a in o["added"]:
        sp = added_spec(m, a)
        if not sp:
            continue
        what = f"new {KINDS[a['kind']].split(' (')[0].lower()} {a['id']}"
        add(f"add:{a['id']}", "New structures", f"Add the {what} (from {ids.get(sp['phase'], sp['phase']) or 'the first stage'})",
            f"{sp['name']}.add", "", "not added: 0", kind="values", values=["not added: 0", "added: 1"])
        add(f"addshift:{a['id']}", "New structures", f"Level of the {what}: shift in {up}", f"{sp['name']}.shift{up}",
            "m", 0.0)
    d = o["dynamic"]
    if d["on"]:
        unit = "m" if d["source"] == "base" else ("kN/m²" if is3 else "kN/m/m")
        add("dyn:amplitude", "Dynamic load", "Dynamic amplitude" + (" (base displacement)" if d["source"] == "base" else f" ({d['source']})"),
            "Dynamic.amplitude", unit, d["amplitude"], section="stages")
        add("dyn:frequency", "Dynamic load", "Dynamic frequency (harmonic)", "Dynamic.frequency", "Hz", d["frequency"], section="stages")
        add("dyn:duration", "Dynamic load", "Dynamic time interval", "Dynamic.duration", "s", d["duration"], section="stages")
    return out


def outputs(m: dict, s: dict) -> list[dict]:
    """PLAXIS results given by the study's features: the displacements at each chosen point, the volume loss, the
    dynamic response."""
    o = clean(s)
    is3 = m.get("version") == "3d"
    up = "Uz" if is3 else "Uy"
    out = []

    def add(key, label, result, unit, phase_hint=""):
        out.append({"key": key, "label": label, "result": result, "unit": unit, "phase_hint": phase_hint,
                    "operator": "report", "value": None})
    for i, p in enumerate(o["probes"]):
        at = f"{p['x']:g};{p['y']:g}" + (f";{p['z']:g}" if is3 else "")
        for q, lab, res in PROBE_QTY:
            lab = lab or ("uz" if is3 else "uy")
            res = res or f"Soil.{up}"
            add(f"probe{i}:{q}", f"{lab} at {p['name']} ({at.replace(';', ', ')})", f"{res}|at {at}", "m")
    d = (o["tunnel"] or {}).get("d")
    if d or m.get("tunnels") or m.get("contractions"):
        if d:
            add("vl", f"Volume loss VL (tunnel D = {d:g} m)", f"Soil.{up}|trough:{d:g}", "%")
    if o["dynamic"]["on"]:
        add("dyn_ax", "Max horizontal acceleration ax (dynamic phase)", "Soil.Ax", "m/s²", "dynamic")
        add("dyn_vx", "Max horizontal velocity vx (dynamic phase)", "Soil.Vx", "m/s", "dynamic")
        add("dyn_ux", "Max horizontal displacement ux (dynamic phase)", "Soil.Ux", "m", "dynamic")
    return out


def out_domain(key: str) -> str | None:
    if key.startswith("probe"):
        return "points"
    if key in ("geogrid_n", "skin", "beam_n", "beam_m", "beam_m3", "iface_sn", "iface_tau", "iface_rel"):
        return "forces"
    if key in ("q", "head", "psteady", "pactive"):
        return "water"
    if key in ("trough", "vl"):
        return "displacements"
    if key.startswith("dyn_"):
        return "dynamics"
    if key in ("settle_profile", "wall_profile"):  # V26.1.9 (batch 2)
        return "displacements"
    if key == "temp":
        return "thermal"
    return None


def solver_options(m: dict, s: dict) -> dict:
    """What the PLAXIS solver adds in every run of the study (safety phases, new structures, the dynamic phase)."""
    o = clean(s)
    names = [ph["name"] for ph in m.get("phases") or []]
    return {"safety_after": [x for x in o["safety"]["after"] if x in names],
            "additions": {sp["name"]: sp for sp in (added_spec(m, a) for a in o["added"]) if sp},
            "dynamic": o["dynamic"] if o["dynamic"]["on"] and o["dynamic"]["after"] in names else None}


def describe(m: dict, s: dict) -> list[list[str]]:
    """The study's PLAXIS features as rows [feature, setting] for the reports (Word, Excel) and the study page."""
    o = clean(s or {})
    is3 = (m or {}).get("version") == "3d"
    ids = {ph["name"]: ph.get("id") or ph["name"] for ph in (m or {}).get("phases") or []}
    mats = {x["name"]: x.get("id") or x["name"] for x in (m or {}).get("materials") or []}
    xyz = lambda p: ", ".join(f"{(p or {}).get(k):g}" for k in (("x", "y", "z") if is3 else ("x", "y"))
                              if isinstance((p or {}).get(k), (int, float)))
    rows = []
    if o["safety"]["after"]:
        rows.append(["Factor of safety per stage", "a safety phase after " + ", ".join(ids.get(x, x) for x in o["safety"]["after"])
                     + " in every run"])
    for p in o["probes"]:
        rows.append([f"Result point {p['name']}", f"({xyz(p)}): |u|, ux, {'uz' if is3 else 'uy'}"])
    for a in o["added"]:
        sp = added_spec(m or {}, a)
        txt = f"{KINDS[a['kind']]} from ({xyz(a.get('a'))}) to ({xyz(a.get('b'))})"
        if sp:
            txt += f", material {mats.get(sp['material'], sp['material']) or 'PLAXIS default'}, active from {ids.get(sp['phase'], sp['phase']) or 'the first stage'}"
            if a.get("prestress") is not None and a["kind"] == "anchor":
                txt += f", prestress {a['prestress']:g} kN"
        else:
            txt += " (incomplete: not drawn)"
        rows.append([f"New structure New_{a['id']}", txt])
    d = o["dynamic"]
    if d["on"]:
        rows.append(["Dynamic phase", f"harmonic, {'base shaking' if d['source'] == 'base' else 'on ' + d['source']}, after "
                     f"{ids.get(d['after'], d['after']) or '(no stage chosen)'}: amplitude {d['amplitude']:g}, "
                     f"{d['frequency']:g} Hz, {d['duration']:g} s, viscous boundaries"])
    if (o["tunnel"] or {}).get("d"):
        rows.append(["Tunnel diameter (volume loss)", f"D = {o['tunnel']['d']:g} m"])
    return rows


def points(m: dict, s: dict) -> dict:
    """What the views draw for the study's features: the result points and the new structures (PLAXIS coordinates)."""
    o = clean(s or {})
    return {"probes": [{"name": p["name"], "at": [p["x"], p["y"], p["z"]]} for p in o["probes"]],
            "added": [{"name": sp["name"], "kind": sp["kind"], "a": sp["a"], "b": sp["b"]}
                      for sp in (added_spec(m or {}, a) for a in o["added"]) if sp]}


def probe_checks(m: dict, s: dict) -> list[str]:
    """QA V26.1.9 D7: each result point checked against the model before the runs: outside the model, above the
    ground, on a structure (the soil value next to it is read)."""
    from .plaxis_model import _inside
    o = clean(s or {})
    is3 = (m or {}).get("version") == "3d"
    e = (m or {}).get("extents") or {}
    out = []
    for p in o["probes"]:
        x, y, z = p["x"], p["y"], p["z"]
        up = z if is3 else y
        lab = f"Result point {p['name']} ({x:g}, {y:g}" + (f", {z:g})" if is3 else ")")
        span = [("x", x, e.get("xmin"), e.get("xmax")), ("y", y, e.get("ymin"), e.get("ymax"))] + \
               ([("z", z, e.get("zmin"), e.get("zmax"))] if is3 else [])
        bad = [a for a, v, lo, hi in span if lo is not None and hi is not None and not (lo - 1e-6 <= v <= hi + 1e-6)]
        if bad:
            out.append(f"{lab}: outside the model ({', '.join(bad)} beyond its extents): PLAXIS will give no value.")
            continue
        if not is3 and m.get("soil_polygons"):
            if not any(_inside(x, y - 1e-3, [(float(q[0]), float(q[1])) for q in sp.get("points") or []]) for sp in m["soil_polygons"]):
                out.append(f"{lab}: not inside any soil cluster of the model: PLAXIS may give no value.")
        elif is3 and m.get("boreholes"):
            top = max((lay["top"] for b in m["boreholes"] for lay in b.get("layers") or []), default=None)
            if top is not None and up > top + 1e-6:
                out.append(f"{lab}: above the ground surface (z {top:g}): PLAXIS will give no value.")
        for st in m.get("structures") or []:
            pts = st.get("points") or []
            if len(pts) == 2 and _near_segment((x, y, z), pts[0], pts[1], is3) < 0.02:
                out.append(f"{lab}: on {st['name']}: the soil value next to it is read; move it 0.1 m off the structure "
                           "for the soil alone.")
                break
    return out


def _near_segment(p, a, b, is3: bool) -> float:
    import math
    a = [float(v) for v in (list(a) + [0, 0])[:3]]
    b = [float(v) for v in (list(b) + [0, 0])[:3]]
    p = [float(v) for v in p]
    if not is3:
        p = [p[0], p[1], 0.0]
        a[2] = b[2] = 0.0
    d = [b[i] - a[i] for i in range(3)]
    L2 = sum(c * c for c in d) or 1e-12
    t = max(0.0, min(1.0, sum((p[i] - a[i]) * d[i] for i in range(3)) / L2))
    return math.dist(p, [a[i] + t * d[i] for i in range(3)])
