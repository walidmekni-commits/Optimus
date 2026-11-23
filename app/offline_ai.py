"""Offline assistant: no API key needed. Reads plain-English requests with geotechnical keywords and fills the
variable parameters, target outputs and goal. Rule based, so it understands phrases like:

    "make the wall height variable from 9 to 12 m step 0.5"
    "wall thickness between 0.6 and 1.2 m"
    "moment at the top of the wall less than 100 kN.m"
    "factor of safety above 1.5", "horizontal displacement below 40 mm"
    "load combinations LC1: 1.0, LC2: 1.35, LC3: 1.5"
    "minimize the wall thickness", "remove cohesion", "not thickness"
"""
import csv
import io
import re
from pathlib import Path

from .schema import SECTIONS, Goal, Target, Variable, norm_section

NUM = r"-?\d+(?:[.,]\d+)?"


def _f(s: str) -> float:
    return float(s.replace(",", "."))


# --- vocabulary -------------------------------------------------------------------------------------------
# key, label, unit, keyword regex, how to build the PLAXIS ref
VARIABLES = [
    ("height", "Wall height (top level)", "m", r"(wall\s+)?(height|top\s+level|top\s+of\s+(the\s+)?wall\s+level)", "point_top", "geometry"),
    ("embed", "Wall toe level / embedment", "m", r"(embedment|toe\s+level|wall\s+(length|depth))", "point_bottom", "geometry"),
    ("thickness", "Wall thickness", "m", r"(wall\s+)?thickness", "plate.d", "geometry"),
    ("excavation", "Excavation level", "m", r"excavation(\s+(level|depth))?", "", "stages"),
    ("mesh", "Mesh element size (relative)", "-", r"(\bmesh\b|element\s+size|coarseness)", "mesh.size", "mesh"),
    ("head", "Groundwater head (level)", "m", r"(ground\s*water|water\s+(level|table|head)|phreatic|\bhead\b)", "borehole.head", "water"),
    ("time", "Phase time interval", "day", r"(time\s+interval|consolidation\s+time|duration)", "phase.time", "stages"),
    ("phi", "Friction angle", "degrees", r"(friction\s+angle|\bphi\b)", "mat.phi", "materials"),
    ("cohesion", "Cohesion", "kPa", r"cohesion|\bc'?\s*ref\b", "mat.cref", "materials"),
    ("stiffness", "Young's modulus", "kPa", r"(young'?s?\s+modulus|stiffness|\bE\s*ref\b)", "mat.Eref", "materials"),
    ("gamma", "Unit weight", "kN/m3", r"unit\s+weight", "mat.gammaUnsat", "materials"),
    ("rinter", "Interface strength (Rinter)", "-", r"(rinter|interface\s+strength)", "mat.Rinter", "materials"),
    ("psi", "Dilatancy angle", "degrees", r"(dilatancy|\bpsi\b)", "mat.psi", "materials"),
    ("nu", "Poisson's ratio", "-", r"poisson", "mat.nu", "materials"),
    ("surcharge", "Surcharge", "kPa", r"surcharge", "surfaceload.qz", "loads"),
    ("prestress", "Anchor prestress (lock-off)", "kN", r"(prestress|lock-?off)", "", "loads"),
    ("anchor_len", "Anchor length", "m", r"anchor\s+length", "", "geometry"),
    ("spacing", "Anchor spacing", "m", r"(anchor\s+)?spacing", "", "geometry"),
    ("load", "Load combination", "-", r"(load\s+combination|load\s+case|load\s+factor|\bLC\d)", "lineload.qy_start", "loads"),
]

TARGETS = [
    ("fos", "Factor of safety", "FoS", "", r"(factor\s+of\s+safety|safety\s+factor|\bFoS\b|\bFS\b|\bmsf\b)"),
    ("moment", "Bending moment", "Plate.M11", "kN.m/m", r"(bending\s+)?moment"),
    ("shear", "Shear force", "Plate.Q12", "kN/m", r"shear\s+force"),
    ("anchor_force", "Anchor force", "NodeToNodeAnchor.AnchorForce3D", "kN", r"anchor\s+force"),
    ("ux", "Horizontal displacement", "Ux_max", "mm", r"(horizontal\s+(displacement|deflection)|wall\s+deflection|\bux\b)"),
    ("uz", "Settlement", "Uz_max", "mm", r"(settlement|vertical\s+displacement|\buz\b)"),
    ("utot", "Max total displacement", "Utot_max", "mm", r"(total\s+)?displacement|deformation"),
    ("file", "Model file of each run", "file", "", r"\bfiles?\b"),
]

