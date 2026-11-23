"""Units: the PLAXIS model's own units (read from the project) or the engineer's choice.

PLAXIS gives every number in the project's units (by default kN, m, day). The app labels the inputs in those
units. The results and the control parameters are shown in the chosen units: same as PLAXIS (imported), or
custom (e.g. mm for displacements, kN·m for moments...). Conversion happens only at the display: what is sent
to PLAXIS and saved in the database stays in PLAXIS units.
"""
import re

LENGTH = {"m": 1.0, "cm": 0.01, "mm": 0.001, "km": 1000.0, "ft": 0.3048, "in": 0.0254}
FORCE = {"kN": 1.0, "N": 0.001, "MN": 1000.0, "kip": 4.4482216152605, "lbf": 0.0044482216152605, "tf": 9.80665}
TIME = {"day": 1.0, "h": 1 / 24, "min": 1 / 1440, "s": 1 / 86400}
STRESS = {"kPa": 1.0, "MPa": 1000.0, "Pa": 0.001, "kN/m²": 1.0, "psf": 0.0478802589803, "psi": 6.89475729317831,
          "ksf": 47.8802589803}
DEFAULT = {"length": "m", "force": "kN", "time": "day"}
# custom units: per quantity (displacements in mm, stresses in kPa...), the rest from length and force
CUSTOM_DEFAULT = {"length": "m", "force": "kN", "time": "day", "displacement": "mm", "stress": "kPa"}
CHOICES = {"displacement": ["m", "cm", "mm", "ft", "in"], "force": list(FORCE), "length": ["m", "ft", "in", "cm", "mm"],
           "stress": ["kPa", "MPa", "Pa", "psf", "psi", "ksf"], "time": list(TIME)}
_STRESS = {"kPa": "kN/m²", "MPa": "MN/m²", "Pa": "N/m²", "psf": "lbf/ft²", "psi": "lbf/in²", "ksf": "kip/ft²"}
_SUP = {"²": 2, "³": 3, "⁴": 4}
# PLAXIS' own names (Project.UnitLength / UnitForce / UnitTime)
_PLAXIS_NAMES = {"meter": "m", "metre": "m", "millimeter": "mm", "centimeter": "cm", "kilometer": "km", "foot": "ft",
                 "feet": "ft", "inch": "in", "kilonewton": "kN", "newton": "N", "meganewton": "MN", "kip": "kip",
                 "kilopound": "kip", "pound": "lbf", "poundforce": "lbf", "lbf": "lbf", "ton": "tf", "tonforce": "tf",
                 "day": "day", "hour": "h", "minute": "min", "second": "s", "d": "day"}


def from_plaxis(length=None, force=None, time=None) -> dict:
    out = dict(DEFAULT)
    for k, v, table in (("length", length, LENGTH), ("force", force, FORCE), ("time", time, TIME)):
        v = str(v or "").strip()
        if v in table:
            out[k] = v
        elif v.lower().rstrip("s") in _PLAXIS_NAMES or v.lower() in _PLAXIS_NAMES:
            out[k] = _PLAXIS_NAMES.get(v.lower(), _PLAXIS_NAMES.get(v.lower().rstrip("s")))
    return out


SCALED = ("displacement", "force", "stress")  # quantities with an optional scale factor


def clean(u: dict | None) -> dict:
    u = u or {}
    out = {"mode": "custom" if u.get("mode") == "custom" else "plaxis"}
    for k, opts in CHOICES.items():
        out[k] = u.get(k) if u.get(k) in opts else CUSTOM_DEFAULT[k]
    sc = {}
    for k in SCALED:  # optional: the shown value = converted value x scale (1 = none)
        try:
            v = float((u.get("scale") or {}).get(k) or 1)
        except (TypeError, ValueError):
            v = 1.0
        sc[k] = v if v > 0 else 1.0
    out["scale"] = sc
    return out


def scale_of(unit: str, setup: dict) -> float:
    u = clean((setup or {}).get("units"))
    if u["mode"] != "custom":
        return 1.0
    toks = _tokens(unit or "")
    if not toks:
        return 1.0
    q = _quantity(toks) or ("force" if any(k == "force" for _, _, k in toks) else "")
    return u["scale"].get(q, 1.0) if q else 1.0


def _scaled_label(lab: str, sc: float) -> str:
    return lab if sc == 1 else f"{lab} ×{sc:g}"


