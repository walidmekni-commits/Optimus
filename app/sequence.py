"""Run sequencing: how many runs each criterion (Mesh, Materials, Loads ...) gets, and what changes every run.

  Logical sequencing   the parameter moves from its start value to its end value in equal parts: with N runs
                       each run adds 100/N % of the change (5 runs: 20 %, 40 %, 60 %, 80 %, 100 %).
                       A list of values (e.g. mesh levels) is walked evenly from the first to the last entry.
  Customized sequencing the engineer types the value of every parameter for every run.

Criteria are then combined into the compiled scenarios:
  product   every combination (Mesh 4 runs x Materials 5 runs = 20 runs)
  together  run k takes step k of every criterion (a criterion with fewer runs keeps its last step)
  oat       one criterion at a time, the others stay as in the existing model
Run 0 is the existing model when "baseline" is on.
"""
import itertools

from .planner import numeric, parse_value, show
from .schema import SECTIONS, Variable, norm_section

COMBINE = {"product": "Every combination", "together": "All criteria together, step by step",
           "oat": "One criterion at a time"}


def default_config(variables: list[Variable], old: dict | None = None) -> dict:
    """A sequence config for the current variables, keeping whatever the engineer already set."""
    old = old or {}
    crit = {}
    for sec in SECTIONS:
        vs = [v for v in variables if norm_section(v.section, v.plaxis_ref) == sec]
        if not vs:
            continue
        o = (old.get("criteria") or {}).get(sec) or {}
        n = int(o.get("runs") or _default_runs(vs))
        crit[sec] = {"runs": max(1, n), "mode": o.get("mode") if o.get("mode") in ("logical", "custom") else "logical",
                     "custom": o.get("custom") or {}}
    ends = {}
    for v in variables:
        o = (old.get("ends") or {}).get(v.name) or {}
        f, t = _default_ends(v)
        ends[v.name] = {"from": o.get("from", f), "to": o.get("to", t)}
    return {"enabled": bool(old.get("enabled", True)), "baseline": bool(old.get("baseline", True)),
            "combine": old.get("combine") if old.get("combine") in COMBINE else "product", "criteria": crit, "ends": ends}


def _default_runs(vs: list[Variable]) -> int:
    lists = [len(v.values) for v in vs if v.kind == "values" and v.values]
    return max(lists) if lists else 5


def _default_ends(v: Variable):
    if v.kind == "values":
        return None, None
    cur = numeric(parse_value(v.current)) if v.current not in (None, "") else None
    lo, hi = v.min, v.max
    if cur is None:
        return lo, hi
    ends = [x for x in (hi, lo) if x is not None and abs(x - cur) > 1e-12]
    if not ends:
        return cur, cur
    # towards the larger magnitude: a higher friction angle, a heavier (more negative) load
    return cur, max(ends, key=lambda x: (abs(x), x))


def _r(x: float) -> float:
    return float(f"{x:.6g}")


def logical_steps(v: Variable, n: int, ends: dict) -> list:
    """N values for one parameter under Logical sequencing."""
    if v.kind == "values":
        vals = [parse_value(x) for x in v.values]
        if not vals:
            return []
        if n == 1:
            return [vals[-1]]
        if n == len(vals):
            return vals
        return [vals[round((len(vals) - 1) * k / (n - 1))] for k in range(n)]
    f, t = ends.get("from"), ends.get("to")
    if f is None or t is None:
        f, t = v.min, v.max
    if f is None or t is None:
        return []
    f, t = float(f), float(t)
    return [_r(f + (t - f) * k / n) for k in range(1, n + 1)]


def criterion_steps(sec: str, vs: list[Variable], cfg: dict) -> tuple[list[dict], list[str]]:
    c = cfg["criteria"].get(sec) or {"runs": 1, "mode": "logical", "custom": {}}
    n, errs, steps = int(c["runs"]), [], []
    cols = {}
    for v in vs:
        if c["mode"] == "custom":
            raw = list((c.get("custom") or {}).get(v.name) or [])
            raw += [""] * (n - len(raw))
            cols[v.name] = [None if str(x).strip() == "" else parse_value(x) for x in raw[:n]]
        else:
            vals = logical_steps(v, n, cfg["ends"].get(v.name) or {})
            if not vals:
                errs.append(f"{v.name} ({v.label}): give a start and end value (or a list of values) for Logical sequencing.")
            cols[v.name] = vals
    if errs:
        return [], errs
    for k in range(n):
        steps.append({name: col[k] for name, col in cols.items() if k < len(col) and col[k] is not None})
    if c["mode"] == "custom" and not any(steps):
        errs.append(f"{SECTIONS[sec]}: type the values of at least one run in Customized sequencing.")
    return steps, errs


def build(variables: list[Variable], cfg: dict) -> tuple[list[dict], str, list[str]]:
    """(runs, how, errors): each run {label, inputs}; label is the scenario, e.g. 'Load 120 kPa / Coarse mesh'."""
    cfg = default_config(variables, cfg)
    per, errs, parts = [], [], []
    for sec in SECTIONS:
        vs = [v for v in variables if norm_section(v.section, v.plaxis_ref) == sec]
        if not vs:
            continue
        steps, e = criterion_steps(sec, vs, cfg)
        errs += e
        c = cfg["criteria"][sec]
        per.append(steps)
        parts.append(f"{SECTIONS[sec]} {len(steps)} run(s) ({'logical' if c['mode'] == 'logical' else 'customized'})")
    if errs or not per:
        return [], "", errs or ["Add at least one variable to sequence."]
    combos = []
    if cfg["combine"] == "product":
        for combo in itertools.product(*per):
            merged = {}
            for d in combo:
                merged.update(d)
            combos.append(merged)
        how = " × ".join(parts)
    elif cfg["combine"] == "together":
        for k in range(max(len(s) for s in per)):
            merged = {}
            for s in per:
                merged.update(s[min(k, len(s) - 1)])
            combos.append(merged)
        how = "together, step by step: " + ", ".join(parts)
    else:
        for s in per:
            combos += s
        how = "one criterion at a time: " + " + ".join(parts)
    runs = [{"label": scenario(c, variables), "inputs": c} for c in combos]
    if cfg["baseline"]:
        runs.insert(0, {"label": "Existing model", "inputs": {}})
    return runs, how + (", plus the existing model as Run 0" if cfg["baseline"] else ""), []


def short(v: Variable | dict) -> str:
    d = v if isinstance(v, dict) else v.model_dump()
    return d.get("label") or d.get("name")


def value_text(v: Variable | dict, val) -> str:
    """'120 kPa', 'Coarse mesh', 'LC2 (1.35)'."""
    d = v if isinstance(v, dict) else v.model_dump()
    if isinstance(val, list):
        val = tuple(val)
    if (d.get("section") == "mesh" or str(d.get("plaxis_ref", "")).lower().startswith("mesh")) and isinstance(val, tuple):
        return f"{val[0]} mesh"
    unit = d.get("unit") or ""
    return f"{show(val)}{(' ' + unit) if unit and not isinstance(val, tuple) else ''}"


def scenario(inputs: dict, variables) -> str:
    parts = []
    for v in variables:
        d = v if isinstance(v, dict) else v.model_dump()
        if d["name"] in inputs:
            txt = value_text(d, inputs[d["name"]])
            parts.append(txt if txt.endswith(" mesh") else f"{short(d)} {txt}")
    return " / ".join(parts) or "Existing model"


def run_no(idx: int, plan: dict) -> int:
    seq = plan.get("sequence")
    return idx if seq and seq.get("baseline") else idx + 1
