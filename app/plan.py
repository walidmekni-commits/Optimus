"""Rev B of the module interface (Walid, 23-24 Nov 2026): the Study plan, the module chains, "What next?" at the end of
every module, and the handover of a finished study to the next module.

The Study plan is kept in the project's setup["plan"]:
    several   bool: one module (False), or several modules in the engineer's order (True)
    steps     ["sensitivity", "optimize", ...] the modules of the plan, in the engineer's order (a module can repeat)
    next      {mode: [modes]} the order of the "What next?" suggestions of each module, when changed by hand

Chains (Walid's pages 2 and 3, all modules except Advanced reporting):
    A  Sensitivity analysis -> Design optimization -> Design modification -> Design scenarios
    B  Calibration / Back-analysis -> Design modification -> Design scenarios
A link only pre-fills: nothing is calculated until the engineer presses Run in the next module.
"""
from . import study as S

MODES = ["sensitivity", "optimize", "design_change", "combinations", "calibration", "reporting"]
CHAINS = {"A": ["sensitivity", "optimize", "design_change", "combinations"],
          "B": ["calibration", "design_change", "combinations"]}
CHAIN_TEXT = {"A": "Sensitivity → Optimization → Modification → Scenarios",
              "B": "Back-analysis → Modification → Scenarios"}

# what each arrow hands over (sheet 2 of the Rev B report)
TAKES = {
    ("sensitivity", "optimize"): "the inputs ranked highest, with their ranges, as variable inputs; the controls with a Max or Min as targets",
    ("optimize", "design_change"): "the recommended run as Modification 1, the interpolated limit as Modification 2, the other passing runs as Puzzle boxes; the targets as acceptance criteria",
    ("design_change", "combinations"): "the chosen modification as the base scenario; the acceptance criteria as scenario targets",
    ("calibration", "design_change"): "the calibrated values as the model of Run 0; the field readings as a reference",
    ("sensitivity", "design_change"): "the run that best meets the controls as Modification 1",
}

# the default "What next?" of every module, in order, and its question (sheet 5)
DEFAULT_NEXT = {
    "sensitivity": ["optimize", "design_change", "reporting"],
    "optimize": ["design_change", "combinations", "reporting"],
    "design_change": ["combinations", "reporting", "optimize"],
    "combinations": ["reporting", "design_change"],
    "calibration": ["design_change", "combinations", "sensitivity"],
    "reporting": ["sensitivity", "calibration", "optimize"],
}
QUESTION = {
    "sensitivity": "Would you like to proceed with Design Optimization?",
    "optimize": "Would you like to proceed with Design Modification and Design Scenarios?",  # Walid's words
    "design_change": "Would you like to proceed with Design Scenarios?",
    "combinations": "Would you like to report the governing scenario in full?",
    "calibration": "Would you like to proceed with Design Modification on the calibrated model?",
    "reporting": "Would you like to study this design further?",
}
WHY = {
    "sensitivity": "Find which inputs drive the results most, and by how much.",
    "optimize": "Find the least over-design that meets every target.",
    "design_change": "Check one or more modified designs against the existing one, and rank them.",
    "combinations": "Test the chosen design under the other situations: SLS, ULS factors, water level, long term.",
    "calibration": "Match the model to the field readings.",
    "reporting": "Report the design in full, at every stage.",
}


def clean(p: dict | None) -> dict:
    p = dict(p or {})
    steps = [m for m in (p.get("steps") or []) if m in MODES][:12]
    nxt = {k: [m for m in v if m in MODES and m != k] for k, v in (p.get("next") or {}).items()
           if k in MODES and isinstance(v, list)}
    return {"several": bool(p.get("several")) and bool(steps), "steps": steps, "next": nxt,
            "chain": p.get("chain") if p.get("chain") in CHAINS else None}


