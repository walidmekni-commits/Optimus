"""Geometry selected in the 3D view (points, two-point segments, multi-point groups, with their similar copies) as
study variables: a shift of all their points in x / y / z, or a spacing (length) between the two points of each
segment. "Whole piles / beams" also moves every point of the piles and beams that a moved point belongs to, so a
pile spacing changes over the full pile length.

A selection entry is {id, label, members: [[point names], ...], whole: bool}; members[0] is what was picked and the
others are its similar copies (same vector between the points, e.g. the same part of the other anchors)."""
import math
import re



def _key(p) -> tuple:
    return tuple(round(float(v), 5) for v in (p[0], p[1], p[2] if len(p) > 2 else 0.0)) if isinstance(p, (list, tuple)) \
        else (round(float(p["x"]), 5), round(float(p["y"]), 5), round(float(p.get("z", 0.0)), 5))


def _by_xyz(m: dict) -> dict:
    out: dict = {}
    for p in m["points"]:
        out.setdefault(_key(p), []).append(p["name"])
    return out


def _point_names(m: dict, coords: list) -> list[str]:
    """Every model point at the given coordinates: PLAXIS often keeps two points at one place (the end of a free
    length and the start of its grout body), and both must move or the lines overlap."""
    idx, out = _by_xyz(m), []
    for q in coords or []:
        for n in idx.get(_key(q), []):
            if n not in out:
                out.append(n)
    return out


def coincident(m: dict, names: set[str]) -> set[str]:
    """The names plus every other point at the same place."""
    pts, idx = _pts(m), _by_xyz(m)
    out = set(names)
    for n in names:
        if n in pts:
            out |= set(idx.get(_key(pts[n]), []))
    return out

MODES = {"x": "shift in x", "y": "shift in y", "z": "shift in z", "s": "spacing"}
REF = re.compile(r"^(.+)\.(\w+)\+(?:\*(-?[\d.]+(?:[eE][-+]?\d+)?))?(?:~(-?[\d.]+(?:[eE][-+]?\d+)?))?$")  # = solvers.SHIFT_REF
WHOLE_KINDS = ("embedded_beam", "beam")


def _pts(m: dict) -> dict:
    return {p["name"]: p for p in m["points"]}


def _whole(m: dict, names: set[str]) -> set[str]:
    out = set(names)
    for st in m.get("structures") or []:
        if st.get("kind") in WHOLE_KINDS:
            pn = _point_names(m, st.get("points"))
            if names & set(pn):
                out |= set(pn)
    return out


def _structs(m: dict) -> list[tuple[dict, set[str]]]:
    return [(st, set(_point_names(m, st.get("points")))) for st in m.get("structures") or []]


def same_item(m: dict, a: str, b: str) -> dict | None:
    """The structure that has both points as its points (then a spacing between them is its length)."""
    return next((st for st, pn in _structs(m) if a in pn and b in pn), None)


def follow(m: dict, moving: set[str], fixed: set[str]) -> set[str]:
    """The moving points and the points of the structures attached to them that are not held by a fixed point: e.g.
    the grout body (geogrid) at the end of a lengthened anchor moves with the anchor end, keeping its own length."""
    out, todo = set(moving), True
    structs = _structs(m)
    while todo:
        todo = False
        for _, pn in structs:
            if pn & out and not pn & fixed and not pn <= out:
                out |= pn
                todo = True
    return out


def mode_text(m: dict, members: list[list[str]], mode: str) -> str:
    if mode == "s" and members and len(members[0]) == 2 and same_item(m, *members[0]):
        return "length"
    return MODES[mode]


def spacing(m: dict, members: list[list[str]]) -> float | None:
    pts = _pts(m)
    if not members or len(members[0]) != 2 or any(n not in pts for n in members[0]):
        return None
    a, b = pts[members[0][0]], pts[members[0][1]]
    return math.dist((a["x"], a["y"], a.get("z", 0.0)), (b["x"], b["y"], b.get("z", 0.0)))


