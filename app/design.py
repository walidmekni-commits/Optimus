"""Design consultant: reads an extracted cross section as a whole retaining-wall model (soil layers, excavation,
wall, anchors / struts, water, surcharge, model extents), checks it against good practice for PLAXIS models and
offers options. Everything is parametric, so the drawing, the PLAXIS geometry and the 3D model all come from the
same numbers and change together.

Coordinates as everywhere in the app: x to the right from the left model boundary, z = depth below the top of the
model, positive downwards, in metres."""
from __future__ import annotations

import copy
import math
from html import escape

SOIL_NAMES = ["Fill", "Sand", "Silty sand", "Clay", "Dense sand", "Weathered rock", "Rock"]
SOIL_COLOURS = ["#d9c7a3", "#e8d59b", "#d6c28a", "#b9a58c", "#cdb47a", "#a99a86", "#8f8a82"]
WALL_TYPES = ["Diaphragm wall", "Secant pile wall", "Contiguous pile wall", "Sheet pile wall", "Soldier pile wall"]


def _r(v, n=2):
    return round(float(v), n)


# --------------------------------------------------------------------------------------------- interpretation
def wall_toe_guess(toe, D):
    return toe if toe > 0 else D

def interpret(sec: dict, template: str | None = None) -> dict:
    """What kind of problem the drawing shows, and the best reading of it. A retaining wall / excavation is read
    into its parameters; anything else (embankment, foundation, slope, tunnel ...) stays a general model made of the
    drawn lines, with closed soil clusters. template forces a reading: "general" or "retaining_wall"."""
    if template == "general" or (template is None and not looks_like_wall(sec)):
        return general(sec)
    return interpret_wall(sec)


def looks_like_wall(sec: dict) -> bool:
    """A retaining wall: a vertical structure line with the ground at a different level on its two sides (an
    excavation, a basement, a quay). A pile under an embankment or a footing is not one."""
    nodes = sec.get("nodes") or []
    if len(nodes) < 3:
        return False
    xs, zs = [n[0] for n in nodes], [n[1] for n in nodes]
    x0, W, D = min(xs), max(xs) - min(xs), max(zs) - min(zs)
    segs = [(nodes[a][:2], nodes[b][:2], (t[0] if t else "soil")) for a, b, *t in sec.get("segments") or []]
    horiz = [(min(p[0], q[0]), max(p[0], q[0]), (p[1] + q[1]) / 2) for p, q, t in segs
             if t == "soil" and abs(p[1] - q[1]) < 0.05 * max(abs(p[0] - q[0]), 1e-9)]
    for (x1, z1), (x2, z2), t in segs:
        if t != "structure" or abs(x1 - x2) > 0.05 * abs(z1 - z2) or abs(z1 - z2) < 0.15 * D:
            continue
        wx = (x1 + x2) / 2
        if not 0.03 * W < wx - x0 < 0.97 * W:
            continue

        def ground(lo, hi):
            span, best = hi - lo, None
            levels = sorted(set(round(z, 2) for *_ab, z in horiz))
            for z in levels:
                cover = sum(max(0.0, min(b, hi) - max(a, lo)) for a, b, zz in horiz if abs(zz - z) < 0.05)
                if cover > 0.5 * span:
                    best = z
                    break
            return best
        gl, gr = ground(x0, wx - 0.5), ground(wx + 0.5, x0 + W)
        if gl is not None and gr is not None and abs(gl - gr) > 0.5:
            return True
    return False


