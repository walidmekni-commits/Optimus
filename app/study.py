"""The four studies of the app: Sensitivity analysis, Design optimization, Design modification, Design scenarios.

A study is kept in the project's setup["study"]:
    mode          sensitivity | optimize | design_change | combinations
    params        [{item, min, max, values, custom, how, new}]   the variable input criteria (PLAXIS model items)
    controls      [{output, how, value, min, max}]               sensitivity: control parameters (PLAXIS outputs)
    targets       [{output, operator, value}]                     optimization: target optimization criteria
    outputs       [output keys]                                   modification / scenarios: outputs to compare
    runs          the total number of runs, Run 0 (the existing design) included; a sensitivity shares them between its parameters
    distribution  even | custom
    baseline      Run 0 = the existing model
build() turns it into the variables, targets and runs the engine calculates; results() reads them back as a
summary, a table of three columns and figures. Excel and Word files are made by app.exports.
"""
import math

from . import features, geocheck, geosel, plaxis_model
from .planner import numeric, parse_value, show

MODES = {
    "sensitivity": {"title": "Sensitivity analysis",
                    "hint": "How the PLAXIS results respond when one input changes."},
    "optimize": {"title": "Design optimization",
                 "hint": "Find the input values that meet the target criteria."},
    "design_change": {"title": "Design modification",
                      "hint": "Compare the existing design with a modified one."},
    "combinations": {"title": "Design scenarios",
                     "hint": "Run a set of scenarios and summarize them."},
    "calibration": {"title": "Calibration / Back-analysis",
                    "hint": "Find the soil or model inputs whose PLAXIS results match the measured values."},
    "reporting": {"title": "Advanced reporting",
                  "hint": "Calculate the existing design once and report its results in full detail."},
}
DOMAINS = {"geometry": "Geometry", "structures": "Structures", "loads": "Loads", "soil": "Soil",
           "water": "Water conditions", "mesh": "Mesh", "stages": "Stages", "thermal": "Thermal",
           "features": "PLAXIS features"}
OUT_DOMAINS = {"safety": "Safety", "displacements": "Displacements", "forces": "Structural forces",
               "water": "Pore pressure and flow", "points": "Results at chosen points", "dynamics": "Dynamics",
               "thermal": "Thermal"}
OPERATORS = [">=", "<=", ">", "<"]


def domain_of(c: dict) -> str:
    k = c["key"]
    if k.startswith(("pt:", "move:", "geo:", "area:", "corner:", "slope:", "height:", "len:")) or (k.startswith("del:") and c.get("group") == "Remove a point"):
        return "geometry"  # the surcharge area is geometry (Area / Line), its values stay under Loads
    if k.startswith("load:"):
        return "loads"
    if k.startswith("thermal:"):
        return "thermal"
    if k.startswith(("prestress:", "design:", "iface:", "add:", "addshift:", "dyn:", "contraction:")):
        return "features"  # QA V26.1.9 D14: the PLAXIS features of the study grouped in section 2
    return {"soil": "soil", "flow": "water", "mesh": "mesh", "stages": "stages", "thermal": "thermal"}.get(c["section"], "structures")


def geo_kind(c: dict, m: dict, kinds: dict) -> str | None:
    """Point, Line, Area or Volume: how a geometry parameter is seen in the model."""
    k = c["key"]
    if k.startswith("pt:") and c.get("loads"):  # a corner of a surcharge load: seen as that load's area (or length)
        ld = next((x for x in m.get("loads") or [] if x["name"] == c["loads"][0][0]), {})
        return "area" if m.get("version") == "3d" and len(ld.get("points") or []) > 2 else "line"
    if k.startswith("pt:") or (k.startswith("del:") and c.get("group") == "Remove a point"):
        return "point"
    if k.startswith(("area:", "corner:")):
        return "area" if m.get("version") == "3d" and len(c.get("corners") or []) > 2 else "line"
    if k.startswith("move:"):
        o = kinds.get(k[5:].rsplit(".", 1)[0]) or {}
        return "area" if m.get("version") == "3d" and len(o.get("points") or []) > 2 else "line"
    if k.startswith(("geo:", "slope:", "height:", "len:")):
        return "line"
    if k.startswith("layer:"):
        return "volume"
    return None


def out_domain(key: str) -> str | None:
    if key == "fos":
        return "safety"
    if key in ("utot", "ux", "uv", "settle", "wall_ux"):
        return "displacements"
    if key in ("plate_m", "plate_q", "plate_n", "anchor", "pile_n"):
        return "forces"
    if key == "pexcess":
        return "water"
    return features.out_domain(key)  # V26.1.9


def all_candidates(m: dict, s: dict | None = None) -> list[dict]:
    """The model's input parameters and those of the study's PLAXIS features (V26.1.9: new structures, dynamics)."""
    return plaxis_model.candidates(m) + features.candidates(m, s or {})


def all_outputs(m: dict, s: dict | None = None) -> list[dict]:
    """PLAXIS' results and those of the study's features (V26.1.9: results at chosen points, volume loss, dynamics)."""
    return plaxis_model.outputs(m["version"]) + features.outputs(m, s or {})


def catalog(m: dict, s: dict | None = None) -> dict:
    """Everything the study page offers: the model's input parameters and PLAXIS' outputs, by domain."""
    params = []
    kinds = {o["name"]: o for o in plaxis_model.objects(m)}
    for c in all_candidates(m, s):
        p = {"key": c["key"], "label": c["label"], "unit": c.get("unit") or "", "current": c["current"],
             "kind": c["kind"], "domain": domain_of(c), "group": c["group"], "ref": c["plaxis_ref"]}
        gk = geo_kind(c, m, kinds)
        if gk:
            p["gk"] = gk
        if c.get("loads"):
            p["loads"] = c["loads"]  # [[load, corner]]: the point is a corner of these surcharge loads
        if c["kind"] == "mesh":
            p["values"] = plaxis_model.mesh_values(m["version"])
        if c["kind"] == "remove":
            p["values"] = ["keep: 0", "remove: 1"]
        if c["kind"] == "values":  # the soil model (V26.1.8)
            p["values"] = c.get("values") or []
        params.append(p)
    outs = [{**o, "domain": out_domain(o["key"])} for o in all_outputs(m, s) if out_domain(o["key"])]
    phases = [{"name": name, "label": lab, "day": d} for name, (d, lab) in schedule(m, {}).items()]
    allph = [{"name": ph["name"], "label": ph.get("id") or ph["name"], "type": plaxis_model.calc_type_name(ph.get("type")) if ph.get("type") not in (None, "") else ""}
             for ph in (m.get("phases") or [])]
    mats = [{"name": x["name"], "id": x.get("id") or x["name"], "kind": x["kind"]} for x in m.get("materials") or []]
    return {"modes": MODES, "domains": DOMAINS, "out_domains": OUT_DOMAINS, "operators": OPERATORS,
            "params": params, "outputs": outs, "phases": phases, "all_phases": allph, "materials": mats,
            "loads": [x["name"] for x in m.get("loads") or []], "kinds": features.KINDS,
            "has": {k: len(m.get(k) or []) for k in ("interfaces", "tunnels", "contractions", "wells", "drains")}}


def default(mode: str) -> dict:
    return {"mode": mode if mode in MODES else "sensitivity", "params": [], "controls": [], "targets": [],
            "outputs": [], "measured": [], "geo": [], "detector": {"on": False, "fields": []},
            "screen": {"on": False, "domain": "loads", "pct": 20, "shift": 1.0, "items": []}, "runs": 5, "distribution": "even", "baseline": True, "swap": True,
            "report": {"vis": ["figures", "tables", "output", "geometry"], "plots": ["utot", "ux"]}}


def clean(study: dict | None, mode: str | None = None) -> dict:
    s = {**default(mode or (study or {}).get("mode") or "sensitivity"), **(study or {})}
    if mode:
        s["mode"] = mode
    s["runs"] = max(1, min(200, int(s.get("runs") or 1)))
    if s["distribution"] not in ("even", "custom"):
        s["distribution"] = "even"
    return s


# --- the runs -----------------------------------------------------------------------------------------------
def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _r(x: float) -> float:
    return float(f"{x:.6g}")


def fmt(x) -> str:
    if isinstance(x, float):
        if 1e3 <= abs(x) < 1e7:  # 18700 kN/m², not 1.87e+04
            return f"{x:.0f}"
        return f"{x:.4g}" if abs(x) < 1e5 else f"{x:.3e}"
    return str(x)


def value_text(var: dict, val) -> str:
    if isinstance(val, (list, tuple)):
        return f"{val[0]}"
    return f"{fmt(val)}{(' ' + var['unit']) if var.get('unit') and var['unit'] != '-' else ''}"


def _even(p: dict, var: dict, n: int) -> list:
    if var["kind"] == "values":
        levels = [x for x in (p.get("values") or var["values"]) if x in var["values"]] or var["values"]
        if n >= len(levels):
            return list(levels)
        return [levels[round(k * (len(levels) - 1) / max(1, n - 1))] for k in range(n)] if n > 1 else [levels[-1]]
    lo, hi = _f(p.get("min")), _f(p.get("max"))
    if lo is None or hi is None:
        lo, hi = var["min"], var["max"]
    if n == 1:
        return [_r(hi)]
    return [_r(lo + (hi - lo) * k / (n - 1)) for k in range(n)]


def value_list(p: dict) -> list | None:
    """The proposed values typed as a list ("8, 10, 12"), or None when the parameter uses a range."""
    if p.get("how") != "list":
        return None
    import re
    vals = [_f(x) for x in re.split(r"[,;\s]+", str(p.get("list") or "")) if x.strip()]
    return [_r(v) for v in vals if v is not None]


def param_values(p: dict, var: dict, n: int, distribution: str) -> list:
    """The value of one parameter in each of n runs (None keeps the existing value)."""
    lst = value_list(p) if var["kind"] != "values" else None
    if lst is not None and distribution != "custom":
        return [lst[k] if k < len(lst) else None for k in range(n)]
    if distribution == "custom" or p.get("how") == "custom":
        col = list(p.get("custom") or [])
        out = []
        for k in range(n):
            x = col[k] if k < len(col) else ""
            if x in (None, ""):
                out.append(None)
            elif var["kind"] == "values":
                out.append(next((v for v in var["values"] if v.split(":")[0].strip().lower() == str(x).split(":")[0].strip().lower()), None))
            else:
                out.append(_f(x))
        return out
    return _even(p, var, n)


