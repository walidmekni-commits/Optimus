"""Engineering insight on the runs of a study: why a run failed and what to do about it, the effect of each variable
input on the control parameters, and proposed combinations for the next runs (from the runs already calculated)."""
from __future__ import annotations

import itertools
import math
import re

from . import study as S

# ---------------------------------------------------------------- failed runs
# PLAXIS LogInfo codes that come back most often from a failed phase
LOGINFO = {
    "101": "the soil body seems to collapse (PLAXIS LogInfo 101)",
    "103": "the load advancement procedure failed (PLAXIS LogInfo 103)",
    "104": "the prescribed ultimate state was not reached (PLAXIS LogInfo 104)",
    "112": "there were not enough load steps to finish the phase (PLAXIS LogInfo 112)",
    "113": "the iterations did not converge (PLAXIS LogInfo 113)",
}

RULES = [  # (pattern, reason, proposed solution)
    (r"did not converge|LogInfo\s*(101|103|113)|collapse",
     "The PLAXIS calculation did not converge{log}. With these input values the model cannot reach equilibrium.",
     "Check the values of this run against the other runs: a weaker soil, a larger load or a shorter wall is the usual cause. "
     "Narrow the range of the variable (or remove this value from the puzzle), or in PLAXIS increase Max steps and the "
     "tolerated error of the phase and refine the mesh near the wall."),
    (r"LogInfo\s*112|not enough load steps",
     "PLAXIS used all its load steps before the phase finished.",
     "Increase Max steps (Numerical control parameters of the phase) or reduce the change of this run; then Restart the run."),
    (r"LogInfo\s*104|ultimate state",
     "The prescribed ultimate state of a phase was not reached (often a Safety phase that stopped early).",
     "Increase the number of steps of the Safety phase (e.g. 100 → 300) and check that the previous phase converged."),
    (r"Phase .* failed",
     "A calculation phase of PLAXIS failed{log}.",
     "Open the run file in PLAXIS Input and look at the log of that phase; check its inputs and Restart the run."),
    (r"did not take the new geometry|overlap|intersect",
     "PLAXIS refused the new geometry: a moved point merges with another item or crosses a soil boundary.",
     "Reduce the shift / new length of this run, or move the item together with the points connected to it "
     "(use a group in the geometry view). The geometry checks under Scheduled runs show the overlapping items."),
    (r"Could not set|not accepted|out of range",
     "PLAXIS did not accept a new value of this run ({what}).",
     "Check the value and its unit: it may be outside the allowed range (e.g. a negative stiffness, φ' ≥ 90°, ψ > φ') "
     "or the property may not exist for that material model."),
    (r"not found in the PLAXIS model|was not found",
     "An item of this run is not in the PLAXIS model ({what}): the model was changed after it was read.",
     "Read the model again (↻ Read from PLAXIS) and choose the variable input again, then Restart."),
    (r"no nodes match|Location filter",
     "The location filter of a result found no node ({what}).",
     "Change the filter of the control parameter (e.g. 'Z = max') or remove it."),
    (r"not a number",
     "A value of this run is not a number ({what}).",
     "Type the value as a number (or 'label: number' for a list)."),
    (r"remote scripting server|Could not reach|Connection|refused|timed out|No result from PLAXIS",
     "OPTIMUS lost the connection to PLAXIS during this run.",
     "Open PLAXIS Input, check Expert › Configure remote scripting server (port and password), and press Restart: "
     "only the failed runs are calculated again."),
    (r"cannot be removed",
     "A stage removed in this run is the start of later stages.",
     "Remove the later stages too, or choose another stage."),
    (r"No PLAXIS project file|data folder|could not save",
     "The PLAXIS project files could not be opened or saved.",
     "Upload the .p3d / .p2dx together with its data folder (a zip), and check there is free disk space."),
    (r"Stopped|Cancelled|closed during",
     "The run was stopped before it finished.",
     "Press Restart to calculate it."),
]


def diagnose(run: dict, variables: list[dict] | None = None) -> dict:
    """Failure reason and proposed solution of a failed run, from its error message and its inputs."""
    err = (run.get("error") or "").strip()
    if not err:
        reason = "PLAXIS returned no error message (the run was interrupted, or PLAXIS was closed)."
        sol = "Press Restart: only the failed runs are calculated again. If it fails again, open the run in PLAXIS Input."
    else:
        reason = sol = None
        code = re.search(r"LogInfo\s*(\d+)", err)
        log = ": " + LOGINFO.get(code.group(1), f"PLAXIS LogInfo {code.group(1)}") if code else ""
        what = re.sub(r"^\w*Error:\s*", "", err)[:160]
        for pat, rs, so in RULES:
            if re.search(pat, err, re.I):
                reason, sol = rs.format(log=log, what=what), so
                break
        if not reason:
            reason, sol = f"PLAXIS stopped with: {what}", ("Open the run file in PLAXIS Input and check the log of the "
                                                          "failed phase; correct the input and press Restart.")
    extreme = _extremes(run, variables or [])
    if extreme:
        sol += " This run uses " + "; ".join(extreme) + "."
    return {"reason": reason, "solution": sol}