def takes(a: str, b: str) -> str:
    return TAKES.get((a, b), "nothing calculated: the module opens with the results to read already chosen"
                     if b != "reporting" else "the design to report: the existing design, or the run chosen here")


def _title(mode: str) -> str:
    return S.MODES[mode]["title"]


def what_next(mode: str, plan: dict, out: dict | None = None) -> dict:
    """The "What next?" card of a finished study: the question and the suggested modules, in order. The order is the
    one saved by hand for this module, else the default, and it moves with the results (sheet 5, W4.1c)."""
    plan = clean(plan)
    order = list(plan["next"].get(mode) or DEFAULT_NEXT[mode])
    q = QUESTION[mode]
    flags = []
    out = out or {}
    if plan["several"] and mode in plan["steps"]:  # the Study plan says what comes next: it goes first
        i = plan["steps"].index(mode)
        if i + 1 < len(plan["steps"]):
            nxt = plan["steps"][i + 1]
            order = [nxt] + [m for m in order if m != nxt]
    if mode == "optimize" and out.get("optimization") and not out["optimization"].get("achieved"):
        q = "No run meets every target. Would you like to proceed with Design Modification to try other changes?"
        flags.append("more_runs")
    if mode == "design_change":
        done = [c for c in out.get("modifications") or [] if c.get("rank")]
        if done and not any(c["meets"] is not False for c in done):  # no modification passes: Optimization moves up
            order = ["optimize"] + [m for m in order if m != "optimize"]
            q = "No modification meets every criterion. Would you like to look for a better design with Design Optimization?"
    if mode == "calibration" and ((out.get("calibration") or {}).get("estimate") or {}).get("at_end"):
        flags.append("more_runs")  # the best fit is at the end of its range: + Add new runs comes first
    order = [m for m in dict.fromkeys(order) if m in MODES and m != mode]
    items = [{"mode": m, "title": _title(m), "why": WHY[m], "takes": takes(mode, m),
              "action": "report" if m == "reporting" else "proceed"} for m in order]
    others = [{"mode": m, "title": _title(m)} for m in MODES if m != mode and m not in order]
    return {"mode": mode, "question": q, "items": items, "others": others, "flags": flags,
            "custom": mode in plan["next"]}


def _crit_text(crit: list[dict], plan: dict) -> str:
    lab = {t.get("key"): (t.get("base_label") or t["label"], t.get("unit") or "") for t in plan["targets"]}
    sym = {"<=": "≤", ">=": "≥"}
    return ", ".join(f"{lab.get(c['output'], (c['output'], ''))[0]} {sym.get(c['operator'], c['operator'])} {c['value']}"
                     + (f" {lab[c['output']][1]}" if lab.get(c["output"], ("", ""))[1] else "") for c in crit)


def _decode(var: dict, val):
    """A run's input back into the value a study takes (a level by its name)."""
    if var.get("kind") == "values":
        lab = str(val[0] if isinstance(val, (list, tuple)) and val else val).strip()
        return next((v for v in var.get("values") or [] if v.split(":")[0].strip().lower() == lab.lower()), lab)
    v = val[0] if isinstance(val, (list, tuple)) and val else val
    return S._r(float(v)) if S._isnum(v) else v