OPS = [(r"(at\s+least|not\s+less\s+than|minimum(\s+of)?|>=|≥)", ">="), (r"(at\s+most|not\s+more\s+than|maximum(\s+of)?|<=|≤)", "<="),
       (r"(greater\s+than|more\s+than|above|over|exceed\w*|higher\s+than|>)", ">"),
       (r"(less\s+than|lower\s+than|below|under|smaller\s+than|<)", "<"), (r"(equal\s+to|equals|=)", "=")]

NEGATION = r"(remove|delete|drop|without|not|no|don'?t\s+(vary|change|use))\s+(the\s+)?(wall\s+)?"


def guess_section(v: dict) -> str:
    """Which Variables page section (PLAXIS mode) a variable belongs to, from its label and PLAXIS ref."""
    from .schema import norm_section
    return norm_section(_guess_section(v), v.get("plaxis_ref") or "")


def _guess_section(v: dict) -> str:
    text = f"{v.get('label', '')} {v.get('plaxis_ref', '')}"
    ref = (v.get("plaxis_ref") or "").lower()
    if ref.startswith("mesh."):
        return "mesh"
    if ".head" in ref or "waterlevel" in ref:
        return "water"
    if "load" in ref:
        return "loads"
    if ".timeinterval" in ref:
        return "stages"
    if re.search(r"\.(z|x|y)\b", ref) or ".d" == ref[-2:]:
        return "geometry"
    for _key, _label, _unit, pat, _how, sec in VARIABLES:
        if re.search(pat, text, re.I):
            return sec
    if re.search(r"\.(phi|cref|eref|e50ref|eoedref|eurref|gamma\w*|nu|psi|rinter|ea|ei|w)\b", ref):
        return "materials"
    return "geometry"


# --- model names (from the PLAXIS model description) ------------------------------------------------------
def _model_names(desc: str) -> dict:
    out = {"materials": [], "plates": [], "points": [], "surfaceloads": [], "lineloads": [], "phases": []}
    for line in (desc or "").splitlines():
        head, _, rest = line.partition(":")
        h = head.strip().lower()
        if h == "materials":
            out["materials"] = [re.sub(r"\s*\(.*\)$", "", m).strip() for m in rest.split(",") if m.strip()]
            out["material_ids"] = re.findall(r"\(([^)]*)\)", rest)
        elif h == "plates":
            out["plates"] = [x.strip() for x in rest.split(",") if x.strip()]
        elif h == "surfaceloads":
            out["surfaceloads"] = [x.strip() for x in rest.split(",") if x.strip()]
        elif h == "lineloads":
            out["lineloads"] = [x.strip() for x in rest.split(",") if x.strip()]
        elif h.startswith("points"):
            out["points"] = [(n, float(z)) for n, z in re.findall(r"(\w+)\([^,]+,[^,]+,\s*(" + NUM + r")\)", rest)]
        elif h == "phases":
            out["phases"] = re.findall(r"(\w+)\s*\(", rest)
    return out


def _ref(how: str, names: dict, mentioned_material: str | None) -> tuple[str, str]:
    """Returns (plaxis_ref, note)."""
    if how == "plate.d":
        return (f"{names['plates'][0]}.d", "") if names["plates"] else ("Plate_1.d", "check the plate name")
    if how.startswith("mat."):
        prop = how.split(".", 1)[1]
        mat = mentioned_material or (names.get("material_ids") or names["materials"] or [None])[0]
        return (f"{mat}.{prop}", "") if mat else ("", "which material?")
    if how == "mesh.size":
        return "Mesh.size", ""
    if how == "borehole.head":
        return "Borehole_1.Head", "Head is a level (elevation), check the borehole name"
    if how == "phase.time":
        ph = names["phases"][-1] if names["phases"] else "Phase_1"
        return f"{ph}.TimeInterval", f"I used {ph}; change the phase if needed"
    if how == "surfaceload.qz":
        return (f"{names['surfaceloads'][0]}.qz", "") if names["surfaceloads"] else ("", "which surface load?")
    if how == "lineload.qy_start":
        return (f"{names['lineloads'][0]}.qy_start", "") if names["lineloads"] else ("", "which load to scale?")
    if how in ("point_top", "point_bottom") and names["points"]:
        zs = [z for _, z in names["points"]]
        level = max(zs) if how == "point_top" else min(zs)
        pts = [n for n, z in names["points"] if abs(z - level) < 1e-6]
        return "; ".join(f"{p}.z" for p in pts), f"I used the points at z = {level:g}; change them if that's wrong"
    if how in ("point_top", "point_bottom"):
        return "", "which points define it? Write e.g. Point_7.z; Point_8.z (Test connection in Settings lists them)"
    return "", "which PLAXIS object.property?"


