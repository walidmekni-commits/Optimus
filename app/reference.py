"""Reference Results for the Calibration / Back-analysis study, read from an Excel file (.xlsx, .xlsm, .xls) or a CSV.

The file holds ONE table (the sheet named "Reference Results", else the first sheet). Its first non-empty row is the
header. Columns (any order, names not case sensitive):

    Result   required  the PLAXIS result: its key (utot), its name in the app (Max total displacement |u|) or its
                       PLAXIS name (Utot_max)
    Value    required  the measured or benchmark value (a number)
    Unit     required  the unit of Value: mm, cm, m, ft, in / kN, MN, N, kip / kN·m/m (kNm/m) / kN/m, kN/m² (kPa),
                       MPa... It must be a unit of the same kind as the result (a length for a displacement...).
                       Leave it empty (or "-") only for the factor of safety.
    Stage    optional  the construction stage of the reading: its name in PLAXIS (Phase_3) or its label, or
                       "Long term"
    Day      optional  the day of the reading, counted from the start of works
    Time     optional  the time / duration of the reading in min, h or day: the unit in the header, Time (min), or
                       in a "Time unit" column; else the unit of the study's X-axis (days by default)
    X        optional  the value of the X-axis variable at the reading (e.g. the load of a pile test): the reading
                       goes on the stage whose X value is nearest, and is drawn at its own X

A row with no Stage, Day, Time or X is the final value of that result (one per result). Any other row is one
reading of the field series, placed by the first of Day, Time, X, Stage that is filled in. Values are converted from the file's unit into the units the study shows.

parse() returns {"measured": [...], "series": [...], "rows": n, "warnings": [...], "longterm": days or None} in the shapes study.build()
already reads, or raises ReferenceError with every problem found (row by row). Nothing is saved on bad input.
"""
import csv
import io
import re

from . import plaxis_model, units

SHEET = "Reference Results"
MAX_ROWS = 5000
COLUMNS = {  # column -> accepted header names (lower case, without the text in brackets)
    "result": ("result", "plaxis result", "output", "quantity"),
    "value": ("value", "measured value", "reference value", "measured", "reference"),
    "unit": ("unit", "units"),
    "stage": ("stage", "phase", "construction stage"),
    "day": ("day", "days"),
    "time": ("time", "duration", "elapsed time", "time from start"),
    "time_unit": ("time unit", "unit of time", "duration unit"),
    "x": ("x", "x value", "x-axis", "x axis"),
}
TIME_ALIASES = {"day": "day", "days": "day", "d": "day", "h": "h", "hr": "h", "hrs": "h", "hour": "h", "hours": "h",
                "min": "min", "mins": "min", "minute": "min", "minutes": "min", "s": "s", "sec": "s", "second": "s",
                "seconds": "s"}
REQUIRED = ("result", "value", "unit")
LONGTERM = "longterm"  # the long-term consolidation phase of study.build()
GUIDE = [  # the table shown in the app, the README and the template's Guide sheet
    ("Result", "required", "The PLAXIS result: its key (utot), its name in the app (Max total displacement |u|) or its PLAXIS name (Utot_max)."),
    ("Value", "required", "The measured or benchmark value, a number."),
    ("Unit", "required", "The unit of Value, of the same kind as the result: mm, cm, m / kN, MN / kN·m/m / kN/m / kPa, kN/m², MPa. Empty or - only for the factor of safety."),
    ("Stage", "optional", "The construction stage of the reading (its PLAXIS name or label), or Long term."),
    ("Day", "optional", "The day of the reading from the start of works."),
    ("Time", "optional", "The time or duration of the reading from the start, in the Time unit: write the unit in the header, Time (min), or in a Time unit column (min, h, day). Without one it is in the unit of the X-axis (days by default)."),
    ("X", "optional", "The value of the X-axis variable at the reading (e.g. the applied load of a pile test, in its unit): it is compared with the stage whose X value is nearest."),
]
# the first of Day, Time, X, Stage that is filled in places the reading
EXAMPLE = [  # the template's example rows (UA-22 style wall)
    ("Wall horizontal deflection", 12.5, "mm", "", ""),
    ("Max anchor force", 410, "kN", "", ""),
    ("wall_ux", 4.1, "mm", "", 30),
    ("wall_ux", 8.7, "mm", "", 60),
    ("wall_ux", 12.5, "mm", "", 90),
    ("uv", 2.4, "mm", "Anchor + prestress", ""),
    ("Surface settlement (top of model)", 9.8, "mm", "Long term", ""),
]