def handover(src_task: dict, out: dict, runs: list[dict], to: str) -> dict:
    """The study the next module opens with: pre-filled from a finished study, editable, with its source written in
    ("link"). Nothing is calculated here."""
    plan = src_task["plan"]
    src = plan.get("study") or {}
    mode = src.get("mode") or src_task.get("objective")
    s = S.default(to)
    s["geo"] = src.get("geo") or []
    s["report"] = src.get("report") or s["report"]
    if src.get("time"):
        s["time"] = src["time"]
    if src.get("features"):
        s["features"] = src["features"]
    link = {"task": src_task["id"], "mode": mode, "title": _title(mode), "taken": []}
    # the results: what the source study read is what the next one compares / follows
    keys = list(dict.fromkeys(t["key"] for t in plan["targets"] if t.get("key") and not t.get("stage") and t["key"] != "file"))
    crit = [{"output": t.get("output"), "operator": t.get("operator") or "<=", "value": t.get("value")}
            for t in (src.get("targets") if mode == "optimize" else src.get("criteria") if mode == "design_change" else []) or []
            if t.get("output") and t.get("value") not in (None, "")]
    if to == "sensitivity":
        s["controls"] = [{"output": k, "how": "fixed"} for k in keys]
    elif to == "optimize":
        s["targets"] = crit or [{"output": c.get("output"), "operator": "<=" if c.get("how") == "max" else ">=",
                                 "value": c.get("value")} for c in src.get("controls") or [] if c.get("value") not in (None, "")]
        s["params"] = [dict(p) for p in src.get("params") or []]
    elif to == "calibration":
        s["params"] = [dict(p) for p in src.get("params") or []]
    else:
        s["outputs"] = keys
    if to == "combinations" and mode == "design_change":
        ch = next((c for c in out.get("modifications") or [] if c.get("chosen")), None)
        if ch:
            link["taken"].append(f"base scenario = {ch['title']} ({ch['values']})")
        if crit:
            link["taken"].append("acceptance criteria → scenario targets: " + _crit_text(crit, plan))
    if to == "design_change":
        s["criteria"] = crit
        vmap = {v["name"]: v for v in plan["variables"]}
        o = out.get("optimization") or {}
        rows = {r.get("run"): r for r in out.get("rows") or []}
        ok = [r for r in runs if r.get("status") == "succeeded" and r.get("inputs")]
        best = next((r for r in ok if r.get("no") == o.get("best_run")), None) if mode == "optimize" else None
        packages, names = [], {}
        if best:  # Modification 1 = the recommended run
            packages.append(dict(best["inputs"]))
            names[str(len(packages))] = f"{link['title'][:12].strip()} · Run {best['no']}"
            link["taken"].append(f"Modification 1 = Run {best['no']} ({rows.get(best['no'], {}).get('values', '')})")
            g = o.get("governing")
            gv = next((v for v in plan["variables"] if v["label"] == (g or {}).get("var")), None)
            if g and gv and S._isnum(g.get("x")):  # Modification 2 = the interpolated limit
                packages.append({**best["inputs"], gv["name"]: S._r(float(g["x"]))})
                names[str(len(packages))] = "At the limit"
                link["taken"].append(f"Modification 2 = the limit ({gv['label']} {S.fmt(S._r(float(g['x'])))})")
        elif mode == "sensitivity" and ok:
            packages.append(dict(ok[0]["inputs"]))
        if mode == "optimize":  # the other passing runs: Puzzle boxes
            checked = [t for t in plan["targets"] if t["operator"] != "report" and not t.get("stage")]
            passing = [r for r in ok if r is not best and all(S._meets(t, (r.get("outputs") or {}).get(t["name"])) for t in checked)]
            extra = [dict(r["inputs"]) for r in passing]
            if extra:
                link["taken"].append("the other passing runs (" + ", ".join(f"Run {r['no']}" for r in passing) + ") as Puzzle boxes")
        else:
            extra = []
        params = {}
        for pk in packages + extra:
            for name, val in pk.items():
                var = vmap.get(name)
                if not var:
                    continue
                p = params.setdefault(var["item"], {"item": var["item"], "news": []})
                p["news"].append(_decode(var, val))
        # Modification k takes the k-th value of each item (the runs of one optimization all set the same items, so
        # the values of one package stay at the same place in every item)
        s["params"] = list(params.values())
        for p in s["params"]:
            p["new"] = p["news"][0] if p["news"] else None
        s["mods"] = max(1, len(packages)) if packages else None
        s["names"] = names
        if crit:
            link["taken"].append("targets → acceptance criteria: " + _crit_text(crit, plan))
    s["link"] = link
    return s