# --- parsing helpers ----------------------------------------------------------------------------------------
def _clauses(text: str) -> list[str]:
    """Split into one idea per clause: on sentence ends, ';', new lines, 'and'/'also' and commas, but not inside
    numbers (1.5), ranges ("between 25 and 35") or value lists ("LC1: 1.0, LC2: 1.35", "10, 20")."""
    t = re.sub(r"\.(?=\s|$)", "\n", text)
    t = re.sub(r"(?<!\d)\s+(?:and|also|plus)\s+(?!\d)", "\n", t, flags=re.I)
    t = re.sub(r"\s+and\s+(?=(?:save|report|check|keep|collect|target|minimi|maximi|make|set|i\s+want)\b)", "\n", t, flags=re.I)
    t = re.sub(r",\s*(?!\s*(?:-?\d|[A-Z]\w*\d\s*[:=]|and\s+-?\d|or\s+-?\d))", "\n", t)
    return [p.strip() for p in re.split(r"[;\n]+", t) if p.strip()]


def _range(clause: str) -> dict:
    c = clause.replace("–", "-")
    out = {}
    m = re.search(rf"(?:from|between)\s+({NUM})\s*\w*\s+(?:to|and|-)\s+({NUM})", c, re.I) or \
        re.search(rf"({NUM})\s*(?:m|kpa|kn|deg\w*|°)?\s*(?:-|to)\s*({NUM})", c, re.I)
    if m:
        out["min"], out["max"] = sorted([_f(m.group(1)), _f(m.group(2))])
    s = re.search(rf"(?:step|increment|every|by)\s+(?:of\s+)?({NUM})", c, re.I)
    if s:
        out["step"] = _f(s.group(1))
    labelled = re.findall(rf"(LC\s*\d+|[A-Z]\w*\d)\s*[:=]\s*({NUM})", c)
    if labelled:
        out["values"] = [f"{a.replace(' ', '')}: {_f(b):g}" for a, b in labelled]
    else:
        v = re.search(rf"(?:values?|options?|cases?|of)\s*:?\s*((?:{NUM}\s*(?:,|and|or)\s*)+{NUM})", c, re.I)
        if v and "min" not in out:
            out["values"] = [f"{_f(x):g}" for x in re.findall(NUM, v.group(1))]
    cur = re.search(rf"(?:currently|existing|now\s+is|is\s+now|current(?:ly)?\s+value\s+(?:is\s+)?)\s*({NUM})", c, re.I)
    if cur:
        out["current"] = f"{_f(cur.group(1)):g}"
    new = re.search(rf"(?:to|=|new\s+value\s+(?:of\s+)?)\s*({NUM})\s*(?:m|kpa|kn|deg\w*)?\s*$", c, re.I)
    if new and "min" not in out and "values" not in out:
        out["new"] = f"{_f(new.group(1)):g}"
    return out


def _criterion(clause: str) -> tuple[str | None, float | None]:
    for pat, op in OPS:
        m = re.search(rf"{pat}\s*({NUM})", clause, re.I)
        if m:
            return op, _f(m.group(m.lastindex))
    return None, None


def _material_in(clause: str, names: dict) -> str | None:
    for m in (names.get("material_ids") or []) + names["materials"]:
        if m and re.search(rf"\b{re.escape(m)}\b", clause, re.I):
            return m
    m = re.search(r"\b(clay|sand|silt|gravel|rock|fill)\b", clause, re.I)
    return m.group(1).capitalize() if m else None


def _next_name(prefix: str, rows: list[dict]) -> str:
    used = {r["name"] for r in rows}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


