"""Checks of the geometry of a run before it goes to PLAXIS: the points a run moves (geosel.displace) applied to the
model, then every line and two-point structure is looked at again.

- error: a line or structure with no length left, turned over (its end moved past its start), or two lines that
  overlap after the change but did not before (PLAXIS stops the mesh with "Lines overlap: ...").
- warning: an item whose length changes although it is not one of the items the variables change: it is changed
  indirectly, through a point it shares (e.g. a grout body stretched because only the end of the free length moved).
"""
import math

TOL = 1e-3


def _k(c) -> tuple:
    return tuple(round(float(v), 5) for v in (c[0], c[1], c[2] if len(c) > 2 else 0.0))


def segments(m: dict, moves: dict) -> list[dict]:
    """[{name, kind, a0, b0, a1, b1}] for the geometry lines and the two-point structures, before and after the run."""
    pts = {p["name"]: p for p in m["points"]}
    xyz = lambda p: (float(p["x"]), float(p["y"]), float(p.get("z", 0.0)))
    moved_at = {}  # a place -> where a point there goes (any point at that place that the run moves)
    for n, c in (moves or {}).items():
        if n in pts:
            moved_at.setdefault(_k(xyz(pts[n])), tuple(float(v) for v in c))
    new_n = lambda n: tuple(float(v) for v in moves[n]) if n in (moves or {}) else xyz(pts[n])
    new_c = lambda c: moved_at.get(_k(c), tuple(float(v) for v in (c[0], c[1], c[2] if len(c) > 2 else 0.0)))
    out = []
    for ln in m.get("lines") or []:
        if ln.get("a") in pts and ln.get("b") in pts:
            out.append({"name": ln["name"], "kind": "line", "a0": xyz(pts[ln["a"]]), "b0": xyz(pts[ln["b"]]),
                        "a1": new_n(ln["a"]), "b1": new_n(ln["b"])})
    for st in (m.get("structures") or []) + (m.get("loads") or []):
        ps = st.get("points") or []
        if len(ps) == 2:
            a0, b0 = (tuple(float(v) for v in (q[0], q[1], q[2] if len(q) > 2 else 0.0)) for q in ps)
            out.append({"name": st["name"], "kind": st.get("kind") or "", "a0": a0, "b0": b0, "a1": new_c(ps[0]), "b1": new_c(ps[1])})
    return out


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _len(a):
    return math.sqrt(_dot(a, a))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def overlap(a, b, c, d) -> float:
    """The length along which the segments ab and cd lie on top of each other (0 when they are not collinear)."""
    ab = _sub(b, a)
    L = _len(ab)
    if L < TOL:
        return 0.0
    for q in (c, d):
        if _len(_cross(ab, _sub(q, a))) / L > TOL:
            return 0.0
    tc, td = _dot(_sub(c, a), ab) / L, _dot(_sub(d, a), ab) / L
    return max(0.0, min(L, max(tc, td)) - max(0.0, min(tc, td)))


def on_segment(q, a, b) -> bool:
    ab = _sub(b, a)
    L = _len(ab)
    if L < TOL:
        return False
    t = _dot(_sub(q, a), ab) / (L * L)
    return -1e-6 <= t <= 1 + 1e-6 and _len(_cross(ab, _sub(q, a))) / L <= TOL


def check(m: dict, moves: dict, own: list[tuple] = ()) -> list[dict]:
    """[{level: error | warning, text}] for one run, one line per kind of problem. own: the (a, b) coordinates of the
    items the variables change on purpose (their length may change)."""
    if not moves:
        return []
    segs = segments(m, moves)
    # a geometry line and the structure drawn on it are one item: "AnchorRow1_Free1 (Line_1)"
    place = lambda s: tuple(sorted((tuple(round(v, 4) for v in s["a0"]), tuple(round(v, 4) for v in s["b0"]))))
    items: dict = {}
    for s in segs:
        items.setdefault(place(s), []).append(s)
    label = {}
    for key, ss in items.items():
        st = [x["name"] for x in ss if x["kind"] != "line"]
        ln = [x["name"] for x in ss if x["kind"] == "line"]
        label[key] = f"{', '.join(st)} ({', '.join(ln)})" if st and ln else ", ".join(st or ln)
    zero, turned, longer, over = [], [], [], []
    for key, ss in items.items():
        s = ss[0]
        L0, L1 = _len(_sub(s["b0"], s["a0"])), _len(_sub(s["b1"], s["a1"]))
        if L0 < TOL or (s["a0"] == s["a1"] and s["b0"] == s["b1"]):
            continue
        for x in ss:
            x["moved"] = True
        if L1 < 0.01:
            zero.append(f"{label[key]} ({L0:.3g} m → 0)")
        elif _dot(_sub(s["b0"], s["a0"]), _sub(s["b1"], s["a1"])) < 0:
            turned.append(label[key])
        elif abs(L1 - L0) > TOL and not any(on_segment(s["a0"], a, b) and on_segment(s["b0"], a, b) for a, b in own):
            longer.append(f"{label[key]} {L0:.3g} → {L1:.3g} m")
    keys = list(items)
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            s, t = items[ka][0], items[kb][0]
            if not (s.get("moved") or t.get("moved")):
                continue
            if overlap(s["a1"], s["b1"], t["a1"], t["b1"]) > TOL and overlap(s["a0"], s["b0"], t["a0"], t["b0"]) <= TOL:
                over.append(f"{label[ka]} with {label[kb]}")
    out = []
    if over:
        out.append({"level": "error", "text": "Lines overlap after the change (PLAXIS cannot mesh it): " + "; ".join(over) + "."})
    if zero:
        out.append({"level": "error", "text": "No length left: " + "; ".join(zero) + "."})
    if turned:
        out.append({"level": "error", "text": "Turned over (the end moves past the start): " + "; ".join(turned) + "."})
    if longer:
        out.append({"level": "warning", "text": "Changed through an attached point, not by a variable: " + "; ".join(longer) + "."})
    return out