def candidate(m: dict, entry: dict, mode: str, rng: tuple | None = None) -> dict | None:
    """The entry as a candidate (like plaxis_model.candidates) for plaxis_model.variable_from / study.build.
    rng: the (min, max) of the picked item; entry["ranges"] {"1": [min, max], ...}: another item's own range. In a
    run every item takes the same fraction of its own range, so the group still changes together in one run."""

    def scale(i):  # item i: new value = a + b * (value of the picked item)
        r = (entry.get("ranges") or {}).get(str(i))
        if not i or not rng or not r or None in rng or None in r:
            return 1.0, None
        lo0, hi0 = rng
        b = (r[1] - r[0]) / (hi0 - lo0) if hi0 != lo0 else 1.0
        return (b if abs(b) > 1e-9 else 1e-9), r[0] - (b if abs(b) > 1e-9 else 1e-9) * lo0
    pts = _pts(m)
    members = [[n for n in mem if n in pts] for mem in entry.get("members") or []]
    members = [mem for mem in members if mem]
    if not members or mode not in MODES:
        return None
    axes = m.get("axes") or ["x", "y"]
    n_sim = len(members) - 1
    label = f"{entry.get('label') or ' – '.join(members[0])}{f' and similar ({n_sim + 1} items)' if n_sim else ''} {mode_text(m, members, mode)}"
    refs = []
    if mode == "s":
        L = spacing(m, members)
        if L is None or L == 0:
            return None
        pairs = [mem for mem in members if len(mem) == 2]
        prev = {b: a for a, b in pairs}
        index = {tuple(mem): i for i, mem in enumerate(members)}

        def steps(n, seen=()):  # a row of similar spacings (pile 1 – 2 – 3 …): pile k moves k times the change
            return 1 + steps(prev[n], seen + (n,)) if n in prev and prev[n] not in seen else 0

        done = set()
        for a_name, b_name in pairs:
            a, b = pts[a_name], pts[b_name]
            d = [b[ax] - a[ax] for ax in axes]
            ln = math.sqrt(sum(x * x for x in d)) or 1.0
            k = steps(b_name)
            # the length of one pile / anchor: only its end moves; a spacing between two piles: the whole pile moves
            # attached items always follow (a "points only" option made grout bodies stretch and lines overlap)
            whole = not same_item(m, a_name, b_name)
            fixed = coincident(m, _whole(m, {a_name}) if whole else {a_name})
            moving = coincident(m, _whole(m, {b_name}) if whole else {b_name}) - fixed
            moving = coincident(m, follow(m, moving, fixed)) - fixed
            b_i, a_i = scale(index[(a_name, b_name)])
            base = L if a_i is None else (ln - a_i) / b_i  # new length a + b * value: moves b * (value - base)
            for n in sorted(moving - done):
                for ax, comp in zip(axes, d):
                    if abs(comp / ln) > 1e-9:
                        refs.append(f"{n}.{ax}+*{k * comp / ln * b_i:.6g}~{base:.6g}")
            done |= moving
        current = round(L, 6)
    else:
        if mode not in axes:
            return None
        done = set()
        for i, mem in enumerate(members):
            names = coincident(m, _whole(m, set(mem)))  # a shift moves these points (and points at the same place)
            b_i, a_i = scale(i)
            for n in sorted(names - done):
                refs.append(f"{n}.{mode}+" if a_i is None else f"{n}.{mode}+*{b_i:.6g}~{-a_i / b_i:.6g}")
            done |= names
        refs.sort(key=lambda r: r.split(".")[0])
        current = 0.0
    return {"key": f"grp:{entry['id']}", "section": "structures", "group": "Selected geometry", "label": label,
            "plaxis_ref": "; ".join(refs), "unit": "m", "current": current, "kind": "number"}


def displace(m: dict, variables: list[dict], inputs: dict) -> dict[str, list[float]]:
    """The new coordinates [x, y, z] of the model points that a run moves (its geometry variables applied)."""
    pts = _pts(m)
    new: dict[str, dict] = {}
    for v in variables:
        if v["name"] not in inputs:
            continue
        try:
            val = float(inputs[v["name"]])
        except (TypeError, ValueError):
            continue
        for one in (v.get("plaxis_ref") or "").split(";"):
            one = one.strip()
            if not one or "@" in one:
                continue
            sm = REF.match(one)
            if sm:
                name, ax = sm.group(1), sm.group(2)
                f, b = float(sm.group(3) or 1), float(sm.group(4) or 0)
            else:
                name, _, ax = one.rpartition(".")
            if name not in pts or ax not in ("x", "y", "z"):
                continue
            q = new.setdefault(name, {k: float(pts[name].get(k, 0.0)) for k in "xyz"})
            q[ax] = q[ax] + f * (val - b) if sm else val
    out = {n: [round(q[k], 6) for k in "xyz"] for n, q in new.items()
           if any(abs(q[k] - float(pts[n].get(k, 0.0))) > 1e-9 for k in "xyz")}
    from .plaxis_model import load_corner_moves
    out.update(load_corner_moves(m, variables, inputs))
    return out