# --- main entry ---------------------------------------------------------------------------------------------
def converse(objective: str, model_description: str, setup: dict, history: list[dict]):
    from .ai import SetupTurn  # local import to avoid a cycle

    names = _model_names(model_description)
    section = setup.get("current_section") if setup.get("current_section") in SECTIONS else None
    variables = [dict(v) for v in setup.get("variables") or []]
    targets = [dict(t) for t in setup.get("targets") or []]
    goal = dict(setup["goal"]) if setup.get("goal") else None
    last = history[-1] if history else {"content": "", "attachments": []}
    text = last.get("content") or ""
    did, notes, asks = [], [], []

    # Files uploaded for the tables: CSV / text tables are read; drawings need the AI.
    for a in last.get("attachments") or []:
        added = _read_table_file(Path(a["path"]), a.get("section", "assistant"), variables, targets)
        if added:
            did.append(f"read {added} row(s) from {Path(a['path']).name}")
        else:
            notes.append(f"I can't read {Path(a['path']).name} without the AI (drawings and PDFs need an API key). "
                         "A CSV works: name, label, unit, plaxis_ref, min, max, step")

    for clause in _clauses(text):
        low = clause.lower()

        # removals: "remove cohesion", "not thickness"
        removed = False
        for key, label, *_rest in VARIABLES:
            pat = _rest[1]
            if re.search(NEGATION + r"(" + pat + r")", clause, re.I):
                before = len(variables)
                variables = [v for v in variables if v.get("key") != key and not re.search(pat, v["label"], re.I)]
                if len(variables) < before:
                    did.append(f"removed {label.lower()}")
                removed = True
        for key, label, _res, _u, pat in TARGETS:
            if re.search(NEGATION + r"(" + pat + r")", clause, re.I):
                before = len(targets)
                targets = [t for t in targets if not re.search(pat, t["label"], re.I)]
                if len(targets) < before:
                    did.append(f"removed {label.lower()}")
                removed = True
        if removed and not re.search(r"\b(instead|but|rather)\b", low):
            continue

        # goal
        g = re.search(r"\b(minimi[sz]e|maximi[sz]e|lightest|thinnest|shortest|cheapest|smallest|largest|biggest)\b", low)

        # target outputs (criteria) take priority when the clause has a comparison or "target/check"
        t_hit = None
        for key, label, res, unit, pat in TARGETS:
            if re.search(pat, clause, re.I):
                t_hit = (key, label, res, unit)
                break
        op, val = _criterion(clause)
        is_target = t_hit and (op or re.search(r"\b(target|check|output|report|collect|read|save|keep)\b", low))

        v_hit = None
        for key, label, unit, pat, how, sec in VARIABLES:
            if re.search(pat, clause, re.I):
                v_hit = (key, label, unit, how, sec)
                break

        var_words = re.search(r"\b(variable|vary|varying|range|change|changing)\b|\b(from|between)\s+" + NUM, low)
        if is_target:
            key, label, res, unit = t_hit
            if key == "moment" or key == "shear":
                if re.search(r"\btop\b", low):
                    res += " | Z = max"
                    label += " at top of wall"
                elif re.search(r"\b(toe|bottom)\b", low):
                    res += " | Z = min"
                    label += " at toe of wall"
            if key == "fos" and re.search(r"\bfinal\b", low) and names["phases"]:
                pass
            m_unit = re.search(r"(kN\.?m(/m)?|kNm|kN/m|kN|mm|cm|\bm\b)", clause)
            if m_unit and key not in ("fos", "file"):
                unit = m_unit.group(1)
            row = {"label": label, "result": res, "phase": "", "operator": op or "report", "value": val, "unit": unit}
            existing = next((t for t in targets if t["result"].split("|")[0].strip() == res.split("|")[0].strip()), None)
            if existing:
                existing.update(row)
                did.append(f"updated target {existing['name']}: {label}" + (f" {op} {val:g}" if op else ""))
            else:
                row["name"] = _next_name("T", targets)
                targets.append(row)
                did.append(f"added target {row['name']}: {label}" + (f" {op} {val:g} {unit}".rstrip() if op else " (collected)"))
            if g and t_hit and not v_hit:
                goal = {"direction": "minimize" if g.group(1).startswith(("mini", "light", "thin", "short", "cheap", "small")) else "maximize",
                        "of": (existing or row)["name"]}
            if not (v_hit and var_words):
                continue

        if v_hit:
            key, label, unit, how, sec = v_hit
            rng = _range(clause)
            mat = _material_in(clause, names) if how.startswith("mat.") else None
            existing = next((v for v in variables if v.get("key") == key or v["label"] == label), None)
            row = existing or {"name": _next_name("x", variables), "key": key, "label": label, "unit": unit,
                               "plaxis_ref": "", "kind": "range", "min": None, "max": None, "step": None,
                               "values": [], "current": None, "section": sec or section}
            if not row.get("plaxis_ref"):
                ref, note = _ref(how, names, mat)
                row["plaxis_ref"] = ref
                if note:
                    (asks if not ref else notes).append(f"{row['name']} ({label}): {note}")
            if "values" in rng:
                row.update(kind="values", values=rng["values"])
            if "min" in rng:
                row.update(kind="range", min=rng["min"], max=rng["max"])
            if "step" in rng:
                row["step"] = rng["step"]
            if "current" in rng:
                row["current"] = rng["current"]
            if "new" in rng and objective == "design_change":
                row["current"] = rng["new"]
            if existing:
                did.append(f"updated {row['name']} ({label})")
            else:
                variables.append(row)
                did.append(f"added {row['name']}: {label} (under {SECTIONS.get(norm_section(row['section'], row.get('plaxis_ref') or ''), 'Structures')})")
            if g:
                goal = {"direction": "minimize" if g.group(1).startswith(("mini", "light", "thin", "short", "cheap", "small")) else "maximize",
                        "of": row["name"]}
                did.append(f"goal: {goal['direction']} {row['name']}")
            continue

        if g and variables:
            goal = {"direction": "minimize" if g.group(1).startswith(("mini", "light", "thin", "short", "cheap", "small")) else "maximize",
                    "of": variables[0]["name"]}
            did.append(f"goal: {goal['direction']} {goal['of']}")

    # What is still missing?
    for v in variables:
        if objective == "design_change":
            if not v.get("current") and v.get("min") is None and not v.get("values"):
                asks.append(f"{v['name']} ({v['label']}): what is the new value?")
        elif v.get("kind") == "values" and not v.get("values") or v.get("kind") == "range" and (v.get("min") is None or v.get("max") is None):
            asks.append(f"{v['name']} ({v['label']}): what range? e.g. \"from 9 to 12 m step 0.5\"")
        if v.get("kind") == "range" and v.get("min") is not None and not v.get("step") and objective == "combinations":
            asks.append(f"{v['name']}: what step?")
    for v in variables:
        if not v.get("plaxis_ref"):
            hint = "which points? e.g. Point_7.z; Point_8.z" if re.search(r"height|level|embed|toe", v["label"], re.I) \
                else "which PLAXIS object.property?"
            asks.append(f"{v['name']} ({v['label']}): PLAXIS ref, {hint} (type it in the table)")
    for t in targets:
        if t["operator"] != "report" and t.get("value") is None:
            asks.append(f"{t['name']} ({t['label']}): what limit value?")
    if not variables:
        asks.append("What should change between runs? e.g. \"wall height from 9 to 12 m step 0.5\"")
    if not targets:
        asks.append("On the Output page: what should each run be checked against? e.g. \"moment at the top of the wall less than 100 kN.m\"")

    parts = []
    if did:
        parts.append("Done: " + "; ".join(did) + ".")
    elif text.strip():
        parts.append("I didn't recognise a parameter or target in that. Try the examples below the chat, or add rows with + Add.")
    parts += [n[0].upper() + n[1:] + ("" if n.endswith(".") else ".") for n in notes]
    if asks:
        parts.append("Still needed:\n" + "\n".join(f"• {a}" for a in dict.fromkeys(asks)))
    else:
        parts.append("Everything needed is filled in. Check the tables and press Review & run.")

    clean = lambda rows, model: [model.model_validate({k: r.get(k) for k in model.model_fields}) for r in rows]  # noqa: E731
    for v in variables:  # keep the internal 'key' for later edits, but the schema doesn't store it
        v.setdefault("values", [])
    turn = SetupTurn(status="clarify" if asks else "ready", message="\n".join(parts),
                     variables=clean(variables, Variable), targets=clean(targets, Target),
                     goal=Goal.model_validate(goal) if goal and goal.get("of") else None)
    return turn