def _encode(var: dict, val):
    return parse_value(val) if var["kind"] == "values" else val


def _phase_for(o: dict, m: dict) -> str:
    if o.get("phase_hint") == "dynamic":
        return features.DYNAMIC_PHASE
    if o.get("phase_hint") == "safety":
        return next((ph["name"] for ph in m.get("phases") or [] if plaxis_model.is_safety_phase(ph)), "")
    return ""


MAX_MODS = 20  # Rev B W1.3: Number of modifications, 1 to 20


def new_values(p: dict) -> list:
    """Design modification: the new values of an item (one Puzzle box each). "news" is the list; "new" (one value) is
    how a study saved before Rev B gave it, and stays the first value."""
    vals = p.get("news")
    if not isinstance(vals, list) or not [x for x in vals if x not in (None, "")]:
        vals = [p.get("new")]
    return [x for x in vals if x not in (None, "")]


def _dc_value(var: dict, x):
    if var["kind"] == "values":
        return next((v for v in var["values"] if v.split(":")[0].strip().lower() == str(x).split(":")[0].strip().lower()), None)
    return _f(x)


def mod_count(s: dict, longest: int) -> int:
    """The number of modifications: the one asked for in the Runs box, else one per new value of the longest item."""
    n = int(_f(s.get("mods")) or 0) or longest
    return max(1, min(MAX_MODS, n))