def interpret_wall(sec: dict) -> dict:
    """Best reading of the extracted lines as a retaining-wall model. What could not be seen is filled with usual
    values and listed in `assumed`."""
    nodes = [tuple(map(float, n[:2])) for n in sec.get("nodes") or []]
    segs = [(a, b, (t[0] if t else "soil")) for a, b, *t in sec.get("segments") or []]
    assumed = []
    if len(nodes) < 2:
        nodes = [(0.0, 0.0), (40.0, 25.0)]
        assumed.append("No usable lines in the drawing: a typical anchored excavation is proposed; edit the values.")
    x0, x1 = min(n[0] for n in nodes), max(n[0] for n in nodes)
    z0, z1 = min(n[1] for n in nodes), max(n[1] for n in nodes)
    W, D = max(x1 - x0, 1.0), max(z1 - z0, 1.0)

    def P(i):
        x, z = nodes[i]
        return x - x0, z - z0

    lines = [(P(a), P(b), t) for a, b, t in segs if a < len(nodes) and b < len(nodes)]
    vert = [(p, q, t) for p, q, t in lines if abs(p[0] - q[0]) < 0.05 * max(abs(p[1] - q[1]), 1e-9) and abs(p[1] - q[1]) > 0.05 * D]
    horiz = [(p, q, t) for p, q, t in lines if abs(p[1] - q[1]) < 0.05 * max(abs(p[0] - q[0]), 1e-9) and abs(p[0] - q[0]) > 0.02 * W]

    # wall: vertical structure lines away from the model boundary, joined by their x (the drawing splits them
    # where other lines meet); the group with the longest run is the wall, a second run close by is its other face
    inner = [v for v in vert if 0.03 * W < v[0][0] < 0.97 * W]
    cand = [v for v in inner if v[2] == "structure"] or inner
    runs = {}
    for p_, q_, _t in cand:
        k = next((k for k in runs if abs(k - p_[0]) < 0.05), p_[0])
        lo, hi = sorted((p_[1], q_[1]))
        r = runs.setdefault(k, [lo, hi])
        r[0], r[1] = min(r[0], lo), max(r[1], hi)
    if runs:
        wx, (wtop, toe) = max(runs.items(), key=lambda kv: kv[1][1] - kv[1][0])
        faces = [k for k, (lo, hi) in runs.items() if k != wx and abs(k - wx) < 2.0 and abs(hi - toe) < 0.6]
        if faces:
            wt = abs(faces[0] - wx)
            wx = (wx + faces[0]) / 2
        else:
            wt = 0.8
            assumed.append("Wall thickness not drawn: 0.80 m assumed.")
    else:
        wx, toe, wt = 0.6 * W, 0.55 * D, 0.8
        assumed.append("No wall found in the drawing: a wall is proposed at 60 % of the width.")

    def side_levels(left: bool):
        lo, hi = (0, wx) if left else (wx, W)
        span = hi - lo
        out = []
        for p, q, t in horiz:
            a, b = sorted((p[0], q[0]))
            cover = max(0.0, min(b, hi) - max(a, lo))
            if cover > 0.6 * span and t != "water":
                out.append(p[1])
        return sorted(set(_r(z, 2) for z in out))

    left, right = side_levels(True), side_levels(False)
    left = [z for z in left if z < D - 0.05 * D]
    right = [z for z in right if z < D - 0.05 * D]

    def only(a, b):
        return [z for z in a if all(abs(z - y) > 0.1 for y in b)]

    lo_l, lo_r = (min(left) if left else 0.0), (min(right) if right else 0.0)
    if abs(lo_l - lo_r) > 0.3:  # the pit is the side whose ground is lower
        side = "left" if lo_l > lo_r else "right"
        pit, kept = (left, right) if side == "left" else (right, left)
        own = [z for z in only(pit, kept) if z <= wall_toe_guess(toe, D)]
        H = min(own) if own else max(lo_l, lo_r)
    else:
        side = "left" if wx > W / 2 else "right"
        H = min(toe * 0.6, D * 0.4)
        assumed.append("Excavation level not clear in the drawing: assumed at 60 % of the wall length.")
    H = _r(H)
    ret_levels = right if side == "left" else left
    exc_levels = left if side == "left" else right
    bounds = sorted(set(z for z in ret_levels + [z for z in exc_levels if z > H + 0.2] if 0.3 < z < D - 0.3))
    merged = []
    for z in bounds:
        if not merged or z - merged[-1] > 0.4:
            merged.append(z)
    layers = [{"name": SOIL_NAMES[min(i, len(SOIL_NAMES) - 1)], "bottom": _r(z)} for i, z in enumerate(merged)]
    layers.append({"name": SOIL_NAMES[min(len(merged), len(SOIL_NAMES) - 1)], "bottom": _r(D)})
    if len(layers) == 1:
        assumed.append("No soil layer boundaries found: one soil layer assumed. Add layers from the borehole log.")

    # anchors / struts: structure lines starting at the wall
    anchors, struts = [], []
    for p, q, t in lines:
        if t != "structure":
            continue
        for a, b in ((p, q), (q, p)):
            if abs(a[0] - wx) <= max(wt, 0.5) and abs(b[0] - wx) > 1.0:
                dx, dz = b[0] - a[0], b[1] - a[1]
                behind = (dx > 0) == (side == "left")
                ang = math.degrees(math.atan2(dz, abs(dx)))
                if behind and 3 < ang < 60:
                    # follow collinear pieces to the end of the anchor
                    end = b
                    for p2, q2, t2 in lines:
                        for c, d in ((p2, q2), (q2, p2)):
                            if t2 == "structure" and math.dist(c, end) < 0.05 and abs(
                                    math.degrees(math.atan2(d[1] - c[1], abs(d[0] - c[0]))) - ang) < 3:
                                end = d
                    L = math.dist(a, end)
                    if all(abs(an["z"] - a[1]) > 0.3 for an in anchors):
                        anchors.append({"z": _r(a[1]), "angle": _r(ang, 1), "free": _r(L * 0.6, 1),
                                        "bond": _r(L * 0.4, 1), "spacing": 2.5, "prestress": 300})
                elif not behind and abs(ang) < 3 and a[1] < H:
                    if all(abs(s - a[1]) > 0.3 for s in struts):
                        struts.append(_r(a[1]))
    anchors.sort(key=lambda a: a["z"])
    if anchors:
        assumed.append("Anchor free / bonded lengths split 60 / 40 from the drawn length; spacing 2.5 m and "
                       "prestress 300 kN assumed.")

    water = [p for p, q, t in lines if t == "water"] + [q for p, q, t in lines if t == "water"]
    w_ret = [z for x, z in water if (x > wx if side == "left" else x < wx)]
    w_exc = [z for x, z in water if (x < wx if side == "left" else x > wx)]
    if not w_ret:
        assumed.append("No water level drawn: groundwater assumed 2.0 m below ground.")
    params = {
        "side": side, "width": _r(W), "depth": _r(D),
        "wall": {"type": WALL_TYPES[0], "x": _r(wx), "thickness": _r(wt), "toe": _r(toe)},
        "excavation": {"depth": H, "berm_width": 0.0, "berm_height": 0.0},
        "anchors": anchors, "struts": struts, "layers": layers,
        "water": {"outside": _r(min(w_ret) if w_ret else 2.0), "inside": _r(min(w_exc) if w_exc else H + 0.5)},
        "surcharge": {"q": 10.0, "width": 10.0},
        "phi": 30.0, "type": "retaining_wall",
    }
    return normalise(params) | {"assumed": assumed}


# ------------------------------------------------------------------------------------------------- options
def normalise(p: dict) -> dict:
    p = copy.deepcopy(p)
    p.pop("assumed", None)
    if p.get("type") == "general":
        return p
    p["type"] = "retaining_wall"
    W, D = max(float(p.get("width", 40)), 2), max(float(p.get("depth", 25)), 2)
    w = p.setdefault("wall", {})
    w["x"] = min(max(float(w.get("x", W * 0.6)), 0.5), W - 0.5)
    w["thickness"] = min(max(float(w.get("thickness", 0.8)), 0.05), 3)
    e = p.setdefault("excavation", {})
    e["depth"] = min(max(float(e.get("depth", 8)), 0.0), D - 0.5)
    e["berm_width"] = max(float(e.get("berm_width", 0) or 0), 0.0)
    e["berm_height"] = min(max(float(e.get("berm_height", 0) or 0), 0.0), e["depth"])
    w["toe"] = min(max(float(w.get("toe", e["depth"] * 1.6)), e["depth"] + 0.1), D - 0.2)
    layers = sorted((l for l in p.get("layers") or [] if 0 < float(l.get("bottom", 0)) <= D + 1e-6),
                    key=lambda l: float(l["bottom"]))
    if not layers or float(layers[-1]["bottom"]) < D:
        layers.append({"name": layers[-1]["name"] if layers else "Soil", "bottom": D})
    for l in layers:
        l["bottom"] = _r(min(float(l["bottom"]), D))
    p["layers"] = layers
    p["anchors"] = sorted(({**a, **{k: float(a.get(k, d)) for k, d in (("z", 2), ("angle", 15), ("free", 8),
                                                                        ("bond", 6), ("spacing", 2.5), ("prestress", 300))}}
                           for a in p.get("anchors") or [] if 0 < float(a.get("z", 0)) < e["depth"]), key=lambda a: a["z"])
    p["struts"] = sorted(float(s) for s in p.get("struts") or [] if 0 <= float(s) < e["depth"])
    wa = p.setdefault("water", {})
    wa["outside"] = min(max(float(wa.get("outside", 2)), 0), D)
    wa["inside"] = min(max(float(wa.get("inside", e["depth"] + 0.5)), 0), D)
    s = p.setdefault("surcharge", {})
    s["q"], s["width"] = max(float(s.get("q", 10)), 0), max(float(s.get("width", 10)), 0)
    p["phi"] = min(max(float(p.get("phi", 30)), 15), 45)
    p["width"], p["depth"] = _r(W), _r(D)
    if p.get("side") not in ("left", "right"):
        p["side"] = "left"
    return p