def _read_table_file(path: Path, section: str, variables: list, targets: list) -> int:
    if path.suffix.lower() not in (".csv", ".txt", ".tsv"):
        return 0
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        rows = list(csv.DictReader(io.StringIO(text), dialect=csv.Sniffer().sniff(text[:2000], ",;\t")))
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for r in rows:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        is_target = section == "targets" or ("result" in r or "operator" in r or "criterion" in r)
        if is_target:
            targets.append({"name": r.get("name") or _next_name("T", targets), "label": r.get("label") or r.get("output", ""),
                            "result": r.get("result", ""), "phase": r.get("phase", ""),
                            "operator": r.get("operator") or r.get("criterion") or "report",
                            "value": _f(r["value"]) if re.fullmatch(NUM, r.get("value", "")) else None,
                            "unit": r.get("unit", "")})
        else:
            vals = [x.strip() for x in re.split(r"[|;]", r.get("values", "")) if x.strip()]
            num = lambda k: _f(r[k]) if re.fullmatch(NUM, r.get(k, "")) else None  # noqa: E731
            variables.append({"name": r.get("name") or _next_name("x", variables), "label": r.get("label") or r.get("parameter", ""),
                              "unit": r.get("unit", ""), "plaxis_ref": r.get("plaxis_ref") or r.get("ref", ""),
                              "kind": "values" if vals else "range", "min": num("min"), "max": num("max"),
                              "step": num("step"), "values": vals, "current": r.get("current") or None})
        n += 1
    return n
