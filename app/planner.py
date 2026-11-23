"""Turns objective + variables + targets into a list of solver runs, and checks run outputs against targets."""
import itertools
import math
import re

from .config import max_runs
from .schema import OBJECTIVES, Goal, Target, Variable

_LABELLED = re.compile(r"^\s*(.+?)\s*[:=]\s*(-?[\d.]+(?:e-?\d+)?)\s*$", re.I)


def parse_value(s: str):
    """'1.35' -> 1.35, 'LC1: 1.35' -> ('LC1', 1.35), 'LC1' -> 'LC1'."""
    s = str(s).strip()
    try:
        return float(s)
    except ValueError:
        pass
    m = _LABELLED.match(s)
    if m:
        return (m.group(1), float(m.group(2)))
    return s


def numeric(v):
    if isinstance(v, tuple):
        return v[1]
    return v if isinstance(v, (int, float)) else None


def show(v) -> str:
    if isinstance(v, tuple):
        return f"{v[0]} ({_fmt(v[1])})"
    return _fmt(v) if isinstance(v, float) else str(v)


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def levels(var: Variable, max_levels: int | None = None) -> list:
    """Values to try for one variable. A range uses its step, or 5 points; max_levels coarsens it evenly."""
    if var.kind == "values":
        return [parse_value(v) for v in var.values]
    lo, hi = var.min, var.max
    if lo is None or hi is None:
        return []
    lo, hi = min(lo, hi), max(lo, hi)
    if hi == lo:
        return [lo]
    if var.step and var.step > 0:
        count = int(math.floor((hi - lo) / var.step + 1e-9)) + 1
        if max_levels is None or count <= max_levels:
            return [round(lo + i * var.step, 10) for i in range(count)]
    else:
        count = 5
    n = max(2, min(count, max_levels or count))
    return [round(lo + (hi - lo) * i / (n - 1), 10) for i in range(n)]


def validate(objective: str, variables: list[Variable], targets: list[Target], sequenced: bool = False) -> list[str]:
    """sequenced: the runs come from the run plan (logical / customized sequencing), which checks the values itself."""
    errs = []
    if objective not in OBJECTIVES:
        errs.append(f"Unknown objective '{objective}'.")
    if not variables:
        errs.append("Add at least one variable parameter.")
    if not targets:
        errs.append("Add at least one target output.")
    names = [v.name for v in variables] + [t.name for t in targets]
    if len(set(names)) != len(names):
        errs.append("Every variable and target needs a unique name.")
    for v in variables:
        if objective == "design_change":
            if v.current is None and not v.values and v.min is None:
                errs.append(f"{v.name}: give the new value.")
        elif not sequenced and not levels(v):
            errs.append(f"{v.name}: give a range (min and max) or a list of values.")
    for t in targets:
        if not t.result:
            errs.append(f"{t.name}: say which result to read (e.g. FoS or Utot_max).")
        if t.operator != "report" and t.value is None:
            errs.append(f"{t.name}: give the limit value for '{t.operator}'.")
    return errs


def build_runs(objective: str, variables: list[Variable]) -> tuple[list[dict], str]:
    """Returns (runs, how) where each run is {label, inputs:{name: value}}."""
    if objective == "design_change":
        changed = {}
        for v in variables:
            val = v.current if v.current is not None else (v.values[0] if v.values else v.min)
            changed[v.name] = parse_value(val)
        return ([{"label": "Existing design", "inputs": {}},
                 {"label": "Changed design", "inputs": changed}],
                "the existing design and the changed design")

    if objective == "combinations":
        grids = [levels(v) for v in variables]
        how = " × ".join(f"{v.name} ({len(g)})" for v, g in zip(variables, grids))
    else:
        # Grid search, coarsened so the total stays under MAX_RUNS.
        per = max(2, int(max_runs() ** (1 / max(1, len(variables)))))
        grids = [levels(v, per) for v in variables]
        how = "a grid of " + " × ".join(f"{v.name} ({len(g)})" for v, g in zip(variables, grids))

    runs = []
    for combo in itertools.product(*grids):
        inputs = {v.name: val for v, val in zip(variables, combo)}
        runs.append({"label": ", ".join(f"{k}={show(x)}" for k, x in inputs.items()), "inputs": inputs})
    if objective == "optimize":
        runs.insert(0, {"label": "Existing design", "inputs": {}})
    return runs, how


def check(targets: list[Target], outputs: dict) -> tuple[dict, bool]:
    checks, ok = {}, True
    for t in targets:
        if t.operator == "report":
            continue
        val = outputs.get(t.name)
        if not isinstance(val, (int, float)):
            checks[t.name] = False
            ok = False
            continue
        lim = t.value
        passed = {">": val > lim, ">=": val >= lim, "<": val < lim, "<=": val <= lim,
                  "=": math.isclose(val, lim, rel_tol=1e-3)}[t.operator]
        checks[t.name] = passed
        ok = ok and passed
    return checks, ok


def pick_best(runs: list[dict], goal: Goal | None, targets: list[Target]) -> dict | None:
    """Best passing run: by goal if given, else by the largest margin on the first checked target."""
    ok = [r for r in runs if r["status"] == "succeeded" and r["passed"]]
    if not ok:
        return None

    def value(r, name):
        if name in (r["inputs"] or {}):
            return numeric(r["inputs"][name])
        return (r["outputs"] or {}).get(name)

    if goal:
        scored = [(value(r, goal.of), r) for r in ok]
        scored = [(s, r) for s, r in scored if isinstance(s, (int, float))]
        if scored:
            scored.sort(key=lambda x: x[0], reverse=goal.direction == "maximize")
            return scored[0][1]
    checked = [t for t in targets if t.operator != "report"]
    if checked:
        t = checked[0]
        sign = 1 if t.operator in (">", ">=") else -1
        return max(ok, key=lambda r: sign * ((r["outputs"] or {}).get(t.name) or 0))
    return ok[0]


def describe_plan(objective: str, variables, targets, goal, runs, how) -> str:
    title = OBJECTIVES[objective]["title"]
    crit = [f"{t.name} {t.operator} {_fmt(t.value)}{(' ' + t.unit) if t.unit else ''}"
            for t in targets if t.operator != "report"]
    collect = [t.name for t in targets if t.operator == "report"]
    parts = [f"{title}: run {len(runs)} Plaxis calculation(s) covering {how}."]
    if crit:
        parts.append("Check " + ", ".join(crit) + ".")
    if collect:
        parts.append("Collect " + ", ".join(collect) + " from every run.")
    if objective in ("optimize", "new_design"):
        if goal:
            parts.append(f"Pick the passing design with the {'lowest' if goal.direction == 'minimize' else 'highest'} {goal.of}.")
        else:
            parts.append("Pick the passing design with the largest safety margin.")
    if objective == "design_change":
        parts.append("Compare the changed design with the existing one.")
    return " ".join(parts)