class ReferenceError(ValueError):
    """Every problem found in the file, one message per line."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("The Reference Results file was not used:\n" + "\n".join(f"- {p}" for p in problems))


# --- reading the table ------------------------------------------------------------------------------------------
def read_table(data: bytes, filename: str) -> list[list]:
    """The rows of the file's one table, as lists of cells (str, float, int or None)."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _read_xlsx(data)
    if name.endswith(".xls"):
        return _read_xls(data)
    if name.endswith((".csv", ".txt")):
        return _read_csv(data)
    raise ReferenceError([f"{filename or 'The file'} is not an Excel file. Use .xlsx (recommended), .xls or .csv."])


def _read_xlsx(data: bytes) -> list[list]:
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - any broken / password-protected workbook
        raise ReferenceError([f"The Excel file could not be opened ({type(e).__name__}). Save it again as .xlsx and retry."])
    ws = wb[SHEET] if SHEET in wb.sheetnames else wb[wb.sheetnames[0]]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _read_xls(data: bytes) -> list[list]:
    try:
        import xlrd
    except ImportError:
        raise ReferenceError(["Old .xls files need the xlrd package (run run.bat once to install it), "
                              "or save the file as .xlsx in Excel and upload that."])
    try:
        wb = xlrd.open_workbook(file_contents=data)
    except Exception as e:  # noqa: BLE001
        raise ReferenceError([f"The .xls file could not be opened ({type(e).__name__}). Save it as .xlsx and retry."])
    ws = wb.sheet_by_name(SHEET) if SHEET in wb.sheet_names() else wb.sheet_by_index(0)
    return [[c.value if c.ctype not in (0, 6) else None for c in ws.row(i)] for i in range(ws.nrows)]


def _read_csv(data: bytes) -> list[list]:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [list(r) for r in csv.reader(io.StringIO(text), dialect)]


# --- checking the cells -----------------------------------------------------------------------------------------
def _blank(x) -> bool:
    return x is None or (isinstance(x, str) and not x.strip())


def _norm(x) -> str:
    """'  Max total displacement |u| ' -> 'max total displacement |u|'; header text in brackets is dropped."""
    return re.sub(r"\s+", " ", re.sub(r"\(.*?\)|\[.*?\]", "", str(x or ""))).strip().lower()


def _number(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x or "").strip().replace(" ", "").replace(" ", "")
    if re.fullmatch(r"-?\d+,\d+", s):  # 12,5 written with a decimal comma
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


_ALIAS = {"kpa": "kPa", "mpa": "MPa", "pa": "Pa", "kn": "kN", "mn": "MN", "knm": "kN·m", "kn-m": "kN·m",
          "kn.m": "kN·m", "mm": "mm", "cm": "cm", "m": "m", "ft": "ft", "in": "in", "n": "N", "kip": "kip"}


def _unit_text(u) -> str:
    """What was typed -> the app's spelling: 'kNm/m' -> 'kN·m/m', 'kN/m2' -> 'kN/m²', 'KPA' -> 'kPa'."""
    s = str(u or "").strip().replace(" ", "")
    if s in ("", "-", "1", "—"):
        return ""
    parts = []
    for i, part in enumerate(s.split("/")):
        m = re.fullmatch(r"([A-Za-z.\-·]+?)(\^?[234²³⁴])?", part)
        if not m:
            return s
        base = _ALIAS.get(m.group(1).lower(), m.group(1))
        exp = (m.group(2) or "").lstrip("^").replace("2", "²").replace("3", "³").replace("4", "⁴")
        parts.append(base + exp)
    return "/".join(parts)