def _behind(p):  # distance from the wall to the boundary on the retained side
    return p["width"] - p["wall"]["x"] if p["side"] == "left" else p["wall"]["x"]


def _front(p):
    return p["wall"]["x"] if p["side"] == "left" else p["width"] - p["wall"]["x"]


def wedge_distance(p, z):
    """Horizontal distance from the wall to the active failure wedge at depth z (line from the excavation base at
    the wall, at 45 + phi/2 to the horizontal)."""
    H = p["excavation"]["depth"]
    return max(H - z, 0) / math.tan(math.radians(45 + p["phi"] / 2))


def recommended(p: dict) -> dict:
    """The same design with the model set up as a PLAXIS consultant would: boundaries far enough not to influence
    the wall, anchors bonded beyond the active wedge, and the pit dewatered below the excavation base."""
    q = normalise(p)
    H, toe = q["excavation"]["depth"], q["wall"]["toe"]
    behind_need = max(3 * H, 2 * toe, 20.0)
    front_need = max(2 * H, toe, 15.0)
    depth_need = max(toe + max(H, 5.0), 2 * H + 5.0, q["depth"])
    add_b, add_f = max(0.0, behind_need - _behind(q)), max(0.0, front_need - _front(q))
    if q["side"] == "left":
        q["wall"]["x"] += add_f
    else:
        q["wall"]["x"] += add_b
    q["width"] = _r(q["width"] + add_b + add_f)
    q["wall"]["x"] = _r(q["wall"]["x"])
    if depth_need > q["depth"]:
        q["layers"][-1]["bottom"] = _r(depth_need)
        q["depth"] = _r(depth_need)
    for a in q["anchors"]:
        need = wedge_distance(q, a["z"]) + max(0.15 * H, 1.5)
        free_h = a["free"] * math.cos(math.radians(a["angle"]))
        if free_h < need:
            a["free"] = _r(math.ceil(need / math.cos(math.radians(a["angle"])) * 2) / 2, 1)
        a["bond"] = max(a["bond"], 6.0)
    q["water"]["inside"] = _r(max(q["water"]["inside"], H + 0.5))
    return normalise(q)


def conservative(p: dict) -> dict:
    """Recommended model with higher loads: groundwater at the ground surface behind the wall and 20 kPa
    surcharge (construction traffic)."""
    q = recommended(p)
    q["water"]["outside"] = 0.0
    q["surcharge"]["q"] = max(q["surcharge"]["q"], 20.0)
    return q


OPTIONS = {"as_drawn": ("As drawn", "Your drawing, read as a model. Nothing moved."),
           "recommended": ("Recommended for PLAXIS", "Boundaries moved out to 3H behind / 2H in front and 2H below, "
                           "anchors bonded beyond the active wedge, pit dewatered 0.5 m below the base."),
           "conservative": ("Conservative", "Recommended model with groundwater at the surface behind the wall and "
                            "20 kPa surcharge.")}


def options(p: dict) -> dict:
    if p.get("type") == "general":
        return {}
    base = normalise(p)
    return {"as_drawn": base, "recommended": recommended(base), "conservative": conservative(base)}


# ------------------------------------------------------------------------------------------------- checks
def remarks(p: dict) -> list[dict]:
    """Consultant checks: [{level: ok|warn|info, text}]"""
    if p.get("type") == "general":
        return general_remarks(p)
    p = normalise(p)
    out = []

    def add(level, text):
        out.append({"level": level, "text": text})

    H, toe = p["excavation"]["depth"], p["wall"]["toe"]
    d = toe - H
    if H <= 0:
        add("warn", "No excavation: set the excavation depth.")
        return out
    ratio = d / H
    if ratio < 0.4:
        add("warn", f"Wall embedment {d:.1f} m is only {ratio:.2f} H. Anchored walls usually need 0.5 to 1.0 H "
                    "below the excavation base; check toe stability.")
    else:
        add("ok", f"Wall embedment {d:.1f} m below the excavation base ({ratio:.2f} H).")
    b, f = _behind(p), _front(p)
    need_b, need_f = max(3 * H, 2 * toe), max(2 * H, toe)
    add("ok" if b >= need_b - 0.01 else "warn",
        f"Model boundary behind the wall at {b:.1f} m" + ("" if b >= need_b - 0.01 else
                                                          f": at least {need_b:.0f} m (3H or 2 × wall length) is "
                                                          "advised so it does not hold the soil back."))
    add("ok" if f >= need_f - 0.01 else "warn",
        f"Model boundary in front of the wall at {f:.1f} m" + ("" if f >= need_f - 0.01 else
                                                               f": at least {need_f:.0f} m (2H) is advised, or use the "
                                                               "pit centre line as a symmetry boundary."))
    below = p["depth"] - toe
    need_d = max(H, 5.0)
    add("ok" if below >= need_d - 0.01 else "warn",
        f"Model bottom {below:.1f} m below the wall toe" + ("" if below >= need_d - 0.01 else
                                                            f": {need_d:.0f} m or more is advised (or stop on rock)."))
    t = p["wall"]["thickness"]
    if p["wall"]["type"] == "Diaphragm wall" and not (0.5 <= t <= 1.5):
        add("warn", f"Diaphragm wall {t:.2f} m thick: usual panels are 0.6 to 1.2 m.")
    if not p["anchors"] and not p["struts"] and H > 4:
        add("warn", f"A {H:.1f} m deep excavation with a cantilever wall: check deflections; one row of anchors or "
                    "struts is usually needed above about 4 to 5 m.")
    prev = 0.0
    for i, a in enumerate(p["anchors"], 1):
        need = wedge_distance(p, a["z"]) + max(0.15 * H, 1.5)
        fh = a["free"] * math.cos(math.radians(a["angle"]))
        if fh < need:
            add("warn", f"Anchor {i} at {a['z']:.1f} m: the bonded length starts {fh:.1f} m behind the wall, inside "
                        f"the active wedge plus margin ({need:.1f} m). Lengthen the free length.")
        else:
            add("ok", f"Anchor {i} at {a['z']:.1f} m bonded beyond the active wedge ({fh:.1f} m ≥ {need:.1f} m).")
        if not 10 <= a["angle"] <= 35:
            add("warn", f"Anchor {i} at {a['angle']:.0f}°: 10 to 35° below horizontal is usual for grouting.")
        zc = a["z"] + (a["free"] + a["bond"] / 2) * math.sin(math.radians(a["angle"]))
        if zc < 4.0:
            add("warn", f"Anchor {i}: the bond zone centre is only {zc:.1f} m deep; 4 to 5 m of cover is usual.")
        if a["z"] - prev < 1.5 and i > 1:
            add("warn", f"Anchor rows {i - 1} and {i} are {a['z'] - prev:.1f} m apart; at least 1.5 to 2 m is "
                        "usual for the excavation steps.")
        prev = a["z"]
    if p["water"]["inside"] < H + 0.5 - 1e-6:
        add("warn", f"Water in the pit at {p['water']['inside']:.1f} m, above 0.5 m below the base ({H + 0.5:.1f} m): "
                    "the base would be wet; lower it (dewatering) or check base heave.")
    if p["water"]["outside"] < H and p["water"]["inside"] > p["water"]["outside"]:
        pass
    head = p["water"]["inside"] - p["water"]["outside"]
    if head > 0 and d > 0:
        i_exit = head / (2 * d + head)
        add("warn" if i_exit > 0.5 else "ok",
            f"Water head difference {head:.1f} m across the wall; rough exit gradient {i_exit:.2f} "
            + ("(above 0.5: check piping / uplift at the base)." if i_exit > 0.5 else "(acceptable, below 0.5)."))
    thin = [l["name"] for i, l in enumerate(p["layers"])
            if l["bottom"] - (p["layers"][i - 1]["bottom"] if i else 0) < 0.5]
    if thin:
        add("info", "Very thin layers (< 0.5 m): " + ", ".join(thin) + ". Merge them unless they matter, to keep the "
                    "mesh light.")
    add("info", "Mesh: refine around the wall and the anchors (local coarseness 0.3 to 0.5); interfaces on both "
                "wall faces with R_inter about 0.67.")
    return out