def _extremes(run: dict, variables: list[dict]) -> list[str]:
    """The inputs of a run at the end of their range (the usual suspects of a failure)."""
    out = []
    for v in variables:
        val = (run.get("inputs") or {}).get(v["name"])
        x = _x(v, val)
        if x is None or v.get("kind") != "range" or v.get("min") is None or v.get("max") is None:
            continue
        lo, hi = float(v["min"]), float(v["max"])
        if hi > lo and (x - lo) / (hi - lo) <= .05:
            out.append(f"the lowest {v['label']} ({S.fmt(x)}{(' ' + v['unit']) if v.get('unit') else ''})")
        elif hi > lo and (hi - x) / (hi - lo) <= .05:
            out.append(f"the highest {v['label']} ({S.fmt(x)}{(' ' + v['unit']) if v.get('unit') else ''})")
    return out[:3]


# ---------------------------------------------------------------- effects and proposals
def _x(v: dict, val):
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[1] if len(val) > 1 and v.get("kind") == "values" else val[0]
    try:
        return float(str(val).split(":")[-1]) if isinstance(val, str) else float(val)
    except (TypeError, ValueError):
        return None


def goal(t: dict):
    """What the engineer wants from a control parameter: ('min', limit) = at least, ('max', limit) = at most,
    ('range', lo, hi), ('target', value), or None (only reported)."""
    op, val = t.get("operator"), t.get("value")
    if op in (">=", ">") and val is not None:
        return ("min", float(val))
    if op in ("<=", "<") and val is not None:
        return ("max", float(val))
    if t.get("measured") is not None:  # calibration: the measured value
        return ("target", float(t["measured"]))
    how, ref = t.get("how"), t.get("ref")
    if how == "range" and t.get("lo") is not None and t.get("hi") is not None:
        return ("range", float(t["lo"]), float(t["hi"]))
    if ref is None:
        return None
    if how == "max":
        return ("max", float(ref))
    if how == "min" or (t.get("key") == "fos"):
        return ("min", float(ref))
    return ("target", float(ref))


def meets(g, y: float, margin: float = 0.0) -> bool:
    if g is None or y is None:
        return True
    if g[0] == "min":
        return y >= g[1] * (1 + margin)
    if g[0] == "max":
        return abs(y) <= abs(g[1]) * (1 - margin)
    if g[0] == "range":
        return g[1] <= y <= g[2]
    return abs(y - g[1]) <= abs(g[1]) * .05


def goal_text(g, unit="") -> str:
    u = f" {unit}" if unit else ""
    if g is None:
        return "reported"
    return {"min": f"≥ {S.fmt(g[1])}{u}", "max": f"≤ {S.fmt(g[1])}{u}",
            "range": f"{S.fmt(g[1])} – {S.fmt(g[2])}{u}" if g[0] == "range" else "",
            "target": f"≈ {S.fmt(g[1])}{u}"}[g[0]]