def plaxis_of(setup: dict) -> dict:
    """The model's units (read from PLAXIS with the model, kN, m, day by default)."""
    setup = setup or {}
    got = setup.get("plaxis_units") or (setup.get("plaxis_model") or {}).get("units") or {}
    return {**DEFAULT, **{k: v for k, v in got.items() if k in DEFAULT}}


def display_of(setup: dict) -> dict:
    u = clean((setup or {}).get("units"))
    return plaxis_of(setup) if u["mode"] == "plaxis" else {k: u[k] for k in CHOICES}


def _tokens(unit: str):
    """'kN·m/m' -> [(kN, 1, 'force'), (m, 1, 'length'), (m, -1, 'length')]; None when a part is not L/F/T."""
    u = unit.strip()
    for k, v in _STRESS.items():
        u = re.sub(rf"(?<![A-Za-z]){k}(?![A-Za-z])", v, u)
    if not u or u in ("-", "°", "%"):
        return None
    out = []
    for i, part in enumerate(u.split("/")):
        for tok in re.split(r"[·*.]", part):
            tok = tok.strip()
            if not tok:
                continue
            m = re.fullmatch(r"([A-Za-z]+)(?:\^(-?\d+)|([²³⁴]))?", tok)
            if not m:
                return None
            base, exp = m.group(1), int(m.group(2) or _SUP.get(m.group(3) or "", 1))
            kind = "length" if base in LENGTH else "force" if base in FORCE else "time" if base in TIME else None
            if not kind:
                return None
            out.append((base, exp if i == 0 else -exp, kind))
    return out


def _quantity(toks) -> str:
    if not toks:
        return ""
    e = {"length": 0, "force": 0, "time": 0}
    for _, x, k in toks:
        e[k] += x
    if e == {"length": 1, "force": 0, "time": 0} and len(toks) == 1:
        return "displacement"
    if e == {"length": -2, "force": 1, "time": 0}:
        return "stress"
    return ""


def factor(unit: str, src: dict, dst: dict) -> float:
    """value_in_dst = value_in_src * factor. `unit` is written in kN/m/day; src/dst say what each base stands for
    (dst may also name a unit for displacements and one for stresses)."""
    toks = _tokens(unit or "")
    if not toks:
        return 1.0
    q = _quantity(toks)
    if q == "displacement" and (dst.get("displacement") or src.get("displacement")):
        return LENGTH[src.get("displacement") or src["length"]] / LENGTH[dst.get("displacement") or dst["length"]]
    if q == "stress" and (dst.get("stress") or src.get("stress")):
        st = lambda u: STRESS[u["stress"]] if u.get("stress") else FORCE[u["force"]] / LENGTH[u["length"]] ** 2
        return st(src) / st(dst)
    f = 1.0
    tables = {"length": LENGTH, "force": FORCE, "time": TIME}
    for _, exp, kind in toks:
        f *= (tables[kind][src[kind]] / tables[kind][dst[kind]]) ** exp
    return f


def label(unit: str, dst: dict) -> str:
    """The unit written in kN/m/day, written in the chosen units: 'kN·m/m' -> 'kip·ft/ft'. Unchanged when it is
    already in kN, m, day (keeps 'kN/m²')."""
    toks = _tokens(unit or "")
    if not toks:
        return unit or ""
    q = _quantity(toks)
    if q in ("displacement", "stress") and dst.get(q):
        return dst[q]
    if all(dst.get(k) == DEFAULT[k] for k in DEFAULT):
        return unit
    num = [(dst[k], e) for _, e, k in toks if e > 0]
    den = [(dst[k], -e) for _, e, k in toks if e < 0]
    sup = {1: "", 2: "²", 3: "³", 4: "⁴"}
    w = lambda xs: "·".join(b + sup.get(e, f"^{e}") for b, e in xs)
    return w(num) + "".join("/" + b + sup.get(e, f"^{e}") for b, e in den) if num else "1/" + w(den)


def to_display(value, unit: str, setup: dict):
    """A value in PLAXIS units -> (value, unit label) in the chosen units."""
    src, dst = plaxis_of(setup), display_of(setup)
    sc = scale_of(unit, setup)
    lab = _scaled_label(label(unit, dst), sc)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value, lab
    return value * factor(unit, src, dst) * sc, lab


def to_plaxis(value, unit: str, setup: dict):
    """A value typed in the chosen units -> PLAXIS units."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    return value * factor(unit, display_of(setup), plaxis_of(setup)) / scale_of(unit, setup)


def input_label(unit: str, setup: dict) -> str:
    """Inputs stay in PLAXIS units: their labels follow the model's units."""
    return label(unit, plaxis_of(setup))