def build(m: dict, study: dict) -> dict:
    """Variables, targets and runs of the study, the scheduled runs (3 columns) and what is still missing."""
    s = clean(study)
    cands = {c["key"]: c for c in all_candidates(m, s)}
    for e in s.get("geo") or []:  # geometry selected in the 3D view (app.geosel)
        c = geosel.candidate(m, e, e.get("mode") or "x")
        if c:
            cands[c["key"]] = c
    outs = {o["key"]: o for o in all_outputs(m, s)}
    errors, variables, params, warnings = [], [], [], []
    geo = {"grp:" + e["id"]: e for e in s.get("geo") or [] if e.get("id")}
    screen = s.get("screen") or {}
    screening = s["mode"] == "sensitivity" and bool(screen.get("on"))
    for i, p in enumerate([] if screening else s["params"]):
        c = cands.get(p.get("item"))
        if not c:
            continue
        lst = value_list(p) if p.get("item") else None
        if lst is not None and var_kind_range(c):
            if not lst:
                errors.append(f"Give the proposed values of {c['label']}, separated by commas.")
            else:
                p = {**p, "min": min(lst), "max": max(lst)}
        if p["item"] in geo and (geo[p["item"]].get("ranges") or {}):  # each item with its own range
            c = geosel.candidate(m, geo[p["item"]], geo[p["item"]].get("mode") or "x", (_f(p.get("min")), _f(p.get("max")))) or c
        var = plaxis_model.variable_from(c, f"p{len(variables) + 1}", m["version"])
        if var["kind"] == "range":
            lo, hi = _f(p.get("min")), _f(p.get("max"))
            if lo is not None and hi is not None:
                var["min"], var["max"] = min(lo, hi), max(lo, hi)
        variables.append(var)
        params.append((p, var))
    if screening:  # Sensitivity Detector of the inputs: each item changed alone by the same step
        for key in screen.get("items") or []:
            c = cands.get(key)
            if not c or c.get("kind") in ("mesh", "remove") or c.get("values"):
                continue
            var = plaxis_model.variable_from(c, f"p{len(variables) + 1}", m["version"])
            variables.append(var)
            params.append(({"item": key}, var))
        if not params:
            errors.append("Sensitivity Detector: tick at least one item to check.")
    elif not params and s["mode"] != "reporting":
        errors.append("Choose at least one variable input parameter.")

    targets = []

    def add_target(key, operator="report", value=None, extra=None):
        o = outs.get(key)
        if not o:
            return
        targets.append({"name": f"T{len(targets) + 1}", "label": o["label"], "result": o["result"],
                        "phase": _phase_for(o, m), "operator": operator, "value": value, "unit": o["unit"],
                        "key": key, **(extra or {})})

    mode, n = s["mode"], s["runs"]
    if mode == "sensitivity":
        for c in s["controls"]:
            add_target(c.get("output"), extra={"how": c.get("how") or "fixed", "ref": _f(c.get("value")),
                                               "lo": _f(c.get("min")), "hi": _f(c.get("max")), "control": True})
            stat = c.get("stat") or "max"
            if targets and targets[-1]["key"] == c.get("output") and stat in STATS and stat != "max" \
                    and c.get("output") not in ("fos", "file"):
                t = targets[-1]  # the minimum / average of the result over its nodes instead of the maximum
                t["result"] += "#" + stat
                lab = t["label"].replace("Max ", "").replace("max ", "")
                t["label"] = f"{lab[:1].upper()}{lab[1:]} ({STATS[stat]})"
                t["stat"] = stat
        det = s.get("detector") or {}
        if det.get("on"):  # Sensitivity Detector of the results: every chosen PLAXIS result is read in every run
            have = {t["key"] for t in targets}
            for key in det.get("fields") or [o for o in outs if o != "file"]:
                if key in outs and key != "file" and key not in have:
                    add_target(key, extra={"detector": True})
                    have.add(key)
        if not targets:
            errors.append("Choose at least one control parameter (a PLAXIS result), or turn on the Sensitivity Detector.")
    elif mode == "optimize":
        for t in s["targets"]:
            val = _f(t.get("value"))
            if t.get("output") and val is None:
                errors.append(f"Give a value for the target {outs.get(t['output'], {}).get('label', '')}.")
                continue
            add_target(t.get("output"), t.get("operator") if t.get("operator") in OPERATORS else ">=", val)
        if not targets:
            errors.append("Choose at least one target optimization criterion.")
    elif mode == "calibration":
        for t in s.get("measured") or []:
            val = _f(t.get("value"))
            if t.get("output") and val is None:
                errors.append(f"Give the measured value of {outs.get(t['output'], {}).get('label', '')}.")
                continue
            add_target(t.get("output"), extra={"measured": val})
        if not targets:
            errors.append("Choose at least one measured result (a PLAXIS result and its measured value).")
    else:
        for key in s["outputs"]:
            add_target(key)
        if mode == "design_change":  # QA V26.1.8: optional acceptance criteria of the modified design
            for c in s.get("criteria") or []:
                val = _f(c.get("value"))
                t = next((t for t in targets if t["key"] == c.get("output")), None)
                if val is None:
                    continue
                if t is None:
                    add_target(c.get("output"), c.get("operator") if c.get("operator") in OPERATORS else "<=", val)
                else:
                    t["operator"], t["value"] = (c.get("operator") if c.get("operator") in OPERATORS else "<="), val
        if not targets:
            errors.append("Choose at least one PLAXIS result to " + ("report." if mode == "reporting" else "compare."))
    # V26.1.8: TIME in every module. Each stage has a date (days from the start of works) and an optional long-term
    # consolidation phase (e.g. 365 days) is added after the last stage in PLAXIS; the chosen results are read at
    # every date. Calibration can take a field time series (readings by day) as its benchmark.
    tm = s.get("time") or {}
    series = [x for x in (s.get("series") or []) if x.get("output") in outs and x.get("points")] if mode == "calibration" else []
    if tm.get("on") or series:
        sched = schedule(m, tm)
        lt = int(_f(tm.get("longterm")) or 0)
        dates = [(d, name, lab) for name, (d, lab) in sched.items()]
        if lt:
            last = max((d for d, _, _ in dates), default=0)
            dates.append((last + lt, LONGTERM, f"Long term ({lt} days)"))

        def at(key, d, name, lab, extra=None):
            o = outs[key]
            add_target(key, extra={"time": True, "stage": True, "day": d, "phase": name, "phase_label": lab,
                                   "label": f"{o['label']} @ {lab} (day {fmt(float(d))})", "base_label": o["label"], **(extra or {})})
        for sr in series:  # the benchmark: one measured value at each reading, put on the stage of that date
            for pt in sr["points"]:
                d, v = _f(pt.get("day")), _f(pt.get("value"))
                if d is None or v is None or not dates:
                    continue
                dd, name, lab = min(dates, key=lambda x: abs(x[0] - d))
                at(sr["output"], dd, name, lab, {"measured": v, "read_day": d})
        done = {sr["output"] for sr in series}
        keys = [k for k in (tm.get("outputs") or list(dict.fromkeys(t["key"] for t in targets if not t.get("time"))))
                if k in outs and k not in ("fos", "file") and k not in done and not k.startswith("dyn_")]
        if tm.get("on"):
            for key in keys:
                for d, name, lab in dates:
                    at(key, d, name, lab)
        if series and not any(t.get("measured") is not None and not t.get("time") for t in targets):
            errors[:] = [e for e in errors if "measured result" not in e]
    # QA V26.1.9 D3 / D9: a result point and the dynamic phase give their results without ticking them separately
    if mode != "calibration" and not screening:
        fo0 = features.clean(s)
        have = {t["key"] for t in targets}
        auto = [f"probe{i}:utot" for i in range(len(fo0["probes"])) if not any(k.startswith(f"probe{i}:") for k in have)]
        if fo0["dynamic"]["on"] and not any(k.startswith("dyn_") for k in have):
            auto += ["dyn_ax", "dyn_ux"]
        for key in auto:
            add_target(key, extra={"auto": True})
        warnings += features.probe_checks(m, s)  # QA V26.1.9 D7: each result point checked before the runs
        if auto:
            warnings.append("Read as well (PLAXIS features of the study): " + ", ".join(outs[k]["label"] for k in auto if k in outs) + ".")
    # QA V26.1.8: a factor of safety needs a safety phase. Without one PLAXIS gives ΣMsf = 1 for the last phase, which
    # was reported as "FoS 1" (reporting) or failed every optimization run after 10 minutes of calculation.
    # V26.1.9: a safety phase added by OPTIMUS after chosen stages: the factor of safety of each of those stages
    fo = features.clean(s)
    after = [ph for ph in (m.get("phases") or []) if ph["name"] in fo["safety"]["after"]]
    if after:
        fos = [t for t in targets if t["key"] == "fos" and not t.get("time")]
        proto = fos[0] if fos else None
        targets[:] = [t for t in targets if not (t["key"] == "fos" and not t.get("time"))]
        o = outs["fos"]
        for ph in after:
            targets.append({"name": f"T{len(targets) + 1}", "label": f"{o['label']} after {ph.get('id') or ph['name']}",
                            "result": o["result"], "phase": features.SAFETY_PHASE + ph["name"],
                            "operator": proto["operator"] if proto else "report", "value": proto.get("value") if proto else None,
                            "unit": o["unit"], "key": "fos", "safety_after": ph["name"],  # a main result (not a stage value): it is a control / target
                            **({k: proto[k] for k in ("how", "ref", "lo", "hi", "control", "measured", "detector") if k in proto} if proto else {})})
        for i, t in enumerate(targets, 1):
            t["name"] = f"T{i}"
    has_safety = bool(after) or any(plaxis_model.is_safety_phase(ph) for ph in m.get("phases") or [])
    if m.get("phases") and not has_safety and any(t["key"] == "fos" for t in targets):
        if any(t["key"] == "fos" and t["operator"] != "report" for t in targets) or mode == "calibration":
            errors.append("The factor of safety needs a safety phase, and this model has none: add a safety phase in "
                          "PLAXIS or choose another result.")
        else:
            targets[:] = [t for t in targets if t["key"] != "fos"]
            warnings.append("Factor of safety left out: the model has no safety phase.")

    runs = []
    base = s["baseline"] or mode in ("design_change", "reporting")
    if base:
        runs.append({"label": "Existing design", "inputs": {}})
    n_var = n - (1 if base else 0)  # "runs" is the total number of runs, Run 0 included
    pz = s.get("puzzle") or {}
    puzzle = bool(pz.get("on")) and mode != "reporting" and not screening  # Rev B W1.1: the Puzzle in Design modification too
    factorial = mode == "combinations" and bool(s.get("factorial"))
    if not puzzle and not screening and not factorial and mode not in ("design_change", "reporting") and params and n_var < (len(params) if mode == "sensitivity" else 1):
        errors.append(f"{n} run(s) in total is too few: give at least {(len(params) if mode == 'sensitivity' else 1) + (1 if base else 0)}.")
    if screening:
        pct = _f(screen.get("pct")) or 20.0
        shift = _f(screen.get("shift")) or 1.0
        for p, var in params:
            cur = _f(var.get("current"))
            if var["item"].startswith("grp:") or not cur:  # geometry (a shift) or a zero value: a step in its unit
                val, how = (cur or 0.0) + shift, f"+{fmt(shift)}"
            else:
                val, how = _r(cur * (1 + pct / 100)), f"{pct:+g} %"
            runs.append({"label": f"{var['label']} {how}", "inputs": {var["name"]: _encode(var, val)}})
    elif mode == "sensitivity":
        lists = [len(value_list(p) or []) if var["kind"] == "range" and value_list(p) is not None and s["distribution"] != "custom"
                 else None for p, var in params]
        free = [i for i, x in enumerate(lists) if x is None]  # the parameters with a range share the other runs
        sh = dict(zip(free, shares(max(0, n_var - sum(x or 0 for x in lists)), len(free))))
        for i, ((p, var), k) in enumerate(zip(params, [lists[i] if lists[i] is not None else sh[i] for i in range(len(params))])):
            if k < 1:
                continue
            for val in param_values(p, var, k, s["distribution"]):
                if val is not None:
                    runs.append({"label": f"{var['label']} = {value_text(var, val)}",
                                 "inputs": {var["name"]: _encode(var, val)}})
    elif mode == "design_change" and puzzle:
        pass  # the modifications are the packages of Puzzle boxes (Match the puzzle, below)
    elif mode == "design_change":
        # Rev B W1.3 (Walid, 23 Nov 2026: "keep the option for Many Modification Runs"): each item has one or more new
        # values (one Puzzle box each); Modification k takes the k-th new value of every item that has one. With
        # Match the puzzle, each modification is the package of boxes dropped into it instead (below).
        cols = []
        for p, var in params:
            vals = [v for v in (_dc_value(var, x) for x in new_values(p)) if v is not None]
            if not vals:
                errors.append(f"Give the new value of {var['label']}.")
                continue
            cols.append((var, vals))
        n_mod = mod_count(s, max((len(v) for _, v in cols), default=1))
        for k in range(n_mod):
            ins = {var["name"]: _encode(var, vals[k]) for var, vals in cols if k < len(vals)}
            if ins:
                runs.append({"label": f"Modification {k + 1}", "inputs": ins})
            elif cols:
                errors.append(f"Modification {k + 1} has no change: give each item a value for it, or lower the number of "
                              "modifications.")
    elif mode == "reporting":
        pass  # the existing design only (Run 0)
    elif mode == "combinations" and s.get("factorial") and params:
        # QA V26.1.8: "All combinations": every value of each variable with every value of the others (a scenario
        # matrix), not only the values of the same run number together. The values are the list / custom values of
        # each variable, or its range split evenly in "levels" values.
        import itertools
        lv = max(2, int(_f(s.get("levels")) or 2))
        cols = []
        for p, var in params:
            vals = value_list(p) if var["kind"] != "values" else None
            if not vals and (p.get("custom") or []):
                vals = [x for x in param_values(p, var, len(p["custom"]), "custom") if x is not None]
            if not vals:
                vals = _even(p, var, lv)
            cols.append((var, list(dict.fromkeys(vals))))
        for k, combo in enumerate(itertools.product(*[c[1] for c in cols])):
            runs.append({"label": f"Scenario {k + 1}", "inputs": {var["name"]: _encode(var, v) for (var, _), v in zip(cols, combo)}})
    else:  # optimization (together, n runs) and scenarios (n scenarios)
        cols = [(var, param_values(p, var, max(0, n_var), s["distribution"])) for p, var in params]
        # V26.1.9: a variable with fewer values than runs (e.g. "not added / added" in 4 runs) spreads its values over
        # the runs (it gave fewer values than runs, and the runs after them failed with an IndexError)
        cols = [(var, vals if len(vals) >= n_var or not vals else
                 [vals[round(k * (len(vals) - 1) / max(1, n_var - 1))] for k in range(n_var)]) for var, vals in cols]
        for k in range(max(0, n_var)):
            ins = {var["name"]: _encode(var, vals[k]) for var, vals in cols if vals[k] is not None}
            if ins:
                runs.append({"label": ("Scenario " if mode == "combinations" else "Run ") + str(k + 1), "inputs": ins})
    vmap = {v["name"]: v for v in variables}
    if puzzle:  # "Match the puzzle": each run is the boxes dropped into it ({item, value} of one or more variables)
        runs = runs[:1] if base else []
        items = {var["item"]: var for _, var in params}
        for cell in pz.get("runs") or []:
            ins = {}
            for t in cell or []:
                var = items.get((t or {}).get("item"))
                if not var:
                    continue
                v = t.get("v")
                if var["kind"] == "values":
                    v = next((x for x in var["values"] if x.split(":")[0].strip().lower() == str(v).split(":")[0].strip().lower()), None)
                else:
                    v = _f(v)
                if v is not None:
                    ins[var["name"]] = _encode(var, v)
            if ins:
                runs.append({"label": f"Modification {len(runs) + (0 if base else 1)}" if mode == "design_change"
                             else run_values(ins, vmap), "inputs": ins})
        if len(runs) == (1 if base else 0) and params:
            errors.append("Drag the puzzle boxes into the " + ("modifications" if mode == "design_change" else "runs")
                          + " (Match the puzzle), or untick Match the puzzle.")
    # a run customized in "Visualize proposed runs": {run number: {parameter item: value}}
    by_item = {var["item"]: var for _, var in params}
    over = s.get("over") or {}
    for i, r in enumerate(runs):
        no = i if base else i + 1
        for item, val in (over.get(str(no)) or {}).items():
            var = by_item.get(item)
            if not var or (mode == "sensitivity" and var["name"] not in r["inputs"]) or (not r["inputs"] and base and i == 0):
                continue
            val = _f(val) if var["kind"] != "values" else val
            if val is not None:
                r["inputs"][var["name"]] = _encode(var, val)
                r["custom"] = True
    for r in runs:
        if r.get("custom") and mode == "sensitivity":
            r["label"] = run_values(r["inputs"], vmap)
    confirmed = {str(x) for x in s.get("confirmed") or []}
    for i, r in enumerate(runs):
        r["key"] = i if base else i + 1  # the run's number before deletions: Confirm / Customize refer to it
    if mode == "design_change":  # Rev B W1.2a: each modification has a name of its own, carried to results and report
        names = s.get("names") or {}
        for r in runs:
            if r["key"] and str(names.get(str(r["key"])) or "").strip():
                r["name"] = str(names[str(r["key"])]).strip()[:60]
                r["label"] = f"{r['label']} · {r['name']}"  # the name is kept with the run (its label)
    order = {str(k): i for i, k in enumerate(s.get("order") or [])}  # runs moved earlier / later by hand
    if order:
        runs.sort(key=lambda r: (0 if base and r["key"] == 0 else 1, order.get(str(r["key"]), len(order) + r["key"])))
    skip = set(s.get("skip") or [])  # runs deleted in "Visualize proposed runs" (by their values)
    deleted = [r for r in runs if r["inputs"] and run_sig(r["inputs"]) in skip]
    runs = [r for r in runs if r not in deleted]
    rows = [{"run": i if base else i + 1, "key": r["key"], "sig": run_sig(r["inputs"]),
             "values": run_values(r["inputs"], vmap) or "Existing design",
             "inputs": r["inputs"], "label": r["label"], "name": r.get("name") or "", "custom": bool(r.get("custom")),
             "confirmed": str(r["key"]) in confirmed, "moves": geosel.displace(m, variables, r["inputs"])}
            for i, r in enumerate(runs)]
    # the geometry of each run checked before PLAXIS: overlapping lines, no length left, items changed indirectly
    pts = {q["name"]: q for q in m.get("points") or []}
    xyz = lambda q: (float(q["x"]), float(q["y"]), float(q.get("z", 0.0)))
    own = {"grp:" + e["id"]: [(xyz(pts[a]), xyz(pts[b])) for mem in e.get("members") or [] if len(mem) == 2
                               for a, b in [mem] if a in pts and b in pts]
           for e in s.get("geo") or [] if e.get("id") and (e.get("mode") or "x") == "s"}
    for row in rows:
        mine = [ab for v in variables if v["name"] in row["inputs"] for ab in own.get(v.get("item"), [])]
        row["checks"] = geocheck.check(m, row["moves"], mine)
        errors += [f"Run {row['run']}: {c['text']}" for c in row["checks"] if c["level"] == "error"]
    return {"study": s, "variables": variables, "targets": targets, "runs": runs, "rows": rows,
            "errors": list(dict.fromkeys(errors)), "first": 0 if base else 1, "deleted": len(deleted),
            "warnings": warnings}