# --------------------------------------------------------------------------------------- stages (sequence)
def stages(p: dict) -> list[str]:
    if p.get("type") == "general":
        return ["Initial phase: K0 procedure (or gravity loading for sloping ground).",
                "Activate the structures and loads in the order they are built.",
                "Each construction step as its own phase; switch soil clusters off where soil is removed.",
                "Safety analysis (phi-c reduction) for the critical stage."]
    p = normalise(p)
    H = p["excavation"]["depth"]
    out = ["Initial phase: K0 procedure, groundwater at %.1f m." % p["water"]["outside"],
           f"Install the {p['wall']['type'].lower()} (to {p['wall']['toe']:.1f} m) and apply {p['surcharge']['q']:.0f} kPa "
           "surcharge."]
    supports = sorted([("anchor", a["z"], a) for a in p["anchors"]] + [("strut", s, None) for s in p["struts"]],
                      key=lambda t: t[1])
    for kind, z, a in supports:
        lvl = min(z + 0.5, H)
        out.append(f"Excavate to {lvl:.1f} m" + (f", lower the water in the pit to {lvl + 0.5:.1f} m." if
                                                     p["water"]["inside"] > p["water"]["outside"] else "."))
        out.append(f"Install and prestress the anchor at {z:.1f} m ({a['prestress']:.0f} kN)." if kind == "anchor"
                   else f"Install the strut at {z:.1f} m.")
    out.append(f"Excavate to the final level {H:.1f} m" + (f" (berm {p['excavation']['berm_width']:.1f} m wide)"
                                                         if p["excavation"]["berm_width"] > 0 else "") + ".")
    out.append("Safety analysis (phi-c reduction) for the final stage.")
    return out


def stage_levels(p: dict) -> list[float]:
    H = p["excavation"]["depth"]
    lv = sorted(set(_r(min(z + 0.5, H)) for z in [a["z"] for a in p["anchors"]] + p["struts"]))
    return [z for z in lv if 0 < z < H]


# ------------------------------------------------------------------------------------------- the geometry
def polygons(p: dict) -> dict:
    if p.get("type") == "general":
        return general_polygons(p)
    return wall_polygons(p)


def wall_polygons(p: dict) -> dict:
    """Soil clusters (per layer and side, excavated part kept as separate clusters for the stages), wall, anchors,
    struts, water lines. Used by the drawing, the PLAXIS geometry and the 3D model."""
    p = normalise(p)
    W, D, H = p["width"], p["depth"], p["excavation"]["depth"]
    wx, wt, toe = p["wall"]["x"], p["wall"]["thickness"], p["wall"]["toe"]
    left = p["side"] == "left"
    xa, xb = wx - wt / 2, wx + wt / 2
    bw, bh = p["excavation"]["berm_width"], p["excavation"]["berm_height"]
    levels = stage_levels(p)
    tops = [0.0] + [l["bottom"] for l in p["layers"][:-1]]
    soil = []
    for i, (top, lay) in enumerate(zip(tops, p["layers"])):
        bot = lay["bottom"]
        colour = SOIL_COLOURS[i % len(SOIL_COLOURS)]
        # retained side, wall cut out down to the toe
        r0, r1 = (xb, W) if left else (0, xa)
        soil.append({"layer": i, "name": lay["name"], "colour": colour, "part": "retained",
                     "pts": [[r0, top], [r1, top], [r1, bot], [r0, bot]]})
        # below the wall toe the soil runs under the wall
        if bot > toe:
            t2 = max(top, toe)
            soil.append({"layer": i, "name": lay["name"], "colour": colour, "part": "below",
                         "pts": [[xa, t2], [xb, t2], [xb, bot], [xa, bot]]})
        # excavated side: split at the excavation steps so each part can be switched off in its stage
        e0, e1 = (0, xa) if left else (xb, W)
        cuts = sorted(set([top, bot] + [z for z in levels + [H, H - bh] if top < z < bot]))
        for a, b in zip(cuts, cuts[1:]):
            if b > H + 1e-9:
                soil.append({"layer": i, "name": lay["name"], "colour": colour, "part": "front",
                             "pts": [[e0, a], [e1, a], [e1, b], [e0, b]]})
            elif bw > 0 and bh > 0 and a >= H - bh - 1e-9:
                # berm left against the wall in the last step
                bx = (xa - bw) if left else (xb + bw)
                bx = min(max(bx, e0), e1)
                berm = [[bx, a], [xa, a], [xa, b], [bx, b]] if left else [[xb, a], [bx, a], [bx, b], [xb, b]]
                rest = [[e0, a], [bx, a], [bx, b], [e0, b]] if left else [[bx, a], [e1, a], [e1, b], [bx, b]]
                soil.append({"layer": i, "name": lay["name"], "colour": colour, "part": "berm", "pts": berm})
                soil.append({"layer": i, "name": lay["name"], "colour": colour, "part": "excavated", "pts": rest})
            else:
                soil.append({"layer": i, "name": lay["name"], "colour": colour, "part": "excavated",
                             "pts": [[e0, a], [e1, a], [e1, b], [e0, b]]})
    wall = {"pts": [[xa, 0], [xb, 0], [xb, toe], [xa, toe]], "type": p["wall"]["type"]}
    anchors = []
    for a in p["anchors"]:
        s = 1 if left else -1
        c, sn = math.cos(math.radians(a["angle"])), math.sin(math.radians(a["angle"]))
        head = [xb if left else xa, a["z"]]
        mid = [head[0] + s * a["free"] * c, a["z"] + a["free"] * sn]
        end = [mid[0] + s * a["bond"] * c, mid[1] + a["bond"] * sn]
        anchors.append({"head": head, "free_end": mid, "end": end, "spacing": a["spacing"], "z": a["z"]})
    struts = [{"z": z, "from": [xa if left else xb, z], "to": [0 if left else W, z]} for z in p["struts"]]
    water = {"outside": [[xb if left else 0, p["water"]["outside"]], [W if left else xa, p["water"]["outside"]]],
             "inside": [[0 if left else xb, p["water"]["inside"]], [xa if left else W, p["water"]["inside"]]]}
    sur = p["surcharge"]
    load = None
    if sur["q"] > 0 and sur["width"] > 0:
        x0 = xb + 1.0 if left else max(0, xa - 1.0 - sur["width"])
        load = {"q": sur["q"], "x0": x0, "x1": min(W, x0 + sur["width"]) if left else xa - 1.0}
    return {"soil": soil, "wall": wall, "anchors": anchors, "struts": struts, "water": water, "load": load,
            "width": W, "depth": D, "excavation": H, "levels": levels, "side": p["side"]}