def _dims(unit: str):
    """The exponents of length, force and time of a unit; () for no unit; None when it is not a unit."""
    if unit == "":
        return ()
    toks = units._tokens(unit)
    if not toks:
        return None
    e = {"length": 0, "force": 0, "time": 0}
    for _, x, k in toks:
        e[k] += x
    return (e["length"], e["force"], e["time"])


def _size(unit: str) -> float:
    """How big the unit is, in kN, m and day."""
    tables = {"length": units.LENGTH, "force": units.FORCE, "time": units.TIME}
    f = 1.0
    for base, exp, kind in units._tokens(unit) or []:
        f *= tables[kind][base] ** exp
    return f


def _kind_of(unit: str) -> str:
    d = _dims(unit)
    names = {(): "no unit (leave Unit empty)", (1, 0, 0): "a length such as mm, cm or m",
             (0, 1, 0): "a force such as kN or MN", (-1, 1, 0): "a force per metre such as kN/m",
             (-2, 1, 0): "a stress such as kPa, kN/m² or MPa"}
    if "·" in unit:  # kN·m/m has the dimensions of a force: name it as written
        return f"a moment per metre such as {unit}" if unit.endswith("/m") else f"a moment such as {unit}"
    return names.get(d, f"a unit like {unit}")


# --- the main entry ---------------------------------------------------------------------------------------------
def _header(rows: list[list]):
    """(index of the header row, {column: cell index}) or a ReferenceError naming what is missing."""
    hi = next((i for i, r in enumerate(rows) if any(not _blank(c) for c in r)), None)
    if hi is None:
        raise ReferenceError(["The file is empty. Put the header row (Result, Value, Unit, Stage, Day) on the first row."])
    cols, unknown = {}, []
    for j, c in enumerate(rows[hi]):
        if _blank(c):
            continue
        n = _norm(c)
        key = next((k for k, names in COLUMNS.items() if n in names), None)
        if key and key not in cols:
            cols[key] = j
            if key == "time":  # Time (min), Duration [hours]
                br = re.search(r"[(\[]\s*([A-Za-z]+)\s*[)\]]", str(c))
                if br:
                    cols["time_header_unit"] = br.group(1)
        elif not key and n not in ("note", "notes", "comment", "remarks", "source", "point", "location"):
            unknown.append(str(c).strip())
    miss = [k.capitalize() for k in REQUIRED if k not in cols]
    if miss:
        found = ", ".join(str(c).strip() for c in rows[hi] if not _blank(c))
        raise ReferenceError([f"Missing column{'s' if len(miss) > 1 else ''}: {', '.join(miss)}. "
                              f"The header row (row {hi + 1}) has: {found or 'nothing'}. "
                              "Required columns: Result, Value, Unit. Optional: Stage, Day, Time, Time unit, X."])
    return hi, cols, unknown