LONGTERM = "@longterm"  # the long-term consolidation phase OPTIMUS adds after the last stage (V26.1.8)


def schedule(m: dict, tm: dict) -> dict:
    """The date of each construction stage, in days from the start of works: as typed (time.days), else 30 days a
    stage. Safety phases have no date of their own."""
    given = (tm or {}).get("days") or {}
    out, d = {}, 0.0
    for ph in (m.get("phases") or [])[1:]:
        if plaxis_model.is_safety_phase(ph):
            continue
        g = _f(given.get(ph["name"]))
        d = g if g is not None else d + 30.0
        out[ph["name"]] = (d, ph.get("id") or ph["name"])
    return out


def time_series(targets: list[dict], runs: list[dict]) -> list[dict]:
    """Every result read over time: per result, the (day, value) of each run, and the field readings if any."""
    out = {}
    for t in targets:
        if not t.get("time"):
            continue
        e = out.setdefault(t["key"], {"key": t["key"], "label": t.get("base_label") or t["label"], "unit": t["unit"],
                                      "runs": {}, "measured": [], "stages": {}})
        e["stages"][t["day"]] = t.get("phase_label") or ""
        if _isnum(t.get("measured")):
            e["measured"].append((t.get("read_day", t["day"]), t["measured"]))
        for r in runs:
            v = (r.get("outputs") or {}).get(t["name"]) if r.get("status") == "succeeded" else None
            if _isnum(v):
                e["runs"].setdefault(r.get("no"), []).append((t["day"], float(v)))
    for e in out.values():
        e["stages"] = sorted(e["stages"].items())
        e["runs"] = [{"run": no, "points": sorted(p)} for no, p in sorted(e["runs"].items(), key=lambda x: (x[0] is None, x[0]))]
    return list(out.values())


def needs_gravity_initial(plan: dict, runs: list | None = None) -> bool:
    """A study that can put a material in Soft Soil Creep (SSC μ*, or a soil-model variable that offers SSC):
    its initial phase is calculated by gravity loading in every run, Run 0 included (QA diagnosis 23 Nov 2026)."""
    for v in plan.get("variables") or []:
        ref = str(v.get("plaxis_ref") or "").lower()
        if ref.endswith(".sscmu"):
            return True
        if ref.endswith(".soilmodel"):
            vals = [(r.get("inputs") or {}).get(v["name"]) for r in runs or []]
            codes = [x[1] if isinstance(x, (list, tuple)) else x for x in vals if x is not None]
            if any(abs(float(c) - 6) < 1e-9 for c in codes if isinstance(c, (int, float))):
                return True  # a run puts this material in Soft Soil Creep
    return False


def var_kind_range(c: dict) -> bool:
    return c.get("kind") not in ("mesh", "remove") and not c.get("values")


def run_sig(inputs: dict) -> str:
    import json
    return json.dumps(inputs or {}, sort_keys=True, default=str)


STATS = {"max": "max", "min": "min", "avg": "average"}