def to_geometry(p: dict) -> tuple[list, list]:
    if p.get("type") == "general":
        return p["nodes"], p["segments"]
    return wall_geometry(p)


def wall_geometry(p: dict) -> tuple[list, list]:
    """Nodes and lines for PLAXIS (via the normal simplify, so crossings get nodes): soil cluster outlines (soil),
    wall centre line, anchors, struts (structure), water levels (water)."""
    from .geometry import simplify
    g = polygons(p)
    polys = []
    for s in g["soil"]:
        polys.append(([tuple(q) for q in s["pts"]] + [tuple(s["pts"][0])], True, "SOIL"))
    xa, xb = g["wall"]["pts"][0][0], g["wall"]["pts"][1][0]
    toe = g["wall"]["pts"][2][1]
    polys.append(([(xa, 0), (xa, toe)], False, "WALL"))
    polys.append(([(xb, 0), (xb, toe)], False, "WALL"))
    polys.append(([(xa, toe), (xb, toe)], False, "WALL"))
    for a in g["anchors"]:
        polys.append(([tuple(a["head"]), tuple(a["free_end"]), tuple(a["end"])], False, "ANCHOR"))
    for s in g["struts"]:
        polys.append(([tuple(s["from"]), tuple(s["to"])], False, "STRUT"))
    for k in ("outside", "inside"):
        polys.append(([tuple(q) for q in g["water"][k]], False, "WATER"))
    geo = simplify(polys, y_is_depth=True)
    return geo["nodes"], geo["segments"]


# ----------------------------------------------------------------------------------------------- drawing
def svg(p: dict, title: str = "", chainage: float | None = None) -> str:
    if p.get("type") == "general":
        return general_svg(p, title, chainage)
    return wall_svg(p, title, chainage)