def _solve(A, b):
    """Least squares (normal equations, Gauss elimination with a small ridge): no numpy needed."""
    k = len(A[0])
    M = [[sum(A[r][i] * A[r][j] for r in range(len(A))) + (1e-9 if i == j else 0) for j in range(k)] for i in range(k)]
    y = [sum(A[r][i] * b[r] for r in range(len(A))) for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-14:
            return None
        M[c], M[p], y[c], y[p] = M[p], M[c], y[p], y[c]
        for r in range(k):
            if r != c:
                f = M[r][c] / M[c][c]
                M[r] = [a - f * bb for a, bb in zip(M[r], M[c])]
                y[r] -= f * y[c]
    return [y[i] / M[i][i] for i in range(k)]


def model(plan: dict, runs: list[dict]):
    """A linear model of every control parameter on the numeric variable inputs, from the calculated runs.
    Returns (variables, {target name: (intercept, [slopes])}, x0 the existing values)."""
    vs = [v for v in plan["variables"] if v.get("kind") == "range" or _x(v, v.get("current")) is not None]
    x0 = [_x(v, v.get("current")) for v in vs]
    ok = [r for r in runs if r.get("status") == "succeeded"]
    fits = {}
    for t in plan["targets"]:
        X, Y = [], []
        for r in ok:
            y = (r.get("outputs") or {}).get(t["name"])
            if not S._isnum(y):
                continue
            row = []
            for v, c in zip(vs, x0):
                x = _x(v, (r.get("inputs") or {}).get(v["name"]))
                row.append(x if x is not None else c)
            if any(x is None for x in row):
                continue
            X.append(row)
            Y.append(float(y))
        if len(Y) < 2:
            continue
        used = [j for j in range(len(vs)) if len({round(x[j], 9) for x in X}) > 1]
        if not used:
            continue
        if len(Y) < len(used) + 1:  # too few runs for every variable together: one variable at a time
            sl = [0.0] * len(vs)
            for j in used:
                pts = [(x[j], y) for x, y in zip(X, Y) if all(abs(x[i] - (x0[i] if x0[i] is not None else x[i])) < 1e-9
                                                             for i in used if i != j)]
                if len({round(p[0], 9) for p in pts}) > 1:
                    mx, my = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
                    den = sum((p[0] - mx) ** 2 for p in pts)
                    sl[j] = sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else 0.0
            mx = [sum(x[j] for x in X) / len(X) for j in range(len(vs))]
            b0 = sum(Y) / len(Y) - sum(s * m for s, m in zip(sl, mx))
        else:
            sol = _solve([[1.0] + [x[j] for j in used] for x in X], Y)
            if sol is None:
                continue
            sl = [0.0] * len(vs)
            for k, j in enumerate(used):
                sl[j] = sol[k + 1]
            b0 = sol[0]
        pred = [b0 + sum(s * xx for s, xx in zip(sl, x)) for x in X]
        my = sum(Y) / len(Y)
        sst = sum((y - my) ** 2 for y in Y)
        r2 = 1 - sum((y - p) ** 2 for y, p in zip(Y, pred)) / sst if sst > 1e-18 else 1.0
        fits[t["name"]] = {"b0": b0, "slopes": sl, "r2": max(0.0, min(1.0, r2)), "n": len(Y), "used": used, "X": X}
    return vs, fits, x0


def effects(plan: dict, runs: list[dict]) -> list[dict]:
    """For each control parameter: how much each variable input moves it over its range, in which direction it must
    go to reach the criterion, and the value that reaches it (linear estimate from the runs)."""
    vs, fits, x0 = model(plan, runs)
    base = next((r for r in runs if r.get("status") == "succeeded" and not r.get("inputs")), None)
    out = []
    for t in plan["targets"]:
        f = fits.get(t["name"])
        if not f or t.get("detector"):
            continue
        g = goal(t)
        yb = (base.get("outputs") or {}).get(t["name"]) if base else None
        ref = float(yb) if S._isnum(yb) else sum(f["b0"] + sum(s * x for s, x in zip(f["slopes"], row)) for row in f["X"]) / len(f["X"])
        items = []
        for j in f["used"]:
            v, s = vs[j], f["slopes"][j]
            xs = [row[j] for row in f["X"]]
            lo, hi = (float(v["min"]), float(v["max"])) if v.get("kind") == "range" and v.get("min") is not None else (min(xs), max(xs))
            ch = s * (hi - lo)
            pct = ch / abs(ref) * 100 if ref else None
            direction = None
            reach = None
            if g and abs(s) > 1e-15:
                if g[0] == "max":  # at most |limit|: a displacement read as negative gets smaller toward zero
                    want_up, lim = ref < 0, (-abs(g[1]) if ref < 0 else abs(g[1]))
                elif g[0] == "range":
                    want_up, lim = ref < g[1], (g[1] if ref < g[1] else g[2])
                else:
                    want_up, lim = (g[0] == "min" or ref < g[1]), g[1]
                direction = "increase" if (s > 0) == want_up else "decrease"
                xb = x0[j] if x0[j] is not None else (lo + hi) / 2
                reach = xb + (lim - ref) / s
            items.append({"var": v["name"], "label": v["label"], "unit": v.get("unit") or "", "slope": s,
                          "change": ch, "pct": pct, "lo": lo, "hi": hi, "direction": direction,
                          "reach": reach, "reach_in_range": reach is not None and lo <= reach <= hi})
        items.sort(key=lambda e: -abs(e["pct"] if e["pct"] is not None else e["change"]))
        tot = sum(abs(e["change"]) for e in items) or 1.0
        for i, e in enumerate(items, 1):
            e["rank"], e["share"] = i, abs(e["change"]) / tot * 100
        out.append({"name": t["name"], "label": t["label"], "unit": t.get("unit") or "", "goal": goal_text(g, t.get("unit") or ""),
                    "met_base": meets(g, ref) if g else None, "base": ref, "r2": f["r2"], "n": f["n"], "items": items})
    return out


def proposals(plan: dict, runs: list[dict], k: int = 3) -> list[dict]:
    """Proposed combinations for the next runs: the values already tested (and the estimated value that reaches each
    criterion), put together so that every control parameter is met, with the least change from the Original File,
    the largest safety margin and a balanced one. Each one is an estimate to be checked with a PLAXIS run."""
    vs, fits, x0 = model(plan, runs)
    cons = [(t, goal(t), fits[t["name"]]) for t in plan["targets"] if t["name"] in fits and goal(t) and not t.get("detector")]
    if not cons:
        return []
    used = sorted({j for _, _, f in cons for j in f["used"]})
    if not used:
        return []
    cand = []
    for j in used:
        v = vs[j]
        vals = {round(row[j], 9) for _, _, f in cons for row in f["X"]}
        if v.get("kind") == "range" and v.get("min") is not None:
            lo, hi = float(v["min"]), float(v["max"])
            vals |= {round(lo + (hi - lo) * q / 4, 9) for q in range(5)}
            for t, g, f in cons:  # the value that reaches the criterion alone
                s = f["slopes"][j]
                if abs(s) > 1e-15 and g[0] != "range":
                    ref = f["b0"] + sum(sl * (x0[i] if x0[i] is not None else 0) for i, sl in enumerate(f["slopes"]))
                    lim = -abs(g[1]) if g[0] == "max" and ref < 0 else g[1]
                    xr = (x0[j] or 0) + (lim - ref) / s
                    if lo <= xr <= hi:  # rounded to a practical step, both ways
                        st = 10 ** math.floor(math.log10(max((hi - lo) / 20, 1e-9)))
                        vals |= {round(min(hi, max(lo, math.floor(xr / st) * st)), 6), round(min(hi, max(lo, math.ceil(xr / st) * st)), 6)}
        cand.append(sorted(vals))
    while len(cand) > 1 and _prod(len(c) for c in cand) > 20000:  # keep it quick: thin the longest list
        i = max(range(len(cand)), key=lambda q: len(cand[q]))
        cand[i] = cand[i][::2]
    tested = {tuple(round(row[j], 6) for j in used) for _, _, f in cons for row in f["X"]}
    span = [max(c) - min(c) or 1.0 for c in cand]
    pool = []
    for combo in itertools.product(*cand):
        x = list(x0)
        for j, val in zip(used, combo):
            x[j] = val
        if any(xx is None for xx in (x[j] for j in used)):
            continue
        pred, ok, margin = {}, True, []
        for t, g, f in cons:
            y = f["b0"] + sum(s * (xx if xx is not None else 0) for s, xx in zip(f["slopes"], x))
            pred[t["name"]] = y
            ok = ok and meets(g, y)
            if g[0] == "min":
                margin.append((y - g[1]) / (abs(g[1]) or 1))
            elif g[0] == "max":
                margin.append((abs(g[1]) - abs(y)) / (abs(g[1]) or 1))
            else:
                margin.append(-abs(y - g[1]) / (abs(g[1]) or 1))
        change = sum(abs(val - (x0[j] if x0[j] is not None else val)) / sp for j, val, sp in zip(used, combo, span))
        pool.append({"combo": combo, "pred": pred, "ok": ok, "margin": min(margin), "change": change,
                     "tested": tuple(round(c, 6) for c in combo) in tested})
    if not pool:
        return []
    good = [p for p in pool if p["ok"] and not p["tested"]] or [p for p in pool if p["ok"]]
    picks = []
    if good:
        picks.append(("Least change from the Original File", min(good, key=lambda p: (p["change"], -p["margin"]))))
        picks.append(("Largest safety margin", max(good, key=lambda p: (p["margin"], -p["change"]))))
        picks.append(("Balanced", min(good, key=lambda p: abs(p["margin"] - sorted(q["margin"] for q in good)[len(good) // 2]) + .1 * p["change"])))
    else:
        picks.append(("Closest to the criteria (none of the tested combinations meets them all)",
                      max(pool, key=lambda p: (p["margin"], -p["change"]))))
    out, seen = [], set()
    for why, p in picks:
        if p["combo"] in seen:
            continue
        seen.add(p["combo"])
        out.append({"why": why, "inputs": [{"var": vs[j]["name"], "item": vs[j].get("item"), "label": vs[j]["label"],
                                            "unit": vs[j].get("unit") or "", "value": round(val, 4),
                                            "existing": x0[j]} for j, val in zip(used, p["combo"])],
                    "predicted": [{"name": t["name"], "label": t["label"], "unit": t.get("unit") or "",
                                   "value": p["pred"][t["name"]], "goal": goal_text(g, t.get("unit") or ""),
                                   "ok": meets(g, p["pred"][t["name"]])} for t, g, _ in cons],
                    "meets": p["ok"], "tested": p["tested"],
                    "confidence": min(f["r2"] for _, _, f in cons)})
        if len(out) >= k:
            break
    return out


def _prod(xs):
    n = 1
    for x in xs:
        n *= x
    return n