def shares(total: int, k: int) -> list[int]:
    """Total runs shared between k parameters (sensitivity): 7 runs, 3 parameters -> 3, 2, 2."""
    if k <= 0:
        return []
    return [total // k + (1 if i < total % k else 0) for i in range(k)]


def run_values(inputs: dict, vmap: dict) -> str:
    return "; ".join(f"{vmap[k]['label']} = {value_text(vmap[k], tuple(v) if isinstance(v, list) else v)}"
                     for k, v in (inputs or {}).items() if k in vmap)


def more_runs(plan: dict, previous: list[dict], n: int) -> list[dict]:
    """Optimization: n new runs in the gaps between the runs already made (those are not run again)."""
    variables = plan["variables"]
    if not variables:
        return []

    def frac(r):
        v, val = variables[0], (r.get("inputs") or {}).get(variables[0]["name"])
        if val is None:
            return None
        if v["kind"] == "values":
            lab = val[0] if isinstance(val, (list, tuple)) else str(val)
            idx = next((i for i, x in enumerate(v["values"]) if x.split(":")[0].strip() == str(lab).strip()), 0)
            return idx / max(1, len(v["values"]) - 1)
        lo, hi = v["min"], v["max"]
        return 0.5 if hi == lo else (numeric(val) - lo) / (hi - lo)

    pos = sorted({round(f, 9) for f in (frac(r) for r in previous) if f is not None} | {0.0, 1.0})
    new = []
    # QA V26.1.8: refine where the answer is: at the interpolated limit (optimization) or the calibrated value
    # (calibration) and inside the interval around it, instead of only in the largest gaps of the range.
    v0 = variables[0]
    mode = (plan.get("study") or {}).get("mode")
    ok = [r for r in previous if r.get("status") == "succeeded"]
    focus, lo_b, hi_b = None, 0.0, 1.0
    try:
        if v0["kind"] != "values" and v0["max"] != v0["min"] and ok:
            span = v0["max"] - v0["min"]
            if mode == "optimize":
                g = governing_limit(limits(plan, ok))
                if g:
                    focus = (g["x"] - v0["min"]) / span
                    xs = sorted((x_of(plan, r) - v0["min"]) / span for r in ok if r.get("no") in g["between"] and _isnum(x_of(plan, r)))
                    if len(xs) == 2:
                        lo_b, hi_b = xs
            elif mode == "calibration":
                fits = [(misfit(plan["targets"], r["outputs"]), r) for r in ok]
                e = calibrated_value(plan, [(f, r) for f, r in fits if f is not None])
                if e:
                    focus = (e["x"] - v0["min"]) / span
                    lo_b, hi_b = max(0.0, focus - 0.25), min(1.0, focus + 0.25)
    except (TypeError, ZeroDivisionError, KeyError):
        focus = None
    if focus is not None and 0.0 <= focus <= 1.0 and all(abs(focus - p) > 1e-6 for p in pos):
        pos = sorted(pos + [focus])
        new.append(focus)
    for _ in range(n - len(new)):
        inside = [(b - a, a, b) for a, b in zip(pos, pos[1:]) if focus is not None and a >= lo_b - 1e-9 and b <= hi_b + 1e-9]
        gaps = sorted(inside or [(b - a, a, b) for a, b in zip(pos, pos[1:])], reverse=True)
        w, a, b = gaps[0]
        t = (a + b) / 2
        pos = sorted(pos + [t])
        new.append(t)
    new.sort()
    runs = []
    for t in new:
        ins = {}
        for v in variables:
            if v["kind"] == "values":
                ins[v["name"]] = parse_value(v["values"][round(t * (len(v["values"]) - 1))])
            else:
                ins[v["name"]] = _r(v["min"] + (v["max"] - v["min"]) * t)
        runs.append({"label": "Added run", "inputs": ins})
    return runs


# --- the results ----------------------------------------------------------------------------------------------
def achievement(targets: list[dict], outputs: dict) -> float | None:
    """How close one run comes to every target, 0-100 % (100 % = every target met)."""
    parts = []
    for t in targets:
        if t["operator"] == "report" or t.get("value") is None:
            continue
        v, lim = (outputs or {}).get(t["name"]), t["value"]
        if not isinstance(v, (int, float)):
            parts.append(0.0)
            continue
        if t["operator"] in (">", ">="):
            ok = v >= lim
            ratio = 1.0 if ok else (v / lim if lim > 0 and v > 0 else 0.0)
        else:
            ok = v <= lim
            ratio = 1.0 if ok else (lim / v if v > 0 and lim > 0 else 0.0)
        parts.append(max(0.0, min(1.0, ratio)))
    return round(100 * sum(parts) / len(parts), 1) if parts else None


# --- QA V26.1.8: the analysis of each module ---------------------------------------------------------------------
LOWER_IS_BETTER = {"utot", "ux", "uv", "settle", "wall_ux", "plate_m", "plate_q", "plate_n", "anchor", "pile_n", "pexcess"}


def _meets(t: dict, v) -> bool | None:
    if t["operator"] == "report" or t.get("value") is None or not _isnum(v):
        return None
    return {">=": v >= t["value"], ">": v > t["value"], "<=": v <= t["value"], "<": v < t["value"]}[t["operator"]]


def margin(t: dict, v) -> float | None:
    """How far a result is inside (+) or outside (−) its criterion, in % of the limit."""
    if t["operator"] == "report" or not _isnum(t.get("value")) or not _isnum(v) or not t["value"]:
        return None
    d = (v - t["value"]) if t["operator"] in (">", ">=") else (t["value"] - v)
    return 100 * d / abs(t["value"])


def x_of(plan: dict, r: dict):
    """The value of the first variable in a run (its position in the list for a list of levels)."""
    if not plan["variables"]:
        return None
    v = plan["variables"][0]
    val = (r.get("inputs") or {}).get(v["name"])
    if val is None:
        if str(v.get("item") or "").startswith(("sscmu:", "smodel:")):
            return None  # Run 0 keeps the material's own model: it is not a point of the SSC μ* curve
        cur = v.get("current")
        return _f(cur) if v["kind"] != "values" else None
    if v["kind"] == "values":
        lab = val[0] if isinstance(val, (list, tuple)) else str(val)
        return float(next((i for i, x in enumerate(v["values"]) if x.split(":")[0].strip() == str(lab).strip()), 0))
    return numeric(val) if not isinstance(val, (list, tuple)) else _f(val[1])


def _one_line(plan: dict) -> bool:
    """The runs lie on one line of the inputs (one variable, or all variables moved together): a limit or a best fit
    can be interpolated along the first variable."""
    return len(plan["variables"]) >= 1


def limits(plan: dict, ok: list[dict]) -> list[dict]:
    """Optimization: for each checked target, the value of the first variable where the result reaches the limit,
    interpolated between the two runs on either side of it (one passes, the other does not)."""
    if not _one_line(plan):
        return []
    v = plan["variables"][0]
    one = len(plan["variables"]) == 1  # Run 0 lies on the line of the runs only when one variable changes
    pts = sorted(((x_of(plan, r), r) for r in ok if (one or r.get("inputs")) and _isnum(x_of(plan, r))), key=lambda p: p[0])
    out = []
    for t in plan["targets"]:
        if t["operator"] == "report" or not _isnum(t.get("value")):
            continue
        for (xa, ra), (xb, rb) in zip(pts, pts[1:]):
            ya, yb = (ra["outputs"] or {}).get(t["name"]), (rb["outputs"] or {}).get(t["name"])
            ma, mb = _meets(t, ya), _meets(t, yb)
            if ma is None or mb is None or ma == mb or not _isnum(ya) or not _isnum(yb) or ya == yb:
                continue
            x = xa + (t["value"] - ya) * (xb - xa) / (yb - ya)
            out.append({"target": t["label"], "name": t["name"], "unit": t["unit"], "limit": t["value"],
                        "operator": t["operator"], "x": _r(x), "var": v["label"], "var_unit": v.get("unit") or "",
                        "between": [ra.get("no"), rb.get("no")], "passing_side": "low" if ma else "high"})
    return out


def governing_limit(lims: list[dict]) -> dict | None:
    """The limit reached first: of the passing sides, the narrowest admissible interval of the first variable."""
    if not lims:
        return None
    low = [l for l in lims if l["passing_side"] == "low"]   # passes below x
    high = [l for l in lims if l["passing_side"] == "high"]  # passes above x
    lo = max((l["x"] for l in high), default=None)
    hi = min((l["x"] for l in low), default=None)
    g = min(low, key=lambda l: l["x"]) if low else max(high, key=lambda l: l["x"])
    return {**g, "admissible": [lo, hi]}


def parabola_min(pts: list[tuple]) -> tuple | None:
    """The minimum of a parabola through three (x, y) points, when it lies between the outer two."""
    if len(pts) != 3:
        return None
    (x1, y1), (x2, y2), (x3, y3) = pts
    den = (x1 - x2) * (x1 - x3) * (x2 - x3)
    if not den:
        return None
    a = (x3 * (y2 - y1) + x2 * (y1 - y3) + x1 * (y3 - y2)) / den
    b = (x3 ** 2 * (y1 - y2) + x2 ** 2 * (y3 - y1) + x1 ** 2 * (y2 - y3)) / den
    c = (x2 * x3 * (x2 - x3) * y1 + x3 * x1 * (x3 - x1) * y2 + x1 * x2 * (x1 - x2) * y3) / den
    if a <= 0:
        return None
    x = -b / (2 * a)
    if not min(x1, x3) <= x <= max(x1, x3):
        return None
    return x, max(0.0, a * x * x + b * x + c)


def calibrated_value(plan: dict, fits: list[tuple]) -> dict | None:
    """Calibration: the value of the first variable with the least misfit, from a parabola through the best run
    and its two neighbours (or the best run itself when it lies at the end of the range)."""
    pts = sorted(((x_of(plan, r), f, r.get("no")) for f, r in fits if _isnum(x_of(plan, r))), key=lambda p: p[0])
    if not pts or not plan["variables"]:
        return None
    v = plan["variables"][0]
    i = min(range(len(pts)), key=lambda k: pts[k][1])
    res = {"var": v["label"], "unit": v.get("unit") or "", "x": pts[i][0], "misfit": pts[i][1], "from_run": pts[i][2],
           "interpolated": False, "at_end": i in (0, len(pts) - 1) and len(pts) > 1}
    if 0 < i < len(pts) - 1:
        pm = parabola_min([(pts[k][0], pts[k][1]) for k in (i - 1, i, i + 1)])
        if pm:
            res.update(x=_r(pm[0]), misfit=round(pm[1], 2), interpolated=True, between=[pts[i - 1][2], pts[i + 1][2]])
    return res


def missing_results(targets: list[dict], runs: list[dict]) -> list[dict]:
    """The results that PLAXIS did not give, with the reason, for every run (they used to disappear silently)."""
    out = {}
    for r in runs:
        if r["status"] != "succeeded":
            continue
        why = {m.split(": ")[0]: m.split(": ", 1)[1] if ": " in m else "" for m in (r.get("outputs") or {}).get("_missing") or []}
        for t in targets:
            if not _isnum((r.get("outputs") or {}).get(t["name"])) and t["result"].lower() != "file":
                w = why.get(t["label"], "not read")
                if "could not convert string to float" in w:  # V26.1.8: PLAXIS returned text (no values at this stage)
                    w = "not active at this stage (PLAXIS returned no values)"
                e = out.setdefault(t["label"], {"label": t["label"], "runs": [], "reason": w})
                e["runs"].append(r.get("no"))
    return list(out.values())


def results(task: dict, runs: list[dict], previous: list[dict] | None = None) -> dict:
    """Summary, the table of runs (3 columns) and the data the figures are drawn from."""
    plan = task["plan"]
    s = plan.get("study") or {}
    mode = s.get("mode") or task["objective"]
    vmap = {v["name"]: v for v in plan["variables"]}
    targets = plan["targets"]
    first = plan.get("first", 0)
    allruns = (previous or []) + runs
    for r in allruns:
        r.setdefault("no", None)
    for i, r in enumerate(runs):
        r["no"] = plan.get("offset", 0) + first + i
    ok = [r for r in allruns if r["status"] == "succeeded"]
    main_t = [t for t in targets if not t.get("stage")]  # V26.1.8: the stage / time values have their own tables

    def res_text(r):
        if r["status"] != "succeeded":
            return r["status"] + (f": {r['error']}" if r.get("error") else "")
        parts = []
        for t in targets:
            if t.get("stage"):  # the stage values are in their own table (Advanced reporting)
                continue
            v = (r["outputs"] or {}).get(t["name"])
            if isinstance(v, (int, float)):
                mark = ""
                if t["operator"] != "report":
                    mark = " ✓" if (r.get("checks") or {}).get(t["name"]) else " ✗"
                parts.append(f"{_short(t)} {fmt(float(v))}{(' ' + t['unit']) if t['unit'] else ''}{mark}")
        return "; ".join(parts)

    rows = [{"run": r["no"], "values": run_values(r["inputs"], vmap) or "Existing design", "result": res_text(r),
             "status": r["status"], "new": r in runs} for r in allruns]
    out = {"mode": mode, "title": MODES.get(mode, {}).get("title", mode), "rows": rows,
           "done": sum(r["status"] in ("succeeded", "failed") for r in runs), "total": len(runs)}
    if mode == "optimize":
        scored = [(achievement(targets, r["outputs"]), r) for r in ok]
        scored = [(a, r) for a, r in scored if a is not None]
        best = None
        checked = [t for t in targets if t["operator"] != "report"]
        # V26.1.8: "passes" = every criterion met, not an achievement rounded to 100 % (0.007201 against 0.0072 is
        # 99.99 %, shown as 100 % and counted as passing although its ✗ said otherwise)
        passed = [r for a, r in scored if all(_meets(t, (r["outputs"] or {}).get(t["name"])) for t in checked)]
        gov = lambda r: min((m for m in (margin(t, (r["outputs"] or {}).get(t["name"])) for t in checked) if m is not None), default=0.0)
        if passed:
            # QA V26.1.8: the least over-designed run on EVERY target (smallest governing margin), not on the first only
            best = min(passed, key=gov)
        elif scored:
            best = max(scored, key=lambda x: (x[0], gov(x[1])))[1]
        pct = max((a for a, _ in scored), default=0.0)
        lims = limits(plan, ok)
        g = governing_limit(lims)
        out["optimization"] = {"achieved": bool(passed), "percent": pct, "best_run": best["no"] if best else None,
                               "best_values": (run_values(best["inputs"], vmap) or "existing design") if best else "",
                               "best_result": res_text(best) if best else "", "passing": len(passed),
                               "best_margin": round(gov(best), 1) if best else None, "limits": lims, "governing": g,
                               "margins": [{"label": t["label"], "margin": margin(t, (best["outputs"] or {}).get(t["name"]))}
                                           for t in checked] if best else []}
        for row, r in zip(rows, allruns):
            row["achievement"] = achievement(targets, r["outputs"]) if r["status"] == "succeeded" else None
            row["margin"] = round(gov(r), 1) if r["status"] == "succeeded" else None
    if mode == "design_change":
        modification_results(out, plan, s, main_t, ok, allruns, vmap)
    if mode == "calibration":
        fits = []
        for row, r in zip(rows, allruns):
            row["misfit"] = misfit(targets, r["outputs"]) if r["status"] == "succeeded" else None
            if row["misfit"] is not None:
                fits.append((row["misfit"], r))
        best = min(fits, key=lambda x: x[0]) if fits else None
        out["calibration"] = {"best_run": best[1]["no"] if best else None, "misfit": best[0] if best else None,
                              "best_values": (run_values(best[1]["inputs"], vmap) or "existing design") if best else "",
                              "best_result": res_text(best[1]) if best else "",
                              "measured": [{"label": t["label"], "unit": t["unit"], "value": t.get("measured")}
                                           for t in targets if _isnum(t.get("measured"))],
                              # QA V26.1.8: what each measurement says in the best run, and the calibrated value
                              "residuals": [{"label": t["label"], "unit": t["unit"], "measured": t.get("measured"),
                                             "calculated": (best[1]["outputs"] or {}).get(t["name"]),
                                             "diff": (((best[1]["outputs"] or {}).get(t["name"]) - t["measured"]) / abs(t["measured"]) * 100)
                                             if best and _isnum((best[1]["outputs"] or {}).get(t["name"])) and _isnum(t.get("measured")) and t["measured"] else None}
                                            for t in targets if _isnum(t.get("measured"))] if best else [],
                              "estimate": calibrated_value(plan, fits)}
    if mode == "combinations":  # QA V26.1.8: the governing scenario of every result (envelope)
        gv = []
        for t in main_t:
            pts = [(r.get("no"), float((r["outputs"] or {})[t["name"]])) for r in ok if r.get("inputs") and _isnum((r["outputs"] or {}).get(t["name"]))]
            if pts:
                hi, lo = max(pts, key=lambda p: p[1]), min(pts, key=lambda p: p[1])
                worst = hi if t.get("key") in LOWER_IS_BETTER else lo
                gv.append({"label": t["label"], "unit": t["unit"], "max": hi[1], "max_run": hi[0], "min": lo[1],
                           "min_run": lo[0], "governing_run": worst[0], "governing": worst[1],
                           "values": run_values(next(r["inputs"] for r in ok if r.get("no") == worst[0]), vmap)})
        out["governing"] = gv
    out["missing"] = missing_results(targets, allruns)
    if mode == "reporting":  # QA V26.1.8: every result at every stage, as a table (stage targets carry a phase)
        st = [t for t in targets if t.get("stage")]
        if st and ok:
            o0 = ok[0]["outputs"] or {}
            lab = lambda t: t.get("phase_label") or t.get("phase") or "last phase"  # QA V26.1.9 D2: no KeyError
            phases = list(dict.fromkeys(lab(t) for t in st))
            tab = {}
            for t in st:
                e = tab.setdefault(t["key"], {"label": t.get("base_label") or t["label"], "unit": t["unit"], "values": {}})
                e["values"][lab(t)] = o0.get(t["name"])
            out["stages"] = {"phases": phases, "rows": list(tab.values())}
    out["stats"] = statistics(main_t, ok)
    if any(t.get("time") for t in targets):  # V26.1.8: every chosen result over time (stage dates, long term)
        out["time"] = time_series(targets, allruns)
    if s.get("mode", mode) == "sensitivity" and not (s.get("screen") or {}).get("on"):
        out["summary"] = sens_summary(plan, allruns)
    if s.get("mode", mode) == "sensitivity" and (s.get("detector") or {}).get("on"):
        out["detector"] = detector(plan, out["stats"], allruns)
    if s.get("mode", mode) == "sensitivity" and (s.get("screen") or {}).get("on"):
        out["screen"] = screening_rank(plan, allruns)
    out["text"] = summary_text(out, plan, ok, allruns)
    return out


def change_size(inputs: dict, vmap: dict) -> float:
    """How far a modification is from the existing design: the sum of |Δx / x| of its numeric inputs (a change of a
    level, a model or a structure counts 1). Used to rank by "least change that meets every criterion"."""
    tot = 0.0
    for name, val in (inputs or {}).items():
        var = vmap.get(name) or {}
        v = val[0] if isinstance(val, (list, tuple)) and val and var.get("kind") != "values" else val
        cur = _f(var.get("current"))
        if var.get("kind") != "values" and _isnum(v) and cur:
            tot += abs(float(v) - cur) / abs(cur)
        elif var.get("kind") != "values" and _isnum(v) and cur == 0:
            tot += abs(float(v))
        else:
            tot += 1.0
    return tot


def _compare(main_t: list[dict], old: dict, new: dict) -> tuple[list[dict], int, int]:
    """Every result of one modification against the existing design: value, change %, effect and criterion."""
    rows, better, worse = [], 0, 0
    for t in main_t:
        nw = (new["outputs"] or {}).get(t["name"])
        if not isinstance(nw, (int, float)):
            continue
        o = (old["outputs"] or {}).get(t["name"]) if old else None
        c = {"label": t["label"], "unit": t["unit"], "key": t.get("key"), "name": t["name"], "old": o, "new": nw,
             "operator": t["operator"], "limit": t.get("value")}
        c["change"] = (nw - o) / abs(o) * 100 if isinstance(o, (int, float)) and o else None
        if isinstance(o, (int, float)) and abs(nw - o) > 1e-9 * max(1.0, abs(o)):
            low = t.get("key") in LOWER_IS_BETTER
            c["effect"] = "improves" if (nw < o) == low else "worsens"
            better += c["effect"] == "improves"
            worse += c["effect"] == "worsens"
        else:
            c["effect"] = "no change"
        c["meets_old"], c["meets_new"] = _meets(t, o), _meets(t, nw)
        c["margin"] = margin(t, nw)
        rows.append(c)
    return rows, better, worse


RANK_RULES = {"least": "least change that meets every criterion", "margin": "largest governing margin"}


def modification_results(out: dict, plan: dict, s: dict, main_t: list[dict], ok: list[dict], allruns: list[dict],
                         vmap: dict) -> None:
    """Rev B W1.5 (Walid, 23 Nov 2026: Many Modification Runs): the existing design (Run 0) against every modification,
    one column each, with its verdict, governing margin and rank; the chosen one (★, rank 1 by default) is the one
    handed to Design scenarios. out["comparison"] / out["verdict"] stay those of the chosen modification."""
    # QA V26.1.8: the existing design is the run without inputs (a failed Run 0 must not compare a design with itself)
    old = next((r for r in ok if not r.get("inputs")), None)
    mods = [r for r in allruns if r.get("inputs")]
    rule = s.get("rank") if s.get("rank") in RANK_RULES else "least"
    checked = [t for t in main_t if t["operator"] != "report" and t.get("value") is not None]
    out["rank_rule"] = {"key": rule, "text": RANK_RULES[rule]}
    out["existing"] = {"run": old["no"] if old else None,
                       "meets": all(_meets(t, (old["outputs"] or {}).get(t["name"])) for t in checked) if old and checked else None,
                       "values": {t["name"]: (old["outputs"] or {}).get(t["name"]) for t in main_t} if old else {},
                       "margin": min((x for x in (margin(t, (old["outputs"] or {}).get(t["name"])) for t in checked) if x is not None),
                                     default=None) if old and checked else None}
    cols = []
    for k, r in enumerate(mods, 1):
        name = r.get("name") or (str(r.get("label") or "").split(" · ", 1)[1] if " · " in str(r.get("label") or "") else "")
        col = {"run": r["no"], "key": r.get("key"), "no": k, "name": name,
               "title": f"Modification {k}" + (f" · {name}" if name else ""),
               "values": run_values(r["inputs"], vmap), "status": r["status"], "size": round(change_size(r["inputs"], vmap), 4)}
        if r["status"] == "succeeded" and old:
            rows, better, worse = _compare(main_t, old, r)
            crit = [c for c in rows if c["meets_new"] is not None]
            margins = [c["margin"] for c in rows if c["margin"] is not None and c["meets_new"] is not None]
            col.update({"results": rows, "improves": better, "worsens": worse, "criteria": len(crit),
                        "meets": all(c["meets_new"] for c in crit) if crit else None,
                        "failing": [c["label"] for c in crit if not c["meets_new"]],
                        "margin": round(min(margins), 1) if margins else None})
        cols.append(col)
    done = [c for c in cols if "results" in c]

    def key(c):
        passes = c["meets"] is not False
        if rule == "margin":
            return (0 if passes else 1, -(c["margin"] if c["margin"] is not None else -1e9), c["size"])
        if c["meets"] is None:  # no criterion: the most results improved, then the least change
            return (0, -(c["improves"] - c["worsens"]), c["size"])
        return (0 if passes else 1, c["size"] if passes else -(c["margin"] if c["margin"] is not None else -1e9), c["no"])
    for i, c in enumerate(sorted(done, key=key), 1):
        c["rank"] = i
    chosen = next((c for c in done if str(c["run"]) == str(s.get("chosen"))), None) or next((c for c in done if c.get("rank") == 1), None)
    for c in cols:
        c["chosen"] = c is chosen
    out["modifications"] = cols
    if not old or not done:
        out["verdict"] = {"incomplete": "the existing" if not old else "the modified"}
        return
    out["comparison"] = chosen["results"]
    out["verdict"] = {"improves": chosen["improves"], "worsens": chosen["worsens"], "criteria": chosen["criteria"],
                      "meets": chosen["meets"], "failing": chosen["failing"], "chosen": chosen["run"],
                      "chosen_title": chosen["title"], "passing": sum(1 for c in done if c["meets"] is True),
                      "calculated": len(done), "total": len(cols)}



def detector(plan: dict, stats: list[dict], runs: list[dict]) -> list[dict]:
    """Sensitivity Detector (results): the 10 PLAXIS results that change the most over the runs, and for each the
    input that changes it the most."""
    drivers = {}
    for sr in sensitivity_series(plan, runs):
        ys = [p[1] for p in sr["points"]] + ([sr["base"][1]] if sr["base"] else [])
        ref = sr["base"][1] if sr["base"] else ys[0]
        ch = (max(ys) - min(ys)) / abs(ref) * 100 if ref else 0.0
        lab = sr["target"]["label"]
        if ch > drivers.get(lab, ("", -1.0))[1]:
            drivers[lab] = (sr["var"]["label"], ch)
    keys = {t["label"]: t.get("key") for t in plan["targets"]}
    ctl = {t.get("key") for t in plan["targets"] if t.get("control")}
    rows = [{**st, "driver": drivers.get(st["label"], ("", 0))[0], "key": keys.get(st["label"]),
             "control": keys.get(st["label"]) in ctl} for st in stats if st.get("spread") is not None]
    rows.sort(key=lambda r: -abs(r["spread"]))
    return rows[:10] + [r for r in rows[10:] if r["control"]]  # V26.1.9: the control parameters are always shown


def screening_rank(plan: dict, runs: list[dict]) -> list[dict]:
    """Sensitivity Detector (inputs): how much each item, changed alone, moves the control parameters (%), most
    effective first (ranked on the first control parameter)."""
    base = next((r for r in runs if r["status"] == "succeeded" and not r.get("inputs")), None)
    ts = plan["targets"]
    key_t = next((t for t in ts if t.get("control")), ts[0] if ts else None)
    out = []
    for v in plan["variables"]:
        r = next((r for r in runs if r["status"] == "succeeded" and list(r.get("inputs") or {}) == [v["name"]]), None)
        if not r or not base:
            continue
        ch = []
        for t in ts:
            y0, y = (base["outputs"] or {}).get(t["name"]), (r["outputs"] or {}).get(t["name"])
            if _isnum(y0) and _isnum(y):
                ch.append({"label": t["label"], "name": t["name"], "from": float(y0), "to": float(y),
                           "change": (y - y0) / abs(y0) * 100 if y0 else 0.0})
        main = next((c for c in ch if key_t and c["name"] == key_t["name"]), ch[0] if ch else None)
        out.append({"item": v.get("item"), "label": v["label"], "run": r["no"], "changes": ch,
                    "main": main["change"] if main else 0.0, "main_label": main["label"] if main else ""})
    out.sort(key=lambda x: -abs(x["main"]))
    return out


def misfit(targets: list[dict], outputs: dict | None) -> float | None:
    """Root mean square of the relative differences (%) between the PLAXIS results and the measured values."""
    parts = []
    for t in targets:
        meas, v = t.get("measured"), (outputs or {}).get(t["name"])
        if _isnum(meas) and _isnum(v):
            parts.append(((float(v) - meas) / (abs(meas) or 1.0)) ** 2)
    return round(100 * math.sqrt(sum(parts) / len(parts)), 2) if parts else None


def statistics(targets: list[dict], ok: list[dict]) -> list[dict]:
    """Minimum, maximum and mean of every result over the calculated runs, and the spread against Run 0."""
    out = []
    for t in targets:
        pts = [(r.get("no"), float((r["outputs"] or {})[t["name"]])) for r in ok if _isnum((r["outputs"] or {}).get(t["name"]))]
        if not pts:
            continue
        ys = [y for _, y in pts]
        lo, hi = min(pts, key=lambda p: p[1]), max(pts, key=lambda p: p[1])
        ref = ys[0]
        out.append({"label": t["label"], "unit": t["unit"], "min": lo[1], "min_run": lo[0], "max": hi[1],
                    "max_run": hi[0], "mean": sum(ys) / len(ys), "n": len(ys),
                    "spread": (hi[1] - lo[1]) / abs(ref) * 100 if ref else None})
    return out


def _short(t: dict) -> str:
    if t.get("safety_after"):  # V26.1.9: the factor of safety after a chosen stage
        return t["label"].replace("Factor of safety", "FoS")
    return {"fos": "FoS", "utot": "|u|", "ux": "ux", "uv": "u vert.", "settle": "settlement", "wall_ux": "wall ux",
            "plate_m": "M", "plate_q": "Q", "plate_n": "N", "anchor": "anchor F", "pile_n": "pile N",
            "pexcess": "p excess"}.get(t.get("key"), t["label"])


def _miss_text(out: dict) -> str:
    m = out.get("missing") or []
    if not m:
        return ""
    return (f" {len(m)} result(s) could not be read from PLAXIS: "
            + "; ".join(f"{x['label']} ({x['reason'][:80]})" for x in m[:4]) + ("…" if len(m) > 4 else "") + ".")


def summary_text(out: dict, plan: dict, ok: list, allruns: list) -> str:
    return _summary_text(out, plan, ok, allruns) + _miss_text(out)


def _summary_text(out: dict, plan: dict, ok: list, allruns: list) -> str:
    mode = out["mode"]
    head = f"{len(ok)} of {len(allruns)} runs calculated."
    if mode == "optimize" and "optimization" in out:
        o = out["optimization"]
        g = o.get("governing")
        lim = ""
        if g:
            side = "at most" if g["passing_side"] == "low" else "at least"
            lim = (f" Interpolated limit: {g['var']} {side} {fmt(g['x'])}{(' ' + g['var_unit']) if g['var_unit'] else ''} "
                   f"(governed by {g['target']} {g['operator']} {fmt(g['limit'])}, between Run {g['between'][0]} and Run "
                   f"{g['between'][1]}); add new runs to confirm it.")
        if o["achieved"]:
            return (f"Optimization achieved. {o['passing']} run(s) meet every target. Recommended design: Run "
                    f"{o['best_run']} ({o['best_values']}): {o['best_result']}"
                    + (f", governing margin {o['best_margin']:.1f} %" if o.get("best_margin") is not None else "") + "." + lim)
        return (f"Optimization not achieved: the best run reaches {o['percent']:.0f} % of the targets"
                + (f" (Run {o['best_run']}: {o['best_values']})" if o["best_run"] is not None else "")
                + ". Widen the ranges or add new runs." + lim)
    if mode == "design_change" and out.get("verdict", {}).get("incomplete"):
        return head + f" No comparison: {out['verdict']['incomplete']} design was not calculated."
    if mode == "design_change" and out.get("comparison") and len(out.get("modifications") or []) > 1:
        return many_text(out)
    if mode == "design_change" and out.get("comparison"):
        parts = [f"{c['label']} {fmt(c['old'])} → {fmt(c['new'])}{(' ' + c['unit']) if c['unit'] else ''}"
                 + (f" ({c['change']:+.1f} %, {c['effect']})" if c.get("change") is not None else "") for c in out["comparison"]]
        v = out.get("verdict") or {}
        txt = "Existing → modified design: " + "; ".join(parts) + "."
        txt += f" The modification improves {v.get('improves', 0)} and worsens {v.get('worsens', 0)} result(s)."
        if v.get("meets") is True:
            txt += f" The modified design meets all {v['criteria']} acceptance criteria."
        elif v.get("meets") is False:
            txt += " The modified design does NOT meet: " + ", ".join(v["failing"]) + "."
        return txt
    if mode == "combinations" and out.get("governing"):
        return head + " Governing scenarios: " + "; ".join(
            f"{g['label']} {fmt(g['governing'])}{(' ' + g['unit']) if g['unit'] else ''} in Run {g['governing_run']} ({g['values']})"
            for g in out["governing"]) + "."
    if mode == "sensitivity" and out.get("screen"):
        top = out["screen"][:3]
        return head + " Sensitivity Detector: " + "; ".join(f"{x['label']} changes {x['main_label']} by {x['main']:+.1f} %"
                                                            for x in top) + (" (most effective first)." if top else ".")
    if mode == "sensitivity":
        ctl = {**plan, "targets": [t for t in plan["targets"] if not t.get("detector")]}
        lines = sensitivity_lines(ctl, allruns)
        txt = head + (" " + "; ".join(lines) + "." if lines else "")
        if out.get("detector"):
            txt += " Most affected results: " + ", ".join(f"{d['label']} ({d['spread']:.0f} %)" for d in out["detector"][:3]) + "."
        return txt
    if mode == "calibration" and (out.get("calibration") or {}).get("best_run") is not None:
        c = out["calibration"]
        txt = (f"{head} Best fit with the measurements: Run {c['best_run']} ({c['best_values']}), misfit "
               f"{c['misfit']:.1f} %" + (f": {c['best_result']}." if c['best_result'] else "."))
        e = c.get("estimate")
        if e and e.get("interpolated"):
            txt += (f" Calibrated value (misfit curve): {e['var']} = {fmt(e['x'])}{(' ' + e['unit']) if e['unit'] else ''}, "
                    f"estimated misfit {e['misfit']:.1f} %; add a run at this value to confirm it.")
        elif e and e.get("at_end"):
            txt += f" The best fit is at the end of the range of {e['var']}: widen the range, the true value may lie outside."
        return txt
    if mode == "reporting" and out.get("stats"):
        return head + " " + "; ".join(f"{st['label']} {fmt(st['max'])}{(' ' + st['unit']) if st['unit'] else ''}"
                                      for st in out["stats"]) + "." + (
            f" Every result is also given at each of the {len(out['stages']['phases'])} stages." if out.get("stages") else "")
    return head


def many_text(out: dict) -> str:
    """Rev B W1.5: the verdict line over every modification."""
    v, cols, ex = out["verdict"], out["modifications"], out.get("existing") or {}
    done = [c for c in cols if "results" in c]
    txt = f"The existing design is compared with {len(cols)} modifications ({len(done)} calculated)."
    if any(c["meets"] is not None for c in done):
        txt += f" {v['passing']} of {len(done)} meet every acceptance criterion"
        txt += "; the existing design does not." if ex.get("meets") is False else "; so does the existing design." if ex.get("meets") else "."
    best = next((c for c in done if c.get("rank") == 1), None)
    if best:
        ch = "; ".join(f"{x['label']} {x['change']:+.1f} %" for x in best["results"] if x.get("change") is not None)
        txt += (f" Rank 1 ({out['rank_rule']['text']}): {best['title']} ({best['values']})"
                + (f": {ch}" if ch else "") + (f", governing margin {best['margin']:+.1f} %" if best.get("margin") is not None else "") + ".")
    ch = next((c for c in done if c["chosen"]), None)
    if ch and best and ch is not best:
        txt += f" Chosen by the engineer: {ch['title']} (rank {ch['rank']})."
    if v.get("meets") is False:
        txt += " The chosen modification does NOT meet: " + ", ".join(v["failing"]) + "."
    return txt


def sens_summary(plan: dict, runs: list[dict]) -> list[dict]:
    """Sensitivity summary (tornado): for each control parameter, how far each variable parameter moves it, from its
    lowest to its highest effect against the existing design (%), largest first."""
    out = {}
    for sr in sensitivity_series(plan, runs):
        t, v = sr["target"], sr["var"]
        if t.get("detector"):
            continue
        pts = sr["points"]
        ref = sr["base"][1] if sr["base"] else None
        if not ref:
            ys = [p[1] for p in pts]
            ref = sum(ys) / len(ys) if ys else None
        if not ref:
            continue
        ch = [((p[1] - ref) / abs(ref) * 100, p[0], p[1], p[2]) for p in pts]
        lo, hi = min(ch, key=lambda c: c[0]), max(ch, key=lambda c: c[0])
        e = out.setdefault(t["name"], {"name": t["name"], "key": t.get("key"), "label": t["label"], "unit": t["unit"],
                                       "base": sr["base"][1] if sr["base"] else None, "params": []})
        e["params"].append({"label": v["label"], "unit": v.get("unit") or "",
                            "lo": min(0.0, lo[0]), "hi": max(0.0, hi[0]),
                            "lo_x": show_x(lo[1]), "hi_x": show_x(hi[1]), "lo_run": lo[3], "hi_run": hi[3],
                            "span": max(0.0, hi[0]) - min(0.0, lo[0])})
    for e in out.values():
        e["params"].sort(key=lambda q: -q["span"])
    return list(out.values())


def show_x(x) -> str:
    return fmt(float(x)) if _isnum(x) else str(x)


def sensitivity_series(plan: dict, runs: list[dict]) -> list[dict]:
    """For each variable parameter and each control parameter: x (input) and y (result) of the runs where only
    that parameter changed, plus the existing design."""
    out = []
    base = next((r for r in runs if r["status"] == "succeeded" and not r.get("inputs")), None)
    for v in plan["variables"]:
        mine = [r for r in runs if r["status"] == "succeeded" and list((r.get("inputs") or {})) == [v["name"]]]
        for t in [t for t in plan["targets"] if not t.get("stage")]:
            pts = []
            for r in mine:
                val = r["inputs"][v["name"]]
                y = (r["outputs"] or {}).get(t["name"])
                if isinstance(y, (int, float)):
                    x = val[0] if isinstance(val, (list, tuple)) and v["kind"] == "values" else numeric(val) if not isinstance(val, list) else val[1]
                    pts.append((x, float(y), r["no"]))
            b = None
            if base and isinstance((base["outputs"] or {}).get(t["name"]), (int, float)):
                cur = v.get("current")
                bx = str(cur).split(":")[0] if v["kind"] == "values" else _f(cur)
                b = (bx, float(base["outputs"][t["name"]]), base["no"])
            if pts:
                out.append({"var": v, "target": t, "points": pts, "base": b})
    return out


def sensitivity_lines(plan: dict, runs: list[dict]) -> list[str]:
    lines = []
    for sr in sensitivity_series(plan, runs):
        ys = [p[1] for p in sr["points"]]
        lo, hi = min(ys), max(ys)
        ref = sr["base"][1] if sr["base"] else ys[0]
        ch = (hi - lo) / abs(ref) * 100 if ref else 0
        lines.append(f"{sr['target']['label']} changes by {ch:.1f} % over the range of {sr['var']['label']}")
    return lines


def chart_specs(task: dict, out: dict, runs_all: list[dict]) -> list[dict]:
    """What each figure shows (drawn by app.exports.figure)."""
    plan, mode = task["plan"], out["mode"]
    s = plan.get("study") or {}
    specs = []
    for sm in out.get("summary") or []:
        if sm["params"]:
            specs.append({"kind": "tornado", "title": f"Sensitivity summary: effect of each parameter on {sm['label']} (% against the existing design)",
                          "params": sm["params"]})
    if len(out.get("summary") or []) > 1:
        specs.append({"kind": "spread", "title": "Sensitivity summary: spread of every control parameter by parameter (%)",
                      "items": out["summary"]})
    if out.get("detector"):
        specs.append({"kind": "rank", "title": "Sensitivity Detector: the most affected PLAXIS results (spread over the runs, %)",
                      "items": [(d["label"], d["spread"]) for d in out["detector"]]})
    if out.get("screen"):
        specs.append({"kind": "rank", "title": f"Sensitivity Detector: effect of each item on {out['screen'][0]['main_label']} (%)",
                      "items": [(x["label"], x["main"]) for x in out["screen"]]})
    if mode == "sensitivity" and not out.get("screen"):
        for sr in sensitivity_series(plan, runs_all):
            if sr["target"].get("detector"):
                continue
            v, t = sr["var"], sr["target"]
            specs.append({"kind": "sensitivity", "title": f"{t['label']} against {v['label']}",
                          "var": v, "target": t, "points": sr["points"], "base": sr["base"],
                          "swap": bool(s.get("swap"))})
    elif mode == "optimize":
        rows = [r for r in out["rows"] if r.get("achievement") is not None]
        specs.append({"kind": "achievement", "title": "Achievement of the target criteria in every run",
                      "rows": rows, "best": out["optimization"]["best_run"]})
        for t in [t for t in plan["targets"] if not t.get("stage")]:
            pts = [(r["no"], (r["outputs"] or {}).get(t["name"])) for r in runs_all if r["status"] == "succeeded"]
            pts = [(a, float(b)) for a, b in pts if isinstance(b, (int, float))]
            if pts:
                specs.append({"kind": "runs", "title": f"{t['label']} in every run", "target": t, "points": pts,
                              "best": out["optimization"]["best_run"]})
    elif mode == "calibration":
        c = out.get("calibration") or {}
        rows = [r for r in out["rows"] if r.get("misfit") is not None]
        if rows:
            specs.append({"kind": "misfit", "title": "Misfit with the measured values in every run (%)",
                          "rows": rows, "best": c.get("best_run")})
        for t in [t for t in plan["targets"] if not t.get("stage")]:
            pts = [(r["no"], (r["outputs"] or {}).get(t["name"])) for r in runs_all if r["status"] == "succeeded"]
            pts = [(a, float(b)) for a, b in pts if isinstance(b, (int, float))]
            if pts:
                specs.append({"kind": "runs", "title": f"{t['label']} in every run (measured {fmt(t['measured'])})"
                              if _isnum(t.get("measured")) else f"{t['label']} in every run", "target": t,
                              "points": pts, "best": c.get("best_run"), "measured": t.get("measured")})
    elif mode == "design_change":
        done = [c for c in out.get("modifications") or [] if "results" in c]
        if len(done) > 1:  # Rev B W1.5: one grouped bar chart per result, existing grey, modifications blue, criterion dashed
            ex = (out.get("existing") or {}).get("values") or {}
            for t in [t for t in plan["targets"] if not t.get("stage")]:
                bars = [(c["title"].replace("Modification ", "Mod "), next((x["new"] for x in c["results"] if x["name"] == t["name"]), None),
                         c["chosen"]) for c in done]
                bars = [b for b in bars if _isnum(b[1])]
                if bars:
                    specs.append({"kind": "compare_many", "title": f"{t['label']}: existing design and every modification",
                                  "target": t, "existing": ex.get(t["name"]), "bars": bars,
                                  "limit": t.get("value") if t["operator"] != "report" else None})
            specs.append({"kind": "change_heat", "title": "Change of every result in every modification (% against the existing design)",
                          "rows": [x["label"] for x in done[0]["results"]],
                          "cols": [c["title"].replace("Modification ", "Mod ") for c in done],
                          "values": [[next((x["change"] for x in c["results"] if x["label"] == lab), None) for c in done]
                                     for lab in [x["label"] for x in done[0]["results"]]],
                          "lower": [x.get("key") in LOWER_IS_BETTER for x in done[0]["results"]]})
        elif out.get("comparison"):
            specs.append({"kind": "compare", "title": "Existing and modified design", "items": out["comparison"]})
    else:
        for t in [t for t in plan["targets"] if not t.get("stage")]:
            pts = [(r["no"], (r["outputs"] or {}).get(t["name"])) for r in runs_all if r["status"] == "succeeded"]
            pts = [(a, float(b)) for a, b in pts if isinstance(b, (int, float))]
            if pts:
                gv = next((g for g in out.get("governing") or [] if g["label"] == t["label"]), None)
                specs.append({"kind": "runs", "title": f"{t['label']} in every scenario" if mode == "combinations"
                              else f"{t['label']} in the existing design (Run 0)", "target": t,
                              "points": pts, "best": gv["governing_run"] if gv else None, "scenario": mode == "combinations"})
    best = (out.get("calibration") or {}).get("best_run") if mode == "calibration" else \
        (out.get("optimization") or {}).get("best_run") if mode == "optimize" else None
    for t in plan["targets"]:  # V26.1.9 (batch 2): the settlement / wall deflection profile, one line per run
        if "|profile" not in (t.get("result") or "") or t.get("time"):
            continue
        rs = [r for r in runs_all if r["status"] == "succeeded" and ((r.get("outputs") or {}).get("_profiles") or {}).get(t["name"])]
        if not rs:
            continue
        keep = rs if len(rs) <= 8 else [r for i, r in enumerate(rs) if i == 0 or r["no"] == best or i % max(1, len(rs) // 7) == 0][:9]
        specs.append({"kind": "profile", "title": t["label"].split(" (max")[0] + " in " +
                      ("every run" if len(keep) == len(rs) else f"{len(keep)} of the {len(rs)} runs"),
                      "target": t, "along": "wall" if "profile:wall" in t["result"] else "surface",
                      "series": [{"label": f"Run {r['no']}" + (" (existing design)" if not r.get("inputs") else ""),
                                  "run": r["no"], "points": r["outputs"]["_profiles"][t["name"]], "best": r["no"] == best,
                                  "existing": not r.get("inputs")} for r in keep]})
    for ts in out.get("time") or []:  # V26.1.8: each result over time, one line per run (+ the field readings)
        specs.append({"kind": "time", "title": f"{ts['label']} over time" + (" against the field readings" if ts["measured"] else ""),
                      "target": {"label": ts["label"], "unit": ts["unit"]}, "measured": ts["measured"],
                      "stages": ts["stages"],
                      "series": [{"label": f"Run {x['run']}", "run": x["run"], "points": x["points"], "best": x["run"] == best,
                                  "existing": x["run"] == 0} for x in ts["runs"]]})
    return specs


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))