def wall_svg(p: dict, title: str = "", chainage: float | None = None) -> str:
    """A clean engineering drawing of the design: coloured soil layers, wall, anchors with grout bodies, struts,
    water levels, surcharge, levels and dimensions."""
    p = normalise(p)
    g = polygons(p)
    W, D, H = g["width"], g["depth"], g["excavation"]
    left = p["side"] == "left"
    S = min(1100 / W, 640 / D)  # px per metre
    mx, my = 90, 105
    w_px, h_px = W * S + mx + 150, D * S + my + 110

    def X(x):
        return mx + x * S

    def Y(z):
        return my + z * S

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_px:.0f} {h_px:.0f}" '
         'font-family="Segoe UI, Arial, sans-serif" font-size="12" style="background:#fff">',
         '<defs><pattern id="conc" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         '<rect width="8" height="8" fill="#9aa0a6"/><line x1="0" y1="0" x2="0" y2="8" stroke="#7d838a" '
         'stroke-width="2"/></pattern>'
         '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#eef5fb"/>'
         '<stop offset="1" stop-color="#ffffff"/></linearGradient></defs>',
         f'<rect x="{X(0):.1f}" y="{Y(-3):.1f}" width="{W * S:.1f}" height="{3 * S:.1f}" fill="url(#sky)"/>']
    for s in g["soil"]:
        if s["part"] == "excavated":
            continue
        pts = " ".join(f"{X(x):.1f},{Y(z):.1f}" for x, z in s["pts"])
        o.append(f'<polygon points="{pts}" fill="{s["colour"]}" stroke="{s["colour"]}" stroke-width="0.6"/>')
    # excavated zone: white with a dashed outline (removed during the stages)
    for s in g["soil"]:
        if s["part"] == "excavated":
            pts = " ".join(f"{X(x):.1f},{Y(z):.1f}" for x, z in s["pts"])
            o.append(f'<polygon points="{pts}" fill="#fbfbfb" stroke="#c9ccd1" stroke-width="0.8" '
                     'stroke-dasharray="5 4"/>')
    # layer boundaries
    tops = [0.0] + [l["bottom"] for l in p["layers"]]
    for i, l in enumerate(p["layers"]):
        z = l["bottom"]
        if z < D - 1e-6:
            r0, r1 = (g["wall"]["pts"][1][0], W) if left else (0, g["wall"]["pts"][0][0])
            spans = [(r0, r1)] + ([(0, W)] if z > H else [])
            for a_, b_ in spans:
                o.append(f'<line x1="{X(a_):.1f}" y1="{Y(z):.1f}" x2="{X(b_):.1f}" y2="{Y(z):.1f}" stroke="#6b5b45" '
                         'stroke-width="0.9"/>')
        zt = tops[i]
        lx = X(W - 0.5) if left else X(0.5)
        anchor = "end" if left else "start"
        o.append(f'<text x="{lx:.1f}" y="{Y((zt + z) / 2) + 4:.1f}" text-anchor="{anchor}" font-weight="600" '
                 f'fill="#3c3326">{escape(l["name"])}</text>')
    # outline of the model
    o.append(f'<rect x="{X(0):.1f}" y="{Y(0):.1f}" width="{W * S:.1f}" height="{D * S:.1f}" fill="none" '
             'stroke="#1c2230" stroke-width="1.6"/>')
    # ground and excavation lines
    e0, e1 = (0, g["wall"]["pts"][0][0]) if left else (g["wall"]["pts"][1][0], W)
    o.append(f'<line x1="{X(e0):.1f}" y1="{Y(H):.1f}" x2="{X(e1):.1f}" y2="{Y(H):.1f}" stroke="#1c2230" '
             'stroke-width="2"/>')
    for z in g["levels"]:
        o.append(f'<line x1="{X(e0):.1f}" y1="{Y(z):.1f}" x2="{X(e1):.1f}" y2="{Y(z):.1f}" stroke="#9aa0a6" '
                 'stroke-width="0.8" stroke-dasharray="3 3"/>')
        o.append(f'<text x="{X((e0 + e1) / 2):.1f}" y="{Y(z) - 3:.1f}" text-anchor="middle" fill="#6b7280" '
                 f'font-size="10.5">excavation step {z:.2f}</text>')
    # water
    for k, col in (("outside", "#1f77d0"), ("inside", "#1f77d0")):
        (xa_, z_), (xb_, _z) = g["water"][k]
        o.append(f'<line x1="{X(xa_):.1f}" y1="{Y(z_):.1f}" x2="{X(xb_):.1f}" y2="{Y(z_):.1f}" stroke="{col}" '
                 'stroke-width="1.6" stroke-dasharray="9 5"/>')
        cx = X((xa_ + xb_) / 2)
        o.append(f'<path d="M{cx - 7:.1f},{Y(z_) - 11:.1f} L{cx + 7:.1f},{Y(z_) - 11:.1f} L{cx:.1f},{Y(z_) - 1:.1f} Z" '
                 f'fill="none" stroke="{col}" stroke-width="1.4"/>')
        o.append(f'<text x="{cx + 11:.1f}" y="{Y(z_) - 3:.1f}" fill="{col}" font-size="11">GWL {z_:.2f}</text>')
    # wall
    wp = " ".join(f"{X(x):.1f},{Y(z):.1f}" for x, z in g["wall"]["pts"])
    o.append(f'<polygon points="{wp}" fill="url(#conc)" stroke="#1c2230" stroke-width="1.4"/>')
    # anchors
    for i, a in enumerate(g["anchors"], 1):
        (hx, hz), (fx, fz), (ex, ez) = a["head"], a["free_end"], a["end"]
        o.append(f'<line x1="{X(hx):.1f}" y1="{Y(hz):.1f}" x2="{X(fx):.1f}" y2="{Y(fz):.1f}" stroke="#1c2230" '
                 'stroke-width="1.6"/>')
        o.append(f'<line x1="{X(fx):.1f}" y1="{Y(fz):.1f}" x2="{X(ex):.1f}" y2="{Y(ez):.1f}" stroke="#4b5563" '
                 f'stroke-width="{max(5, 0.25 * S):.1f}" stroke-linecap="round"/>')
        o.append(f'<rect x="{X(hx) - (0 if left else 7):.1f}" y="{Y(hz) - 5:.1f}" width="7" height="10" fill="#1c2230"/>')
        o.append(f'<text x="{X(ex) + (6 if left else -6):.1f}" y="{Y(ez) + 4:.1f}" text-anchor="{"start" if left else "end"}" '
                 f'font-size="11">A{i}  z {hz:.2f}</text>')
    for s in g["struts"]:
        o.append(f'<line x1="{X(s["from"][0]):.1f}" y1="{Y(s["z"]):.1f}" x2="{X(s["to"][0]):.1f}" y2="{Y(s["z"]):.1f}" '
                 'stroke="#7c2d12" stroke-width="4"/>')
    # surcharge
    if g["load"]:
        x0, x1 = g["load"]["x0"], g["load"]["x1"]
        o.append(f'<rect x="{X(x0):.1f}" y="{Y(0) - 22:.1f}" width="{(x1 - x0) * S:.1f}" height="14" fill="#fde7c7" '
                 'stroke="#b45309"/>')
        n = max(2, int((x1 - x0) * S / 28))
        for k in range(n + 1):
            ax = X(x0 + (x1 - x0) * k / n)
            o.append(f'<line x1="{ax:.1f}" y1="{Y(0) - 8:.1f}" x2="{ax:.1f}" y2="{Y(0) - 1:.1f}" stroke="#b45309"/>')
        o.append(f'<text x="{X((x0 + x1) / 2):.1f}" y="{Y(0) - 27:.1f}" text-anchor="middle" fill="#b45309">'
                 f'q = {g["load"]["q"]:.0f} kPa</text>')
    # levels on the left: ground, excavation, toe, model base
    ty = X(-0.2) - 8
    for z, label in ((0, "Ground"), (H, "Excavation"), (g["wall"]["pts"][2][1], "Wall toe"), (D, "Model base")):
        o.append(f'<line x1="{ty - 40:.1f}" y1="{Y(z):.1f}" x2="{X(0):.1f}" y2="{Y(z):.1f}" stroke="#6b7280" '
                 'stroke-width="0.6"/>')
        o.append(f'<text x="{ty - 42:.1f}" y="{Y(z) - 3:.1f}" font-size="10.5" fill="#374151">{label}</text>')
        o.append(f'<text x="{ty - 42:.1f}" y="{Y(z) + 11:.1f}" font-size="10.5" fill="#374151">{"±0.00" if z == 0 else f"−{z:.2f}"}</text>')
    # dimension chains: bottom (widths), right (depths)
    yb = Y(D) + 28
    xs = sorted(set([0, W, g["wall"]["pts"][0][0], g["wall"]["pts"][1][0]]))
    o.append(f'<line x1="{X(0):.1f}" y1="{yb:.1f}" x2="{X(W):.1f}" y2="{yb:.1f}" stroke="#374151" stroke-width="0.8"/>')
    for x in xs:
        o.append(f'<line x1="{X(x):.1f}" y1="{yb - 5:.1f}" x2="{X(x):.1f}" y2="{yb + 5:.1f}" stroke="#374151"/>')
    for a, b in zip(xs, xs[1:]):
        if (b - a) * S > 24:
            o.append(f'<text x="{X((a + b) / 2):.1f}" y="{yb - 5:.1f}" text-anchor="middle" font-size="11">{b - a:.2f}</text>')
    o.append(f'<text x="{X(W / 2):.1f}" y="{yb + 20:.1f}" text-anchor="middle" font-size="12" font-weight="600">'
             f'Model width {W:.2f} m</text>')
    xr = X(W) + 22
    zs = sorted(set([0, H, g["wall"]["pts"][2][1], D]))
    o.append(f'<line x1="{xr:.1f}" y1="{Y(0):.1f}" x2="{xr:.1f}" y2="{Y(D):.1f}" stroke="#374151" stroke-width="0.8"/>')
    for z in zs:
        o.append(f'<line x1="{xr - 5:.1f}" y1="{Y(z):.1f}" x2="{xr + 5:.1f}" y2="{Y(z):.1f}" stroke="#374151"/>')
    for a, b in zip(zs, zs[1:]):
        lab = {0: "H", }.get(a, "")
        txt = f"H = {b - a:.2f}" if a == 0 and b == H else (f"d = {b - a:.2f}" if a == H and b == g["wall"]["pts"][2][1]
                                                            else f"{b - a:.2f}")
        del lab
        o.append(f'<text x="{xr + 8:.1f}" y="{Y((a + b) / 2) + 4:.1f}" font-size="11">{txt}</text>')
    # title
    t = escape(title or "Cross section")
    ch = f" · chainage {chainage:.1f} m" if chainage is not None else ""
    o.append(f'<text x="{mx:.1f}" y="24" font-size="15" font-weight="700" fill="#1c2230">{t}</text>')
    o.append(f'<text x="{mx:.1f}" y="42" font-size="11.5" fill="#4b5563">{escape(p["wall"]["type"])} '
             f'{p["wall"]["thickness"]:.2f} m · excavation {H:.2f} m · {len(p["anchors"])} anchor row(s)'
             f'{" · " + str(len(p["struts"])) + " strut row(s)" if p["struts"] else ""}{ch} · origin (0, 0) upper left, '
             'z positive down</text>')
    # scale bar
    sb = 5 if W < 60 else 10
    bx, by = X(W) - sb * S, Y(D) + 60
    o.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{sb * S / 2:.1f}" height="5" fill="#1c2230"/>'
             f'<rect x="{bx + sb * S / 2:.1f}" y="{by:.1f}" width="{sb * S / 2:.1f}" height="5" fill="#fff" stroke="#1c2230"/>'
             f'<text x="{bx:.1f}" y="{by + 18:.1f}" font-size="10.5">0</text>'
             f'<text x="{bx + sb * S:.1f}" y="{by + 18:.1f}" font-size="10.5" text-anchor="end">{sb} m</text>')
    o.append("</svg>")
    return "".join(o)