def parse(data: bytes, filename: str, m: dict, setup: dict | None = None, study: dict | None = None) -> dict:
    """Read and check the Reference Results file against the model m. Values come back in the units the study
    shows (setup["units"]), like the values typed by hand."""
    from .study import TIME_UNITS, clean_xaxis, schedule  # keep the import local (study does not import this module)
    setup = setup or {}
    rows = read_table(data, filename)
    hi, cols, unknown = _header(rows)
    warnings = [f"Column{'s' if len(unknown) > 1 else ''} {', '.join(unknown)} ignored (not a Reference Results column)."] if unknown else []
    outs = [o for o in plaxis_model.outputs(m.get("version") or "2d") if o["key"] != "file"]
    by_name = {}
    for o in outs:
        for n in (o["key"], o["label"], o["result"]):
            by_name.setdefault(_norm(n), o)
    tm = (study or {}).get("time") or {}
    sched = schedule(m, tm)
    stages = {}
    for name, (d, lab) in sched.items():
        stages.setdefault(_norm(name), (d, lab))
        stages.setdefault(_norm(lab), (d, lab))
    lt = _number(tm.get("longterm"))
    lt = 365.0 if lt is None else lt
    last = max((d for d, _ in sched.values()), default=0.0)
    plaxis_u = units.plaxis_of(setup)
    xa = clean_xaxis((study or {}).get("xaxis"))
    xvals = [(v, sched[k][0], sched[k][1]) for k, v in (xa.get("values") or {}).items() if k in sched] if xa["kind"] == "variable" else []
    head_tu = cols.pop("time_header_unit", None)
    if head_tu and TIME_ALIASES.get(head_tu.lower()) is None:
        raise ReferenceError([f'The Time column header gives the unit "{head_tu}"; use min, h or day, e.g. Time (min).'])
    default_tu = TIME_ALIASES[head_tu.lower()] if head_tu else xa["unit"]

    problems, measured, series, seen_final, seen_day, used_lt = [], [], {}, {}, {}, False
    body = [(i, r) for i, r in enumerate(rows[hi + 1:], start=hi + 2) if any(not _blank(c) for c in r)]
    if not body:
        raise ReferenceError([f"The table has a header (row {hi + 1}) but no values under it."])
    if len(body) > MAX_ROWS:
        raise ReferenceError([f"The table has {len(body)} rows; the limit is {MAX_ROWS}."])
    cell = lambda r, k: r[cols[k]] if k in cols and isinstance(cols[k], int) and cols[k] < len(r) else None
    for rn, r in body:
        res, val, unit = cell(r, "result"), cell(r, "value"), cell(r, "unit")
        stage, day, tval, tunit, xv = cell(r, "stage"), cell(r, "day"), cell(r, "time"), cell(r, "time_unit"), cell(r, "x")
        bad = []
        o = by_name.get(_norm(res)) if not _blank(res) else None
        if _blank(res):
            bad.append("Result is empty")
        elif not o:
            bad.append(f'Result "{str(res).strip()}" is not a PLAXIS result of the app '
                       f"(use one of: {', '.join(x['key'] for x in outs)})")
        v = _number(val)
        if _blank(val):
            bad.append("Value is empty")
        elif v is None:
            bad.append(f'Value "{val}" is not a number')
        u = _unit_text(unit)
        if o:
            want = units.label(o["unit"], plaxis_u)  # the result's unit in the model's units
            dw, du = _dims(want), _dims(u)
            if du is None:
                bad.append(f'Unit "{str(unit).strip()}" is not a unit the app knows; {o["label"]} needs {_kind_of(want)}')
            elif _blank(unit) and dw != ():
                bad.append(f"Unit is empty; {o['label']} needs {_kind_of(want)}")
            elif du != dw:
                bad.append(f'Unit "{str(unit).strip()}" does not fit {o["label"]}, which needs {_kind_of(want)}')
        d = _number(day) if not _blank(day) else None
        if not _blank(day) and (d is None or d < 0):
            bad.append(f'Day "{day}" is not a number of days (0 or more)')
        if d is None and not _blank(tval):  # Time in min / h / day -> days
            tv = _number(tval)
            tu = TIME_ALIASES.get(str(tunit).strip().lower()) if not _blank(tunit) else default_tu
            if tv is None or tv < 0:
                bad.append(f'Time "{tval}" is not a time (a number, 0 or more)')
            elif tu is None:
                bad.append(f'Time unit "{str(tunit).strip()}" is not min, h or day')
            else:
                d = tv * units.TIME[tu]
        x_read = None
        if d is None and not _blank(xv):  # X: the stage whose X value is nearest
            x_read = _number(xv)
            if x_read is None:
                bad.append(f'X "{xv}" is not a number')
            elif not xvals:
                bad.append("X is given but the X-axis of the study is not a variable with a value at each stage "
                           "(choose Other variable under Results over time and type its values first)")
            else:
                d = min(xvals, key=lambda e: abs(e[0] - x_read))[1]
        st = None
        if not _blank(stage) and d is None:
            n = _norm(stage)
            if n.replace(" ", "").replace("-", "") in ("longterm", LONGTERM):
                st, used_lt = (last + lt, f"Long term ({lt:g} days)"), True
            else:
                st = stages.get(n)
                if not st:
                    bad.append(f'Stage "{str(stage).strip()}" is not a stage of the model '
                               f"(use one of: {', '.join(lab for _, lab in sched.values()) or 'none'}, Long term)")
        if bad:
            problems.append(f"Row {rn}: " + "; ".join(bad) + ".")
            continue
        # the value in the model's units, then in the units the study shows (as if typed by hand)
        vp = v * (_size(u) / _size(want) if dw else 1.0)
        vd = units.to_display(vp, o["unit"], setup)[0]
        vd = float(f"{vd:.6g}")
        if d is None and st is None:
            if o["key"] in seen_final:
                problems.append(f"Row {rn}: {o['label']} already has a final value on row {seen_final[o['key']]}. "
                                "Give a Stage or a Day to make it a reading, or delete one of the rows.")
                continue
            seen_final[o["key"]] = rn
            measured.append({"output": o["key"], "value": vd, "row": rn})
        else:
            day_v = d if d is not None else st[0]
            k = (o["key"], day_v if x_read is None else ("x", x_read))
            if k in seen_day:
                problems.append(f"Row {rn}: {o['label']} already has a reading at day {day_v:g} on row {seen_day[k]}.")
                continue
            seen_day[k] = rn
            series.setdefault(o["key"], []).append({"day": float(f"{day_v:.12g}"), "value": vd, "row": rn,
                                                    **({"x": x_read} if x_read is not None else {})})
    if problems:
        raise ReferenceError(problems)
    if any(x["output"] == "fos" for x in measured) and not any(
            "safety" in f"{ph.get('type', '')} {ph.get('id', '')}".lower() for ph in m.get("phases") or []):
        warnings.append("The model has no safety phase, so a measured factor of safety cannot be compared.")
    if series and not sched:
        warnings.append("The model has no construction stages: the readings by Stage / Day / Time cannot be placed.")
    return {"measured": [{"output": x["output"], "value": x["value"]} for x in measured],
            "series": [{"output": k, "points": sorted(({kk: p[kk] for kk in ("day", "value", "x") if kk in p} for p in pts),
                                                      key=lambda p: (p["day"], p.get("x") or 0))}
                       for k, pts in series.items()],
            "rows": len(body), "warnings": warnings, "longterm": lt if used_lt else None}