# ----------------------------------------------------------------------------------------- 3D: along the job
def _lerp(a, b, t):
    return a + (b - a) * t


def blend(p: dict, q: dict, t: float) -> dict:
    """Parameters between two sections: numbers change linearly; layers and anchors are matched by order."""
    p, q = normalise(p), normalise(q)
    r = copy.deepcopy(p if t < 0.5 else q)

    def num(path):
        a, b = p, q
        for k in path[:-1]:
            a, b = a[k], b[k]
        return _lerp(float(a[path[-1]]), float(b[path[-1]]), t)

    for path in (("width",), ("depth",), ("wall", "x"), ("wall", "thickness"), ("wall", "toe"),
                 ("excavation", "depth"), ("excavation", "berm_width"), ("excavation", "berm_height"),
                 ("water", "outside"), ("water", "inside"), ("surcharge", "q"), ("surcharge", "width"), ("phi",)):
        d = r
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = num(path)
    n = max(len(p["layers"]), len(q["layers"]))
    lay = []
    for i in range(n):
        a = p["layers"][min(i, len(p["layers"]) - 1)]
        b = q["layers"][min(i, len(q["layers"]) - 1)]
        lay.append({"name": (a if t < 0.5 else b)["name"], "bottom": _lerp(a["bottom"], b["bottom"], t)})
    r["layers"] = lay
    an = []
    for i in range(max(len(p["anchors"]), len(q["anchors"]))):
        if i < len(p["anchors"]) and i < len(q["anchors"]):
            a, b = p["anchors"][i], q["anchors"][i]
            an.append({k: _lerp(a[k], b[k], t) for k in ("z", "angle", "free", "bond", "spacing", "prestress")})
        else:
            src = p["anchors"] if i < len(p["anchors"]) else q["anchors"]
            if (src is p["anchors"]) == (t < 0.5):
                an.append(dict(src[i]))
    r["anchors"] = an
    return normalise(r)


def model3d(items: list[dict], mode: str = "linear", step: float = 1.0) -> dict:
    """items: [{name, y (chainage), design}] sorted by chainage. Returns slices along the job, each with its
    polygons, ready to extrude."""
    items = sorted(items, key=lambda it: it["y"])
    if any(it["design"].get("type") == "general" for it in items):
        mode = "step"  # different kinds of drawing can't be blended line by line
    if not items:
        return {"slices": [], "sections": []}
    out = []
    if len(items) == 1:
        L = max(20.0, polygons(items[0]["design"])["width"] * 0.6)
        out.append({"y0": items[0]["y"] - L / 2, "y1": items[0]["y"] + L / 2, "geo": polygons(items[0]["design"])})
    else:
        first, last = items[0], items[-1]
        ends = [first["y"] - max(5.0, (items[1]["y"] - first["y"]) / 2)]
        for a, b in zip(items, items[1:]):
            ends.append((a["y"] + b["y"]) / 2)
        ends.append(last["y"] + max(5.0, (last["y"] - items[-2]["y"]) / 2))
        if mode == "step":
            for i, it in enumerate(items):
                out.append({"y0": ends[i], "y1": ends[i + 1], "geo": polygons(it["design"])})
        else:
            out.append({"y0": ends[0], "y1": first["y"], "geo": polygons(first["design"])})
            for a, b in zip(items, items[1:]):
                L = b["y"] - a["y"]
                n = max(1, min(60, int(math.ceil(L / step))))
                for k in range(n):
                    t = (k + 0.5) / n
                    out.append({"y0": a["y"] + L * k / n, "y1": a["y"] + L * (k + 1) / n,
                                "geo": polygons(blend(a["design"], b["design"], t))})
            out.append({"y0": last["y"], "y1": ends[-1], "geo": polygons(last["design"])})
    return {"slices": out, "sections": [{"name": it["name"], "y": it["y"], "geo": polygons(it["design"])}
                                        for it in items]}


# ------------------------------------------------------------------------------- any other problem: general
def faces(nodes: list, segs: list) -> list[list[int]]:
    """Closed soil clusters: the faces of the planar graph made by the lines (water lines left out)."""
    adj = {}
    for a, b, *t in segs:
        if (t[0] if t else "soil") == "water" or a == b:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    order = {v: sorted(ns, key=lambda w: math.atan2(nodes[w][1] - nodes[v][1], nodes[w][0] - nodes[v][0]))
             for v, ns in adj.items()}
    seen, out = set(), []
    for u in adj:
        for v in adj[u]:
            if (u, v) in seen:
                continue
            cyc, a, b = [], u, v
            for _ in range(10000):
                if (a, b) in seen:
                    break
                seen.add((a, b))
                cyc.append(a)
                ns = order[b]
                i = ns.index(a)
                a, b = b, ns[(i - 1) % len(ns)]
            if len(cyc) >= 3:
                out.append(cyc)

    def area(c):
        return sum(nodes[c[i]][0] * nodes[c[(i + 1) % len(c)]][1] - nodes[c[(i + 1) % len(c)]][0] * nodes[c[i]][1]
                   for i in range(len(c))) / 2

    if not out:
        return []
    areas = [area(c) for c in out]
    sign = 1 if sum(1 for x in areas if x > 0) >= sum(1 for x in areas if x < 0) else -1
    big = max(range(len(out)), key=lambda i: abs(areas[i]))
    return [c for i, c in enumerate(out) if areas[i] * sign > 1e-6 and i != big or
            (areas[i] * sign > 1e-6 and len(out) == 1)]


def general(sec: dict) -> dict:
    nodes = [list(map(float, n[:2])) for n in sec.get("nodes") or []]
    segs = [[a, b, (t[0] if t else "soil")] for a, b, *t in sec.get("segments") or []]
    return {"type": "general", "nodes": nodes, "segments": segs, "assumed": []}


def general_polygons(p: dict) -> dict:
    nodes, segs = p["nodes"], p["segments"]
    if not nodes:
        return {"soil": [], "wall": None, "plates": [], "anchors": [], "struts": [], "water": {"lines": []},
                "load": None, "width": 1, "depth": 1, "excavation": 0, "levels": [], "side": "left", "general": True}
    x0, z0 = min(n[0] for n in nodes), min(n[1] for n in nodes)
    pts = [[n[0] - x0, n[1] - z0] for n in nodes]
    W, D = max(q[0] for q in pts) or 1, max(q[1] for q in pts) or 1
    fs = faces(pts, segs)
    tops = sorted(set(round(min(pts[i][1] for i in f), 1) for f in fs))
    soil = []
    for f in fs:
        k = tops.index(round(min(pts[i][1] for i in f), 1))
        soil.append({"layer": k, "name": f"Cluster {len(soil) + 1}", "colour": SOIL_COLOURS[k % len(SOIL_COLOURS)],
                     "part": "soil", "pts": [pts[i] for i in f]})
    plates = [[pts[a], pts[b]] for a, b, t in segs if t == "structure"]
    water = [[pts[a], pts[b]] for a, b, t in segs if t == "water"]
    return {"soil": soil, "wall": None, "plates": plates, "anchors": [], "struts": [], "water": {"lines": water},
            "load": None, "width": W, "depth": D, "excavation": 0, "levels": [], "side": "left", "general": True}


def general_remarks(p: dict) -> list[dict]:
    g = general_polygons(p)
    out = []
    n = len(g["soil"])
    if n == 0:
        out.append({"level": "warn", "text": "No closed soil clusters: PLAXIS needs closed areas. Close the outline "
                                             "(Extracted lines) or press Make PLAXIS model geometry."})
    else:
        out.append({"level": "ok", "text": f"{n} closed soil cluster(s) found; each gets a material in PLAXIS."})
    used = set()
    for a, b, t in p["segments"]:
        if t == "soil":
            used.add(a); used.add(b)
    deg = {}
    for a, b, t in p["segments"]:
        if t != "water":
            deg[a] = deg.get(a, 0) + 1; deg[b] = deg.get(b, 0) + 1
    loose = [i for i, d in deg.items() if d == 1]
    if loose:
        out.append({"level": "warn" if len(loose) > 4 else "info",
                    "text": f"{len(loose)} line end(s) not connected to anything (anchors and pile tips are fine; "
                            "otherwise join them to close the clusters)."})
    if g["plates"]:
        sx = [q[0] for pl in g["plates"] for q in pl]
        sz = [q[1] for pl in g["plates"] for q in pl]
        span = max(max(sz) - min(sz), max(sx) - min(sx), 1)
        side = min(min(sx), g["width"] - max(sx))
        below = g["depth"] - max(sz)
        out.append({"level": "ok" if side >= 2 * span else "warn",
                    "text": f"Structures are {side:.1f} m from the side boundary" + (
                        "" if side >= 2 * span else f"; about {2 * span:.0f} m (2 × their size) is advised.")})
        out.append({"level": "ok" if below >= span else "warn",
                    "text": f"Model bottom {below:.1f} m below the deepest structure" + (
                        "" if below >= span else f"; about {span:.0f} m is advised.")})
    if not g["water"]["lines"]:
        out.append({"level": "info", "text": "No water level drawn: set the phreatic level under Water conditions."})
    out.append({"level": "info", "text": "Problem type read as General (not a retaining wall). Edit the lines in "
                                         "Extracted lines; switch the type if the drawing is a retaining wall."})
    return out


def general_svg(p: dict, title: str = "", chainage: float | None = None) -> str:
    g = general_polygons(p)
    W, D = g["width"], g["depth"]
    S = min(1100 / W, 640 / D)
    mx, my = 90, 80
    X = lambda x: mx + x * S  # noqa: E731
    Y = lambda z: my + z * S  # noqa: E731
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W * S + mx + 120:.0f} {D * S + my + 90:.0f}" '
         'font-family="Segoe UI, Arial, sans-serif" font-size="12" style="background:#fff">']
    for s in g["soil"]:
        pts = " ".join(f"{X(x):.1f},{Y(z):.1f}" for x, z in s["pts"])
        o.append(f'<polygon points="{pts}" fill="{s["colour"]}" stroke="#6b5b45" stroke-width="0.8"/>')
        cx = sum(q[0] for q in s["pts"]) / len(s["pts"]); cz = sum(q[1] for q in s["pts"]) / len(s["pts"])
        o.append(f'<text x="{X(cx):.1f}" y="{Y(cz):.1f}" text-anchor="middle" font-size="10.5" fill="#3c3326">'
                 f'{escape(s["name"])}</text>')
    nodes = [[n[0] - min(q[0] for q in p["nodes"]), n[1] - min(q[1] for q in p["nodes"])] for n in p["nodes"]]
    for a, b, t in p["segments"]:
        (x1, z1), (x2, z2) = nodes[a], nodes[b]
        st = {"structure": 'stroke="#1c2230" stroke-width="3.5"', "water": 'stroke="#1f77d0" stroke-width="1.6" '
              'stroke-dasharray="9 5"'}.get(t, 'stroke="#3c3326" stroke-width="1"')
        o.append(f'<line x1="{X(x1):.1f}" y1="{Y(z1):.1f}" x2="{X(x2):.1f}" y2="{Y(z2):.1f}" {st}/>')
    yb = Y(D) + 26
    o.append(f'<line x1="{X(0):.1f}" y1="{yb:.1f}" x2="{X(W):.1f}" y2="{yb:.1f}" stroke="#374151"/>'
             f'<text x="{X(W / 2):.1f}" y="{yb + 18:.1f}" text-anchor="middle" font-weight="600">Model width {W:.2f} m'
             f'</text><text x="{X(W) + 10:.1f}" y="{Y(D / 2):.1f}">Depth {D:.2f} m</text>')
    ch = f" · chainage {chainage:.1f} m" if chainage is not None else ""
    o.append(f'<text x="{mx}" y="26" font-size="15" font-weight="700">{escape(title or "Cross section")}</text>'
             f'<text x="{mx}" y="44" font-size="11.5" fill="#4b5563">General model: {len(g["soil"])} soil clusters, '
             f'{len(g["plates"])} structure lines{ch} · thin = soil, thick = structure, dashed = water</text>')
    o.append("</svg>")
    return "".join(o)