def template() -> bytes:
    """An example Reference Results workbook: the table to fill in, and a Guide sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(["Result", "Value", "Unit", "Stage", "Day"])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E79")
    for r in EXAMPLE:
        ws.append([x if x != "" else None for x in r])
    for col, w in zip("ABCDE", (36, 10, 9, 16, 8)):
        ws.column_dimensions[col].width = w
    g = wb.create_sheet("Guide")
    g.append(["Column", "", "What to write"])
    for c in g[1]:
        c.font = Font(bold=True)
    for r in GUIDE:
        g.append(list(r))
    g.append([])
    g.append(["Rules"])
    for t in ("One table per file, on the sheet named 'Reference Results' (else the first sheet). The first row is the header.",
              "A row without Stage, Day, Time or X is the final value of the result (one per result).",
              "Any other row is a reading of the series, placed by the first of Day, Time, X, Stage that is filled in.",
              "Values are converted from the Unit given into the units the study shows.",
              "If any row is wrong, nothing is loaded and every wrong row is listed.",
              "Uploading a file replaces the measured values and the field readings of the study.",
              "Result keys: " + ", ".join(o["key"] for o in plaxis_model.outputs("3d") if o["key"] != "file")):
        g.append([t])
    g.column_dimensions["A"].width, g.column_dimensions["C"].width = 14, 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
