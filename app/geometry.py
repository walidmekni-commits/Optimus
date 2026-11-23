"""Cross sections for the Geometry variables: reads a drawing (DXF, CSV/TXT of coordinates, or an image/PDF with the
Claude AI), simplifies it to nodes and straight lines, and draws it in black and white with every node's coordinates
and the horizontal and vertical spacings.

Coordinates: origin (0, 0, 0) at the upper left of the section. x to the right, z = depth, positive downwards.
y is the position of the section along the model (0 unless set).
"""
import base64
import csv
import io
import math
import mimetypes
import re
from html import escape
from pathlib import Path

DXF_UNITS = {1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 7: 1000.0}  # $INSUNITS -> metres
NUM = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"


class GeometryError(Exception):
    pass


# --------------------------------------------------------------------------------------------------------------
# Readers. Each returns (polylines, y_is_depth, warnings); a polyline is (points, closed).
# --------------------------------------------------------------------------------------------------------------
def read_dxf(text: str):
    lines = text.splitlines()
    if len(lines) < 4 or "SECTION" not in text[:2000].upper():
        raise GeometryError("This doesn't look like a DXF file. Save the drawing as ASCII DXF (not binary or DWG).")
    pairs = [(lines[i].strip(), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    scale, warnings = 1.0, []
    for i, (c, v) in enumerate(pairs):
        if c == "9" and v == "$INSUNITS" and i + 1 < len(pairs):
            try:
                scale = DXF_UNITS.get(int(pairs[i + 1][1]), 1.0)
            except ValueError:
                pass
            break
    # entities section only
    start = next((i for i, (c, v) in enumerate(pairs) if c == "2" and v == "ENTITIES"), None)
    if start is None:
        raise GeometryError("The DXF has no ENTITIES section.")
    entities, cur = [], None
    for c, v in pairs[start + 1:]:
        if c == "0":
            if cur:
                entities.append(cur)
            if v == "ENDSEC":
                cur = None
                break
            cur = [v, []]
        elif cur is not None:
            cur[1].append((c, v))
    if cur:
        entities.append(cur)

    def f(v):
        return float(v) * scale

    polylines, skipped, poly = [], {}, None
    for kind, data in entities:
        if kind == "LINE":
            d = dict(data)
            polylines.append(([(f(d["10"]), f(d["20"])), (f(d["11"]), f(d["21"]))], False))
        elif kind == "LWPOLYLINE":
            pts, x = [], None
            closed = any(c == "70" and int(v) & 1 for c, v in data)
            for c, v in data:
                if c == "10":
                    x = f(v)
                elif c == "20" and x is not None:
                    pts.append((x, f(v)))
                    x = None
            if len(pts) > 1:
                polylines.append((pts, closed))
        elif kind == "POLYLINE":
            poly = ([], any(c == "70" and int(v) & 1 for c, v in data))
        elif kind == "VERTEX" and poly is not None:
            d = dict(data)
            poly[0].append((f(d.get("10", 0)), f(d.get("20", 0))))
        elif kind == "SEQEND" and poly is not None:
            if len(poly[0]) > 1:
                polylines.append(poly)
            poly = None
        elif kind not in ("TEXT", "MTEXT", "DIMENSION", "HATCH", "POINT", "VIEWPORT", "ATTRIB", "ATTDEF"):
            skipped[kind] = skipped.get(kind, 0) + 1
    if skipped:
        warnings.append("Not drawn (only straight lines and polylines are used): "
                        + ", ".join(f"{n} {k}" for k, n in skipped.items()) + ".")
    return polylines, False, warnings


def read_table(text: str):
    """CSV/TXT of points. One point per row: x, level  (or columns named x and z / y / level / depth, plus an optional
    line / id / polyline column). A blank row starts a new polyline. A column called depth is read as positive
    downwards; any other vertical column as a level (positive upwards)."""
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:2000], ",;\t ")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    header, y_is_depth = None, False
    first = [c.strip().lower() for c in (rows[0] if rows else []) if c.strip()]
    if first and not all(re.fullmatch(NUM, c) for c in first):
        header = first
        rows = rows[1:]
    ix = iz = il = None
    if header:
        for i, h in enumerate(header):
            if h in ("x", "x (m)", "x[m]", "chainage", "offset", "distance"):
                ix = i
            elif h.startswith(("depth", "d ")) or h == "d":
                iz, y_is_depth = i, True
            elif h.startswith(("z", "y", "level", "elevation", "rl")) and iz is None:
                iz = i
            elif h.startswith(("line", "id", "poly", "object", "name")):
                il = i
    polylines, cur, cur_id = [], [], None
    for r in rows:
        cells = [c.strip() for c in r if c.strip() != ""]
        if not cells:
            if len(cur) > 1:
                polylines.append((cur, False))
            cur = []
            continue
        if header and ix is not None and iz is not None:
            vals = [c.strip() for c in r]
            try:
                x, z = float(vals[ix]), float(vals[iz])
            except (ValueError, IndexError):
                continue
            lid = vals[il] if il is not None and il < len(vals) else None
        else:
            nums = [float(c) for c in cells if re.fullmatch(NUM, c)]
            if len(nums) < 2:
                if len(cur) > 1:  # a text row like "Wall" separates polylines
                    polylines.append((cur, False))
                cur = []
                continue
            if len(nums) == 2:
                (x, z), lid = nums, None
            else:
                lid, x, z = nums[0], nums[1], nums[2]
        if lid is not None and lid != cur_id and cur:
            if len(cur) > 1:
                polylines.append((cur, False))
            cur = []
        cur_id = lid
        cur.append((x, z))
    if len(cur) > 1:
        polylines.append((cur, False))
    if not polylines:
        raise GeometryError("No coordinates found. Use one point per row (x, level), with a blank row between lines.")
    return polylines, y_is_depth, []


def read_with_ai(path: Path):
    """Images and PDFs: the Claude AI traces the cross section (needs the API key in Settings)."""
    from typing import Literal, Optional

    from pydantic import BaseModel

    from .config import DRAWING_MODEL, ai_mode, api_key
    if ai_mode() != "claude":
        raise GeometryError(f"{path.name}: reading drawings from images or PDFs needs the Claude AI (API key in "
                            "Settings). Offline, upload the cross section as DXF, or as CSV with one point per row "
                            "(x, level) and a blank row between lines.")

    class Pt(BaseModel):
        x: float
        depth: float

    class Line(BaseModel):
        label: Optional[str]
        kind: Literal["soil", "structure"]
        points: list[Pt]
        closed: bool

    class Trace(BaseModel):
        lines: list[Line]
        note: str

    import anthropic
    mime = mimetypes.guess_type(path.name)[0] or ""
    data = base64.b64encode(path.read_bytes()).decode()
    block = ({"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
             if mime == "application/pdf" else
             {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
    prompt = ("This is a geotechnical cross section (e.g. retaining wall, excavation, soil layers). Trace it as "
              "straight lines in real dimensions, in metres, using the dimensions and levels written on the drawing. "
              "Coordinates: x from the left edge of the section, depth from the top of the section, positive "
              "downwards. Include the soil layer boundaries, ground surface, excavation, walls, slabs, anchors and "
              "the model boundary. For each line set kind: \"soil\" for the soil boxes (model boundary, ground surface, "
              "soil layer boundaries, excavation and berm outlines, water table) or \"structure\" for structural elements (walls, "
              "piles, anchors, struts, slabs, beams, footings, tunnel linings). Ignore text, hatching and dimension lines. "
              "In note, say anything you had to guess.")
    resp = anthropic.Anthropic(api_key=api_key()).beta.messages.parse(
        model=DRAWING_MODEL, max_tokens=32000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": [block, {"type": "text", "text": prompt}]}], output_format=Trace)
    t = resp.parsed_output
    if resp.stop_reason == "refusal" or t is None or not t.lines:
        raise GeometryError(f"{path.name}: the AI could not trace a cross section from this file.")
    polys = [([(p.x, p.depth) for p in ln.points], ln.closed, ln.kind) for ln in t.lines if len(ln.points) > 1]
    return polys, True, [t.note] if t.note else []


# CAD layers that are usually not geometry (dimensions, text, hatching, frames): off by default, can be ticked on.
OFF_LAYERS = re.compile(r"(dim|text|txt|anno|hatch|title|border|frame|defpoints|viewport|vport|grid|legend|note|"
                        r"symbol|logo|revision|tb_|sheet|cota|texte)", re.I)
PT = 0.0254 / 72  # metres per PDF point on paper


def _pdf_pages_fitz(path: Path):
    """PyMuPDF: fast (C library). Yields (box, raw lines, texts) per page."""
    import fitz
    doc = fitz.open(str(path))  # PyMuPDF repairs damaged files while opening
    if doc.needs_pass and not doc.authenticate(""):
        raise GeometryError(f"{path.name} is protected with a password. Open it in a PDF reader and print it to a new "
                            "PDF (Microsoft Print to PDF), then upload that.")
    for page in doc:
        H = page.rect.height
        raw = []
        for d in page.get_drawings():
            lw = float(d.get("width") or 0)
            dashed = bool(d.get("dashes")) and d.get("dashes") not in ("[] 0", "[]0")
            group = f"line weight {lw:.2f}" + (" dashed" if dashed else "")
            if d.get("color") is None:  # filled only: wide polylines from CAD, solid hatching, arrow heads
                group = "filled shapes"
            cur = []
            for it in d["items"]:
                kind = it[0]
                if kind == "l":
                    p, q = (it[1].x, H - it[1].y), (it[2].x, H - it[2].y)
                    if cur and math.dist(cur[-1], p) < 1e-6:
                        cur.append(q)
                    else:
                        if len(cur) > 1:
                            raw.append([[list(t) for t in cur], False, group])
                        cur = [p, q]
                elif kind == "c":  # bezier: straight to its end point
                    p, q = (it[1].x, H - it[1].y), (it[4].x, H - it[4].y)
                    if cur and math.dist(cur[-1], p) < 1e-6:
                        cur.append(q)
                    else:
                        if len(cur) > 1:
                            raw.append([[list(t) for t in cur], False, group])
                        cur = [p, q]
                elif kind == "re":
                    r = it[1]
                    raw.append([[[r.x0, H - r.y0], [r.x1, H - r.y0], [r.x1, H - r.y1], [r.x0, H - r.y1], [r.x0, H - r.y0]],
                                True, group])
                elif kind == "qu":
                    q = it[1]
                    pts = [q.ul, q.ur, q.lr, q.ll, q.ul]
                    raw.append([[[t.x, H - t.y] for t in pts], True, group])
            if len(cur) > 1:
                raw.append([[list(t) for t in cur], bool(d.get("closePath")), group])
        words = page.get_text("words")
        texts = [((w[0] + w[2]) / 2, H - (w[1] + w[3]) / 2, w[4]) for w in words]
        # join neighbouring words of a line so "CH 0+045" and "SECTION A-A" stay together (but not two labels
        # that only share a baseline across the sheet)
        runs, cur = [], []
        for w in sorted(words, key=lambda w: (w[5], w[6], w[0])):
            if cur and (w[5], w[6]) == (cur[-1][5], cur[-1][6]) and w[0] - cur[-1][2] < 1.5 * (w[3] - w[1]):
                cur.append(w)
            else:
                if cur:
                    runs.append(cur)
                cur = [w]
        if cur:
            runs.append(cur)
        lines = {i: ((r[0][0] + r[-1][2]) / 2, H - (r[0][1] + r[0][3]) / 2, " ".join(w[4] for w in r))
                 for i, r in enumerate(runs) if len(r) > 1}
        raw = [_centreline(r) if r[2] == "filled shapes" else r for r in raw]
        area = max(page.rect.width * H, 1)
        try:
            pic = sum(abs(fitz.Rect(i["bbox"]) & page.rect) for i in page.get_image_info()) / area
        except Exception:  # noqa: BLE001
            pic = 0.0
        yield (0, 0, page.rect.width, H), raw, texts + list(lines.values()), {"picture": pic, "page": page.number}


def _centreline(r):
    """A long thin filled shape (a CAD polyline with a width, exported filled) becomes its centre line."""
    pts = r[0]
    if pts and pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    if len(pts) != 5:
        return r
    sides = [math.dist(pts[i], pts[i + 1]) for i in range(4)]
    short, long_ = min(sides), max(sides)
    if long_ == 0 or short > 0.25 * long_ or short > 6:  # a real area (solid hatch, block), not a wide line
        return r
    i = sides.index(short)
    a = [(pts[i][k] + pts[i + 1][k]) / 2 for k in (0, 1)]
    j = (i + 2) % 4
    b = [(pts[j][k] + pts[j + 1][k]) / 2 for k in (0, 1)]
    return [[a, b], False, f"filled lines {short:.2f} wide"]


def _pdf_pages_miner(path: Path):
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTCurve, LTFigure, LTTextContainer
    for page in extract_pages(str(path)):
        raw, texts = [], []

        def walk(obj):
            for o in obj:
                if isinstance(o, LTFigure):
                    walk(o)
                elif isinstance(o, LTTextContainer):
                    texts.append(((o.x0 + o.x1) / 2, (o.y0 + o.y1) / 2, " ".join(o.get_text().split())))
                elif isinstance(o, LTCurve):  # LTLine and LTRect are LTCurves too
                    if not getattr(o, "stroke", True):
                        continue
                    lw = float(getattr(o, "linewidth", 0) or 0)
                    dash = getattr(o, "dashing_style", None)
                    group = f"line weight {lw:.2f}" + (" dashed" if dash and dash[0] else "")
                    for pts, closed in _pdf_paths(o):
                        if len(pts) > 1:
                            raw.append([[list(p) for p in pts], closed, group])
        walk(page)
        yield (page.x0, page.y0, page.x1, page.y1), raw, texts, {"picture": 0.0, "page": page.pageid - 1}


def read_pdf(path: Path, progress=None) -> list[dict]:
    """Vector PDF drawings (exported from AutoCAD, Revit, PLAXIS ...): reads the drawn lines of every page, offline.
    Each separate drawing on a sheet becomes its own cross section, named and placed by the title ("SECTION A-A")
    and chainage ("CH 0+045") written next to it. Letters drawn as strokes, symbols, frame, title block, borehole
    logs and dimension strings are left out. Scanned PDFs (pictures) have no lines to read."""
    pages, errs = None, []
    for reader in (_pdf_pages_fitz, _pdf_pages_miner):
        try:
            pages = list(reader(path))
            break
        except GeometryError:
            raise
        except ImportError:
            continue
        except Exception as e:  # noqa: BLE001  damaged or unusual file: try the next reader
            errs.append(f"{type(e).__name__}: {e}")
    if pages is None:
        if errs:
            return read_picture(path, progress)  # still try to see it as a picture
        raise GeometryError("Reading PDFs needs PyMuPDF or pdfminer.six: run  python -m pip install -r "
                            "requirements.txt  (run.bat does this).")
    drafts, as_picture = [], []
    for pno, (box, raw, texts, info) in enumerate(pages, 1):
        if progress:
            progress(f"Page {pno}: {len(raw)} lines found, finding the cross sections…")
        alltext = " ".join(t[2] for t in texts)
        m = re.findall(r"1\s*:\s*(\d{2,4})\b", alltext)
        scale = float(max(set(m), key=m.count)) if m else 100.0
        strokes = [r for r in raw if not r[2].startswith("filled")]
        if len(strokes) < 40 and len(raw) > len(strokes):
            strokes = raw  # drawing exported as filled shapes (wide polylines)
        if info.get("picture", 0) > 0.5 or not strokes:
            as_picture.append(pno)  # a scanned / flattened sheet: the drawing is a picture on the page
            continue
        off = sorted(_hatch_groups(strokes) | ({"filled shapes"} if strokes is not raw else set()) |
                     ({g for _p, _c, g in raw if g.startswith("filled")} if strokes is not raw else set()))
        parts = cluster(strokes, box)
        diag_m = max((math.hypot(b[2] - b[0], b[3] - b[1]) for _p, b in parts), default=0) * PT * scale
        if diag_m < 4 and _has_picture_content(path, pno):
            as_picture.append(pno)  # only scraps of vector lines (stamps, notes): the drawing is a picture
            continue
        labels = label_parts(parts, texts, box)
        for i, ((part, b), (name, ch)) in enumerate(zip(parts, labels), 1):
            drafts.append({"name": name or (f"{path.stem} p{pno}" + (f"-{i}" if len(parts) > 1 else "")),
                           "kind": "pdf", "raw": part, "off": off, "y_is_depth": False, "scale": scale, "unit": PT * scale,
                           "page": pno,
                           "chainage": ch, "warnings": [] if m else ["No drawing scale found on the sheet, so 1:100 "
                                                                     "is assumed. Set the right scale below."]})
    if as_picture:
        try:
            drafts += read_picture(path, progress, pages=as_picture)
        except GeometryError:
            if not drafts:
                raise
    if not drafts:
        raise GeometryError(f"{path.name}: the file opened, but no drawing could be found on its pages.")
    return drafts


def _has_picture_content(path: Path, pno: int) -> bool:
    """True when the rendered page has much more ink than its vector lines explain (a drawing kept as an image)."""
    try:
        import fitz
        page = fitz.open(str(path))[pno - 1]
        pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False)
        ink = sum(1 for v in pix.samples[::7] if v < 160) / max(1, len(pix.samples[::7]))
        return ink > 0.01 or bool(page.get_images())
    except Exception:  # noqa: BLE001
        return False


def _pdf_paths(o):
    from pdfminer.layout import LTRect
    path = getattr(o, "original_path", None)
    if not path:
        return [([tuple(p) for p in o.pts], isinstance(o, LTRect))]
    out, cur, start = [], [], None
    for seg in path:
        op = seg[0]
        if op == "m":
            if len(cur) > 1:
                out.append((cur, False))
            cur = [tuple(seg[1])]
            start = cur[0]
        elif op in ("l", "c", "v", "y"):
            cur.append(tuple(seg[-1]))  # curves become a straight line to their end point (simplified)
        elif op == "h":
            if start and cur and cur[-1] != start:
                cur.append(start)
            if len(cur) > 1:
                out.append((cur, True))
            cur = [start] if start else []
        elif op == "re" and len(seg) >= 2:  # some versions keep rectangles as one op
            pass
    if len(cur) > 1:
        out.append((cur, False))
    return out


def _find(patterns: list[str]) -> str | None:
    import glob
    import shutil
    for pat in patterns:
        if "*" in pat or "/" in pat or "\\" in pat:
            hits = sorted(glob.glob(pat), reverse=True)  # newest version first
            if hits:
                return hits[0]
        elif shutil.which(pat):
            return shutil.which(pat)
    return None


def _has_ezdwg() -> bool:
    import importlib.util
    return importlib.util.find_spec("ezdwg") is not None


def dwg_converter() -> str | None:
    """Which DWG reader this PC has (None: DWG can't be read here; DXF and CSV always can, in pure Python)."""
    import os
    if _has_ezdwg():
        return "the built-in DWG reader (ezdwg)"
    pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    for name, pats in (
            ("ODA File Converter", ["ODAFileConverter"] + [f"{d}\\ODA\\ODAFileConverter*\\ODAFileConverter.exe" for d in pf if d]
             + ["/usr/bin/ODAFileConverter", "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"]),
            ("AutoCAD", [f"{d}\\Autodesk\\AutoCAD*\\accoreconsole.exe" for d in pf if d] + ["accoreconsole"]),
            ("BricsCAD", [f"{d}\\Bricsys\\BricsCAD*\\bricscad.exe" for d in pf if d]),
            ("LibreDWG", ["dwg2dxf"])):
        if _find(pats):
            return name
    return None


def dwg_to_dxf(path: Path) -> Path:
    """DWG is a closed format: it is converted to DXF with ezdwg (pip, no install needed), or else with whatever is on
    this PC, in this order: the free ODA File Converter, AutoCAD's own console (accoreconsole, no window), BricsCAD, or LibreDWG (dwg2dxf)."""
    import os
    import subprocess
    import tempfile
    out_dir = Path(tempfile.mkdtemp(prefix="dwg_"))
    out = out_dir / (path.stem + ".dxf")
    pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    tried = []
    try:  # 1. ezdwg: DWG reader installed with pip (requirements.txt), no CAD or converter on the PC needed
        if not _has_ezdwg():
            raise ImportError
        import ezdwg
        tried.append("ezdwg")
        ezdwg.to_dxf(str(path), str(out))
        if out.exists() and out.stat().st_size > 0:
            return out
    except ImportError:
        pass
    except Exception:  # noqa: BLE001  a DWG ezdwg can't decode: try the converters below
        pass
    oda = _find(["ODAFileConverter"] + [f"{d}\\ODA\\ODAFileConverter*\\ODAFileConverter.exe" for d in pf if d] +
                ["/usr/bin/ODAFileConverter", "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"])
    if oda:
        tried.append("ODA File Converter")
        try:
            src = Path(tempfile.mkdtemp(prefix="dwgin_"))
            (src / path.name).write_bytes(path.read_bytes())
            subprocess.run([oda, str(src), str(out_dir), "ACAD2013", "DXF", "0", "1", path.name],
                           timeout=300, capture_output=True)
            if out.exists():
                return out
        except Exception:  # noqa: BLE001
            pass
    acad = _find([f"{d}\\Autodesk\\AutoCAD*\\accoreconsole.exe" for d in pf if d] + ["accoreconsole"])
    if acad:
        tried.append("AutoCAD (accoreconsole)")
        scr = out_dir / "to_dxf.scr"
        scr.write_text(f'FILEDIA\n0\n_.DXFOUT\n"{out}"\n16\n_.QUIT\n_Y\n')
        try:
            subprocess.run([acad, "/i", str(path), "/s", str(scr), "/l", "en-US"], timeout=300, capture_output=True)
            if out.exists():
                return out
        except Exception:  # noqa: BLE001
            pass
    brics = _find([f"{d}\\Bricsys\\BricsCAD*\\bricscad.exe" for d in pf if d])
    if brics:
        tried.append("BricsCAD")
        scr = out_dir / "to_dxf.scr"
        scr.write_text(f'FILEDIA\n0\n_.DXFOUT\n"{out}"\n16\n_.QUIT\n_Y\n')
        try:
            subprocess.run([brics, "/b", str(scr), str(path)], timeout=300, capture_output=True)
            if out.exists():
                return out
        except Exception:  # noqa: BLE001
            pass
    lib = _find(["dwg2dxf"])
    if lib:
        tried.append("LibreDWG")
        try:
            subprocess.run([lib, "-y", "-o", str(out), str(path)], timeout=300, capture_output=True)
            if out.exists():
                return out
        except Exception:  # noqa: BLE001
            pass
    how = ("Tried: " + ", ".join(tried) + ", but none could convert it. ") if tried else ""
    raise GeometryError(f"{path.name}: DWG is AutoCAD's closed format and can't be read without a converter on this "
                        f"PC. {how}Start the app with run.bat (or run  python -m pip install -r requirements.txt) to add "
                        "the built-in DWG reader, then upload again. Or ask whoever made the drawing for a DXF copy "
                        "(every CAD program saves DXF), which the app reads with nothing installed. Or install the free ODA File Converter "
                        "(opendesign.com › Guest files › ODA File Converter; no CAD licence needed) and upload again: "
                        "the app finds it by itself.")


def read_cad(path: Path) -> list[dict]:
    """DXF (any version, ASCII or binary) with ezdxf: blocks are exploded, arcs/circles/splines become short straight
    pieces, and lines keep their layer so layers can be switched on and off. DWG goes through the free ODA File
    Converter when it is installed."""
    try:
        from ezdxf import disassemble, recover
        from ezdxf import path as ezpath
    except ImportError:
        if path.suffix.lower() == ".dwg":
            raise GeometryError("Reading DWG needs the ezdxf package (python -m pip install -r requirements.txt).")
        polys, depth, warn = read_dxf(path.read_text(errors="replace"))
        return [{"name": path.stem, "kind": "dxf", "raw": [[p, c, "0"] for p, c in polys], "y_is_depth": depth,
                 "unit": 1.0, "warnings": warn}]
    if path.suffix.lower() == ".dwg":
        import shutil
        dxf = dwg_to_dxf(path)
        try:
            doc = recover.readfile(str(dxf))[0]
        finally:
            shutil.rmtree(dxf.parent, ignore_errors=True)  # the converted copy can be tens of MB
    else:
        try:
            doc, _auditor = recover.readfile(str(path))
        except Exception as e:  # noqa: BLE001  very old / hand-written DXF: the simple reader
            try:
                polys, depth, warn = read_dxf(path.read_text(errors="replace"))
            except GeometryError:
                raise GeometryError(f"{path.name}: not a readable DXF ({e}).") from e
            return [{"name": path.stem, "kind": "dxf", "raw": [[[list(q) for q in p], c, "0"] for p, c in polys],
                     "y_is_depth": depth, "unit": 1.0, "warnings": warn}]
    unit = DXF_UNITS.get(doc.header.get("$INSUNITS", 0))
    raw, skipped, texts = [], {}, []
    spaces = [doc.modelspace()] + [lay for lay in doc.layouts if lay.name != "Model"]
    space = next((sp for sp in spaces if len(sp)), doc.modelspace())
    for e in disassemble.recursive_decompose(space):
        kind = e.dxftype()
        if kind in ("TEXT", "MTEXT"):
            try:
                ins = e.dxf.insert
                txt = e.plain_text() if kind == "MTEXT" else e.dxf.text
                texts.append((ins.x, ins.y, " ".join(str(txt).split())))
            except Exception:  # noqa: BLE001
                pass
            continue
        if kind in ("TEXT", "MTEXT", "DIMENSION", "ARC_DIMENSION", "HATCH", "ATTRIB", "ATTDEF", "POINT", "VIEWPORT",
                    "IMAGE", "WIPEOUT", "LEADER", "MULTILEADER", "MLEADER", "SOLID", "TRACE", "3DSOLID", "REGION",
                    "MESH", "INSERT"):
            continue
        try:
            p = ezpath.make_path(e)
        except Exception:  # noqa: BLE001
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        subs = list(p.sub_paths()) if p.has_sub_paths else [p]
        for sp in subs:
            if len(sp) == 0 and sp.start == sp.end:
                continue
            pts = [[v.x, v.y] for v in sp.flattening(distance=0.005 / (unit or 1.0))]
            if len(pts) > 1:
                raw.append([pts, False, e.dxf.get("layer", "0")])
    if not raw:
        raise GeometryError(f"{path.name}: the drawing is empty (no lines in the model or paper space).")
    warnings = []
    if skipped:
        warnings.append("Not drawn: " + ", ".join(f"{n} {k}" for k, n in skipped.items()) + ".")
    xs, ys = [p[0] for r in raw for p in r[0]], [p[1] for r in raw for p in r[0]]
    raw, far = _trim_outliers(raw)
    if far:
        warnings.append(f"Left out {far} line(s) drawn far away from the rest of the drawing.")
        xs, ys = [p[0] for r in raw for p in r[0]], [p[1] for r in raw for p in r[0]]
    box = (min(xs), min(ys), max(xs), max(ys))
    off = sorted(_hatch_groups(raw))
    regions = _regions(raw, box)
    parts = [pt for reg in regions for pt in cluster(reg, _box(reg))] if len(regions) > 1 else cluster(raw, box)
    parts = _split_sheets(parts)
    parts, notes = _keep_sections(parts)
    warnings += notes
    unit, note = _units(unit, parts)
    if note:
        warnings.append(note)
    labels = label_parts(parts, texts, box)
    return [{"name": name or (path.stem + (f" {i}" if len(parts) > 1 else "")), "kind": "dxf", "raw": part, "off": off,
             "y_is_depth": False, "unit": unit, "chainage": ch, "warnings": list(warnings)}
            for i, ((part, _b), (name, ch)) in enumerate(zip(parts, labels), 1)]


def _median(v: list):
    v = sorted(v)
    return v[len(v) // 2] if v else 0


def _busy(parts: list) -> list:
    """Parts far busier than the typical one (10 times the median number of lines, and over 500): a site plan or
    layout drawn next to the sections. Relative to this drawing, so a detailed file keeps its detailed sections."""
    if len(parts) < 2:
        return []
    med = _median([len(p[0]) for p in parts])
    return [p for p in parts if len(p[0]) > max(10 * med, 500)]


def _keep_sections(parts: list) -> tuple[list, list[str]]:
    """Leaves out what is clearly not a cross section, judged against the other drawings in the same file:
    plans / layouts (far busier than the rest) and long thin strips (longitudinal profiles, title bars, borders: much
    flatter than the typical drawing). Nothing is left out when the file holds a single drawing."""
    notes = []
    busy = _busy(parts)
    if busy and len(busy) < len(parts):
        ids = {id(p) for p in busy}
        parts = [p for p in parts if id(p) not in ids]
        notes.append(f"Left out {len(busy)} very busy drawing(s) ({', '.join(str(len(p[0])) for p in busy)} lines), "
                     "probably a plan or site layout rather than a cross section.")
    if len(parts) > 2:
        asp = [(b[2] - b[0]) / max(b[3] - b[1], 1e-9) for _p, b in parts]
        lim = max(4.0, 2.5 * _median(asp))
        strips = [p for p, a in zip(parts, asp) if a > lim]
        if strips and len(strips) < len(parts) / 2:
            ids = {id(p) for p in strips}
            parts = [p for p in parts if id(p) not in ids]
            notes.append(f"Left out {len(strips)} long, thin drawing(s) (over {lim:.0f} times wider than deep, unlike the "
                         "others): profiles, title bars or borders rather than cross sections.")
    return parts, notes


UNIT_NAMES = {1.0: "metres", 0.001: "millimetres", 0.01: "centimetres", 0.0254: "inches", 0.3048: "feet",
              1000.0: "kilometres"}


def _units(declared: float | None, parts: list) -> tuple[float, str]:
    """Drawing units in metres. The units saved in the file are used when they give sensible section sizes (typical
    width 1 m to 5 km); when they are missing or clearly wrong, the units are guessed from the size of the sections
    (metres, then millimetres, then centimetres: the first that gives a typical width of 3 to 1000 m)."""
    w = _median([b[2] - b[0] for _p, b in parts]) or 1.0
    if declared and 1.0 <= w * declared <= 5000:
        return declared, ""
    guess = next((u for u in (1.0, 0.001, 0.01) if 3 <= w * u <= 1000), 1.0)
    why = ("doesn't say its units" if not declared else
           f"says {UNIT_NAMES.get(declared, 'its units are')} but the sections would then be {w * declared:.3g} m wide")
    return guess, (f"The drawing {why}; {UNIT_NAMES[guess]} assumed (typical section {w * guess:.3g} m wide). "
                   "Change the units below if that's wrong.")


def _trim_outliers(raw: list) -> tuple[list, int]:
    """Big CAD files often hold a few stray objects kilometres away (old xrefs, a block at 0,0). They make the
    sections look tiny next to the whole extent, so lines well outside the main body of the drawing are left out."""
    if len(raw) < 200:
        return raw, 0
    cx = sorted((min(p[0] for p in r[0]) + max(p[0] for p in r[0])) / 2 for r in raw)
    cy = sorted((min(p[1] for p in r[0]) + max(p[1] for p in r[0])) / 2 for r in raw)
    n = len(raw)
    x0, x1, y0, y1 = cx[n // 100], cx[n - 1 - n // 100], cy[n // 100], cy[n - 1 - n // 100]
    m = max(x1 - x0, y1 - y0, 1e-6) * 0.5  # generous: only lines clearly apart from the drawing go
    keep = [r for r in raw if all(x0 - m <= p[0] <= x1 + m and y0 - m <= p[1] <= y1 + m for p in r[0])]
    return (keep, n - len(keep)) if keep else (raw, 0)


def _box(raw: list):
    xs, ys = [p[0] for r in raw for p in r[0]], [p[1] for r in raw for p in r[0]]
    return (min(xs), min(ys), max(xs), max(ys))


def _regions(raw: list, box, cells: int = 200) -> list[list]:
    """Groups of lines with clear empty space between them (a coarse grid, 1/200 of the drawing per cell). A CAD file
    that holds a site plan and a row of section sheets gives one region per sheet, so each sheet is read at its own
    size instead of looking like a speck next to the plan. Regions with very few lines compared with the others (under
    8, or under a tenth of the typical region) are notes, north arrows or symbols and are left out. One region (the usual single sheet) means: read the drawing as a whole."""
    if len(raw) < 200:
        return [raw]
    x0, y0, x1, y1 = box
    c = max(x1 - x0, y1 - y0, 1e-9) / cells
    cell_of = {}
    for i, (pts, _c, _g) in enumerate(raw):
        ks = set()
        for a, b in zip(pts, pts[1:] or pts):
            n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / c) + 1)
            for t in range(n + 1):
                ks.add((int((a[0] + (b[0] - a[0]) * t / n - x0) / c), int((a[1] + (b[1] - a[1]) * t / n - y0) / c)))
        cell_of[i] = ks
    owner, parent = {}, list(range(len(raw)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, ks in cell_of.items():
        for k in ks:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    j = owner.get((k[0] + dx, k[1] + dy))
                    if j is not None:
                        parent[find(i)] = find(j)
            owner.setdefault(k, i)
    groups = {}
    for i in range(len(raw)):
        groups.setdefault(find(i), []).append(raw[i])
    med = _median([len(g) for g in groups.values() if len(g) >= 8])
    big = [g for g in groups.values() if len(g) >= max(8, 0.1 * med)]
    return big if len(big) > 1 else [raw]


def _split_sheets(parts: list) -> list:
    """A drawing sheet in its own frame often holds two or more sections: each part is looked at again on its own
    scale, and split when it is a framed sheet holding several drawings (the frame and title block are dropped)."""
    if len(parts) < 2:
        return parts
    busy, out = {id(p) for p in _busy(parts)}, []
    for pt in parts:
        part, b = pt
        if id(pt) in busy:
            out.append(pt)
            continue
        sub = cluster(part, b)
        framed = sum(len(x[0]) for x in sub) < len(part)  # its frame / title block was taken off
        out.extend(sub if len(sub) > 1 and framed else [pt])
    return out


def cluster(raw: list, box) -> list[list]:
    """Splits a sheet into separate drawings: lines that touch form a group; the sheet frame (and anything joined to
    it, such as the title block) is dropped; groups whose outlines overlap or nearly touch become one drawing."""
    x0, y0, x1, y1 = box
    W, H = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    diag = math.hypot(W, H)
    tol = diag * 1e-4
    parent = list(range(len(raw)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = {}
    for i, (pts, _c, _g) in enumerate(raw):
        for p in pts:
            k = (round(p[0] / tol), round(p[1] / tol))
            if k in seen:
                parent[find(i)] = find(seen[k])
            else:
                seen[k] = i
    comps = {}
    for i in range(len(raw)):
        comps.setdefault(find(i), []).append(i)

    def bbox(ids):
        ps = [p for i in ids for p in raw[i][0]]
        return (min(p[0] for p in ps), min(p[1] for p in ps), max(p[0] for p in ps), max(p[1] for p in ps))

    allb = [(bbox(ids), ids) for ids in comps.values()]

    def inside(a, f):  # a lies clearly within f, away from its edges
        m = 0.01 * diag
        return a[0] > f[0] + m and a[2] < f[2] - m and a[1] > f[1] + m and a[3] < f[3] - m and \
            math.hypot(a[2] - a[0], a[3] - a[1]) > 0.04 * diag
    def frame_like(b, ids):  # most of its length runs along its own outline: a border, not a drawn section
        on = tot = 0.0
        e = 0.005 * math.hypot(b[2] - b[0], b[3] - b[1])
        for i in ids:
            for p, q in zip(raw[i][0], raw[i][0][1:]):
                ln = math.hypot(q[0] - p[0], q[1] - p[1])
                tot += ln
                mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
                if min(abs(mx - b[0]), abs(mx - b[2]), abs(my - b[1]), abs(my - b[3])) < e:
                    on += ln
        return tot > 0 and on >= 0.6 * tot

    def touched(b, ids):  # lines inside end on its outline: a soil box with its layers, not a frame round a drawing
        e = 0.005 * math.hypot(b[2] - b[0], b[3] - b[1])
        own, levels = set(ids), set()
        for o, oids in allb:
            if oids is ids or not (o[0] >= b[0] - e and o[2] <= b[2] + e and o[1] >= b[1] - e and o[3] <= b[3] + e):
                continue
            for i in oids:
                if i in own:
                    continue
                pts = raw[i][0]
                p, q = pts[0], pts[-1]
                W_, H_ = max(b[2] - b[0], 1e-9), max(b[3] - b[1], 1e-9)
                # a layer line: long, crossing the middle of the box, and ending on its left or right side
                # (title-block rules and borders near the edges don't count)
                horiz = abs(q[0] - p[0]) >= 0.3 * W_ and 0.15 < ((p[1] + q[1]) / 2 - b[1]) / H_ < 0.85
                ends = sum(1 for r in (p, q) if abs(r[0] - b[0]) < e or abs(r[0] - b[2]) < e)
                if horiz and ends:
                    levels.add(round(((p[1] + q[1]) / 2 - b[1]) / H_ * 50))
        return len(levels) >= 2  # layers at two or more levels (one level is a sheet divider between two views)

    boxes = []
    for b, ids in allb:
        if len(comps) > 1 and (b[2] - b[0]) > 0.85 * W and (b[3] - b[1]) > 0.85 * H and \
                any(inside(o, b) for o, _ in allb) and frame_like(b, ids) and not touched(b, ids):
            continue  # sheet frame: the drawings sit inside it
        if math.hypot(b[2] - b[0], b[3] - b[1]) < 0.02 * diag:
            continue  # letters drawn as strokes (CAD fonts), symbols, arrow heads, hatch dashes
        boxes.append([b, ids])
    boxes.sort(key=lambda g: -((g[0][2] - g[0][0]) * (g[0][3] - g[0][1])))
    pad = diag * 0.006

    def near(a, b):
        return b[0] - pad <= a[2] and a[0] - pad <= b[2] and b[1] - pad <= a[3] and a[1] - pad <= b[3]

    merged = []
    for b, ids in boxes:
        hit = next((m for m in merged if near(b, m[0])), None)
        if hit:
            mb = hit[0]
            hit[0] = (min(b[0], mb[0]), min(b[1], mb[1]), max(b[2], mb[2]), max(b[3], mb[3]))
            hit[1] += ids
        else:
            merged.append([b, list(ids)])
    changed = True  # joining can make outlines overlap again: repeat until stable
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                a, b = merged[i][0], merged[j][0]
                if near(a, b):
                    merged[i] = [(min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])),
                                 merged[i][1] + merged[j][1]]
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    def size(m):
        return math.hypot(m[0][2] - m[0][0], m[0][3] - m[0][1])

    def flat(m):  # a dimension string or an underline: long and very thin
        w, h = m[0][2] - m[0][0], m[0][3] - m[0][1]
        return min(w, h) < 0.06 * max(w, h)

    def area(m):
        return (m[0][2] - m[0][0]) * (m[0][3] - m[0][1])

    big = max((area(m) for m in merged if not flat(m)), default=0)
    # a cross section is a sizeable drawing: borehole logs, legends, north arrows and title blocks are much smaller
    keep = [m for m in merged if len(m[1]) >= 3 and size(m) >= 0.04 * diag and not flat(m) and area(m) >= 0.15 * big]
    keep = keep or merged[:1]
    keep.sort(key=lambda m: (-round(m[0][3] / (H * 0.1)), m[0][0]))  # top to bottom, then left to right
    return [([raw[i] for i in sorted(m[1])], m[0]) for m in keep]


CHAINAGE = re.compile(r"\b(?:CH(?:AINAGE)?|STA(?:TION)?|KM|PK)\.?\s*[:=]?\s*(\d+)\s*\+\s*(\d{1,3}(?:\.\d+)?)|"
                      r"\b(?:CH(?:AINAGE)?|STA(?:TION)?)\.?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*m?\b", re.I)
SECTION_NAME = re.compile(r"\b(?:CROSS[- ]SECTION|SECTION|SEC\.|PROFILE)\s*[:#]?\s*([A-Z0-9]{1,3}(?:\s*[-–]\s*[A-Z0-9]{1,3})?)\b", re.I)


def label_parts(parts, texts, box):
    """Section titles and chainages written next to each drawing: texts = [(x, y, text)], in drawing units."""
    diag = math.hypot(box[2] - box[0], box[3] - box[1])
    out = []
    for _lines, b in parts:
        def dist(t):
            x, y = t[0], t[1]
            dx = max(b[0] - x, 0, x - b[2])
            dy = max(b[1] - y, 0, y - b[3])
            return math.hypot(dx, dy)
        near = sorted((t for t in texts if dist(t) < 0.06 * diag), key=dist)
        name = ch = None
        for t in near:
            if name is None and (m := SECTION_NAME.search(t[2])):
                name = "Section " + re.sub(r"\s+", "", m.group(1)).upper()
            if ch is None and (m := CHAINAGE.search(t[2])):
                ch = float(m.group(1)) * 1000 + float(m.group(2)) if m.group(1) else float(m.group(3))
        out.append((name, ch))
    return out


def _sniff(path: Path) -> str:
    """The file type from its first bytes, so a file with a missing or wrong extension still opens."""
    head = path.read_bytes()[:64]
    if head.startswith(b"%PDF") or b"%PDF" in head:
        return ".pdf"
    if head[:4] == b"AC10":
        return ".dwg"
    if head.startswith(b"AutoCAD Binary DXF") or re.match(rb"\s*0\s*[\r\n]+\s*(SECTION|SECTIOn)", head):
        return ".dxf"
    if head.startswith((b"\x89PNG", b"\xff\xd8", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")) or head[8:12] == b"WEBP":
        return ".png"
    if b"<svg" in path.read_bytes()[:2000]:
        return ".svg"
    return ".csv"


PICTURES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")


CAD_FILES = (".dxf", ".dwg")
TABLE_FILES = (".csv", ".txt", ".tsv", ".xyz")
PDF_OFF_MESSAGE = ("{name}: PDF, scanned and picture drawings are switched off for now, because their geometry can't "
                   "be read reliably (a PDF stores loose pen strokes with no layers, real units or soil / structure "
                   "meaning; text and hatching are drawn as lines; scans are only pixels). Ask whoever made the "
                   "drawing for a DXF copy (no CAD software is needed here: the app reads DXF itself), or type the "
                   "levels and coordinates into a CSV table (see Download CSV template).")


def file_type(path: Path) -> str:
    """The extension used to read the file, recognised from its content when the name has none or a wrong one."""
    ext = path.suffix.lower()
    if ext not in CAD_FILES + TABLE_FILES + (".pdf", ".svg") + PICTURES:
        ext = _sniff(path)
    return ext


def check_allowed(path: Path, name: str | None = None) -> None:
    """Raises GeometryError for a PDF / picture / SVG drawing while PDF import is switched off (CAD only)."""
    from .config import pdf_import_enabled
    if not pdf_import_enabled() and file_type(path) not in CAD_FILES + TABLE_FILES:
        raise GeometryError(PDF_OFF_MESSAGE.format(name=name or path.name))


def read_file(path: Path, progress=None) -> list[dict]:
    """One draft per cross section found in the file: {name, kind, raw lines with their group, unit, ...}.
    Every route ends in something drawn: vector lines first, then the picture of the drawing (Claude Fable reads
    it when an API key is set, otherwise the straight lines are traced offline)."""
    from .config import ai_mode
    ext = path.suffix.lower()
    if ext not in (".dxf", ".dwg", ".pdf", ".csv", ".txt", ".tsv", ".xyz", ".svg") + PICTURES:
        ext = _sniff(path)
    if ext in (".dxf", ".dwg"):
        try:
            return read_cad(path)
        except GeometryError:
            raise
        except Exception:  # noqa: BLE001  damaged DXF: the simple reader
            if ext == ".dwg":
                raise
            polys, depth, warn = read_dxf(path.read_text(errors="replace"))
            return [{"name": path.stem, "kind": "dxf", "raw": [[[list(q) for q in p], c, "0"] for p, c in polys],
                     "y_is_depth": depth, "unit": 1.0, "warnings": warn}]
    if ext == ".pdf":
        return read_pdf(path, progress)
    if ext == ".svg":
        return read_svg(path)
    if ext in PICTURES:
        return read_picture(path, progress)
    polys, depth, warn = read_table(path.read_text(encoding="utf-8-sig", errors="replace"))
    return [{"name": path.stem, "kind": "table",
             "raw": [[[list(p) for p in pl[0]], pl[1], pl[2] if len(pl) > 2 else "lines"] for pl in polys],
             "y_is_depth": depth, "unit": 1.0, "warnings": warn}]


def read_picture(path: Path, progress=None, pages=None) -> list[dict]:
    """A scanned PDF or a picture of a drawing. With a Claude API key, Claude Fable reads the drawing like an
    engineer (dimensions, levels, what is soil and what is structure). Offline, the straight horizontal and
    vertical lines are traced from the pixels, so the section still opens and can be finished in the table."""
    from .config import ai_mode
    if ai_mode() == "claude":
        if progress:
            progress(f"{path.name}: no drawn lines inside, Claude Fable is reading the picture…")
        try:
            polys, depth, warn = read_with_ai(_only_pages(path, pages))
            return [{"name": path.stem, "kind": "ai",
                     "raw": [[[list(p) for p in pl[0]], pl[1], pl[2]] for pl in polys],
                     "y_is_depth": depth, "unit": 1.0, "warnings": warn}]
        except Exception:  # noqa: BLE001  no network etc.: trace offline
            pass
    if progress:
        progress(f"{path.name}: tracing the lines from the picture…")
    return _trace_picture(path, pages)


def _only_pages(path: Path, pages) -> Path:
    if not pages:
        return path
    import tempfile

    import fitz
    doc = fitz.open(str(path))
    doc.select([p - 1 for p in pages])
    out = Path(tempfile.mkdtemp(prefix="pages_")) / path.name
    doc.save(str(out))
    return out


def _trace_picture(path: Path, pages=None) -> list[dict]:
    try:
        import fitz
    except ImportError as e:
        raise GeometryError(f"{path.name}: reading pictures offline needs PyMuPDF (python -m pip install pymupdf; "
                            "run.bat does this).") from e
    is_pdf = path.suffix.lower() == ".pdf" or _sniff(path) == ".pdf"
    doc = fitz.open(str(path))
    if not is_pdf:
        doc = fitz.open("pdf", doc.convert_to_pdf())
    drafts = []
    for pno, page in enumerate(doc, 1):
        if pages and pno not in pages:
            continue
        long_side = max(page.rect.width, page.rect.height)
        zoom = min(4.0, 3000 / long_side)  # about 3000 pixels on the long side
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
        sm = pix.samples
        paper = max(range(256), key=sm[::97].count) if len(sm) else 255
        raw = trace_pixels(sm, pix.width, pix.height, dark=max(60, paper - 35))
        if not raw:
            continue
        texts = []
        try:
            H = page.rect.height
            texts = [((b[0] + b[2]) / 2 * zoom, (H - (b[1] + b[3]) / 2) * zoom, " ".join(b[4].split()))
                     for b in page.get_text("blocks")]
        except Exception:  # noqa: BLE001
            pass
        alltext = " ".join(t[2] for t in texts)
        m = re.findall(r"1\s*:\s*(\d{2,4})\b", alltext)
        if is_pdf:
            scale = float(max(set(m), key=m.count)) if m else 100.0
            unit, warn = PT / zoom * scale, [] if m else ["No drawing scale found, 1:100 assumed: set the right one."]
        else:
            scale, unit = None, 0.02
            warn = ["A picture has no scale: 1 pixel = 2 cm is assumed. Type the real width of a known line in "
                    "the table, or change the scale."]
        warn.append("Read from the picture (no CAD lines inside): horizontal and vertical lines are traced; add "
                    "sloped lines such as anchors with + Line. With a Claude API key, Claude Fable reads it fully.")
        off = sorted(_hatch_groups(raw))
        box = (0, 0, pix.width, pix.height)
        parts = cluster(raw, box)
        labels = label_parts(parts, texts, box)
        for i, ((part, _b), (name, ch)) in enumerate(zip(parts, labels), 1):
            d = {"name": name or (f"{path.stem} p{pno}" + (f"-{i}" if len(parts) > 1 else "")), "kind": "pdf", "source": "picture",
                 "page": pno, "raw": part, "off": off, "y_is_depth": False, "unit": unit, "chainage": ch, "warnings": list(warn)}
            if scale:
                d["scale"] = scale
            drafts.append(d)
    if not drafts:
        raise GeometryError(f"{path.name}: the file opened, but no lines could be seen in it (blank or very faint "
                            "page). Try a sharper scan, or the PDF/DXF exported from CAD.")
    return drafts


def trace_pixels(samples: bytes, w: int, h: int, dark: int = 140) -> list:
    """Straight horizontal and vertical lines in a grey picture: runs of dark pixels, joined across the rows a
    thick line covers. Short runs (letters, hatching, noise) are left out. y is upwards like a PDF."""
    table = bytes(35 if v < dark else 46 for v in range(256))  # dark -> '#', light -> '.'
    img = samples.translate(table)
    min_len = max(25, int(0.02 * min(w, h)))
    pat = re.compile(rb"#{%d,}" % min_len)

    def runs(rows):
        out = []  # (row, start, end)
        for r, row in enumerate(rows):
            for m in pat.finditer(row):
                out.append((r, m.start(), m.end() - 1))
        return out

    def join(rs):
        lines, open_ = [], {}
        for r, a, b in sorted(rs):
            key = next((k for k in open_ if abs(k[0] - a) <= 3 and abs(k[1] - b) <= 3 and r - open_[k][1] <= 1), None)
            if key:
                first, _last, n = open_.pop(key)
                open_[(a, b)] = (first, r, n + 1)
            else:
                open_[(a, b)] = (r, r, 1)
            for k in [k for k, v in open_.items() if r - v[1] > 1]:
                lines.append((k, open_.pop(k)))
        lines += list(open_.items())
        return [((a, b), (f + l) / 2, n) for (a, b), (f, l, n) in lines if n <= 12]  # thicker = filled area

    hor = join(runs(img[r * w:(r + 1) * w] for r in range(h)))
    ver = join(runs(img[c::w] for c in range(w)))
    # lines traced from pixels stop a pixel or two short of each other: pull each end onto the line it meets
    tol = max(4, int(0.004 * max(w, h)))
    H = [[a, b, r, n] for (a, b), r, n in hor]
    V = [[a, b, c, n] for (a, b), c, n in ver]
    for L in H:
        for i in (0, 1):
            for a, b, c, _n in V:
                if abs(L[i] - c) <= tol and a - tol <= L[2] <= b + tol:
                    L[i] = c
                    break
    for L in V:
        for i in (0, 1):
            for a, b, r, _n in H:
                if abs(L[i] - r) <= tol and a - tol <= L[2] <= b + tol:
                    L[i] = r
                    break
    raw = []
    for a, b, row, n in H:
        raw.append([[[a, h - row], [b, h - row]], False, "picture: thick lines" if n >= 4 else "picture: thin lines"])
    for a, b, col, n in V:
        raw.append([[[col, h - a], [col, h - b]], False, "picture: thick lines" if n >= 4 else "picture: thin lines"])
    return raw


def read_svg(path: Path) -> list[dict]:
    """SVG drawings: line, polyline, polygon, rect and straight path pieces."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(path.read_bytes())
    raw = []

    def nums(t):
        return [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", t or "")]

    for el in root.iter():
        tag = el.tag.split("}")[-1]
        g = (el.get("stroke-width") and f"line weight {el.get('stroke-width')}") or "lines"
        if tag == "line":
            pts = [[float(el.get("x1", 0)), -float(el.get("y1", 0))], [float(el.get("x2", 0)), -float(el.get("y2", 0))]]
            raw.append([pts, False, g])
        elif tag in ("polyline", "polygon"):
            v = nums(el.get("points"))
            pts = [[v[i], -v[i + 1]] for i in range(0, len(v) - 1, 2)]
            if len(pts) > 1:
                raw.append([pts + ([pts[0]] if tag == "polygon" else []), tag == "polygon", g])
        elif tag == "rect":
            x, y, ww, hh = (float(el.get(k, 0) or 0) for k in ("x", "y", "width", "height"))
            raw.append([[[x, -y], [x + ww, -y], [x + ww, -y - hh], [x, -y - hh], [x, -y]], True, g])
        elif tag == "path":
            pts = []
            seq = re.findall(r"([MLHVZmlhvz])([^MLHVZmlhvzCcSsQqTtAa]*)", el.get("d", ""))
            x = y = 0.0
            for c, args in seq:
                v = nums(args)
                if c in "Mm":
                    if len(pts) > 1:
                        raw.append([pts, False, g])
                    pts = []
                    for i in range(0, len(v) - 1, 2):  # later pairs after M / m are line-tos
                        x, y = (v[i], v[i + 1]) if c == "M" else (x + v[i], y + v[i + 1])
                        pts.append([x, -y])
                elif c in "Ll":
                    for i in range(0, len(v) - 1, 2):
                        x, y = (v[i], v[i + 1]) if c == "L" else (x + v[i], y + v[i + 1])
                        pts.append([x, -y])
                elif c in "Hh":
                    for a in v:
                        x = a if c == "H" else x + a
                        pts.append([x, -y])
                elif c in "Vv":
                    for a in v:
                        y = a if c == "V" else y + a
                        pts.append([x, -y])
                elif c in "Zz" and pts:
                    pts.append(list(pts[0]))
            if len(pts) > 1:
                raw.append([pts, False, g])
    if not raw:
        raise GeometryError(f"{path.name}: no lines found in the SVG.")
    xs, ys = [p[0] for r in raw for p in r[0]], [p[1] for r in raw for p in r[0]]
    box = (min(xs), min(ys), max(xs), max(ys))
    parts = cluster(raw, box)
    return [{"name": path.stem + (f" {i}" if len(parts) > 1 else ""), "kind": "dxf", "raw": part,
             "off": sorted(_hatch_groups(raw)), "y_is_depth": False, "unit": 0.01,
             "warnings": ["SVG has no units: 1 unit = 1 cm assumed. Change the units if that's wrong."]}
            for i, (part, _b) in enumerate(parts, 1)]


def _hatch_groups(raw: list) -> set:
    """Line groups that are not geometry, switched off at first: hatching (many lines, most of them parallel and
    not horizontal / vertical) and letters drawn as strokes (many very short lines)."""
    pts = [p for r in raw for p in r[0]]
    if not pts:
        return set()
    diag = math.hypot(max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts))
    by, lens = {}, {}
    for pl, _c, g in raw:
        for p, q in zip(pl, pl[1:]):
            if p != q:
                by.setdefault(g, []).append(round(math.degrees(math.atan2(q[1] - p[1], q[0] - p[0])) % 180 / 5))
                lens.setdefault(g, []).append(math.dist(p, q))
    out = set()
    if len(by) < 2:
        return out
    for g, angles in by.items():
        if len(angles) < 12:
            continue
        ls = lens[g]
        # long straight lines of the drawing (boundaries, levels, walls): a group that has them is geometry, even
        # when the same pen also wrote the text (the short strokes are left out one by one when drawing)
        long_ = [(a, L) for a, L in zip(angles, ls) if L > 0.03 * diag]
        ortho = sum(L for a, L in long_ if a in (0, 18, 36))
        if len(long_) >= 5 and ortho > 0.15 * sum(ls):
            continue
        main = max(set(angles), key=angles.count)
        if angles.count(main) >= 0.7 * len(angles) and main not in (0, 18, 36):  # hatching
            out.add(g)
        srt = sorted(ls)
        if len(srt) >= 20 and srt[len(srt) // 2] < 0.012 * diag and len(long_) < 5:  # text strokes, symbols
            out.add(g)
    return out


def _thin(use: list, limit: int) -> list:
    def length(r):
        return sum(math.dist(p, q) for p, q in zip(r[0], r[0][1:]))
    out, n = [], 0
    for r in sorted(use, key=length, reverse=True):
        if n + len(r[0]) > limit:
            break
        out.append(r); n += len(r[0])
    return out


# --------------------------------------------------------------------------------------------------------------
# Layer roles: the engineer says what a CAD layer (or a PDF line group) is. Choices on CAD layers are learnt in a
# library shared by every project, so the next drawing from the same office reads right the first time.
# --------------------------------------------------------------------------------------------------------------
KINDS = ("soil", "structure", "water")
ROLES = KINDS + ("ignore",)


def _layer_key(g: str) -> str:
    return re.sub(r"\s+", " ", (g or "").strip().lower())


def _library_path() -> Path:
    from .config import data_dir
    return data_dir() / "layer_roles.json"


def layer_library() -> dict:
    import json
    try:
        lib = json.loads(_library_path().read_text())
        return {k: v for k, v in lib.items() if v in ROLES} if isinstance(lib, dict) else {}
    except (OSError, ValueError):
        return {}


def save_layer_library(lib: dict) -> dict:
    import json
    lib = {_layer_key(k): v for k, v in lib.items() if _layer_key(k) and v in ROLES}
    _library_path().write_text(json.dumps(dict(sorted(lib.items())), indent=1))
    return lib


def learn_roles(roles: dict) -> None:
    """Remembers the engineer's choices for CAD layer names ("0" and unnamed layers say nothing, so are skipped)."""
    lib = layer_library()
    for g, r in roles.items():
        k = _layer_key(g)
        if not k or k in ("0", "defpoints"):
            continue
        if r in ROLES:
            lib[k] = r
        else:
            lib.pop(k, None)
    save_layer_library(lib)


def _apply_roles(sec: dict, raw: list) -> dict:
    """Roles chosen for this section; at the first read of a CAD file, the learnt library fills them in."""
    groups = sec.setdefault("groups", {})
    if "roles" not in sec:
        lib = layer_library() if sec.get("kind") == "dxf" else {}
        names = {r[2] for r in raw}
        sec["roles"] = {g: lib[_layer_key(g)] for g in names if _layer_key(g) in lib}
        if sec["roles"]:
            sec.setdefault("warnings", []).append(
                f"{len(sec['roles'])} layer(s) set from the layer library learnt on earlier projects. "
                "Change any of them in Extracted lines.")
    roles = sec["roles"]
    for g, r in roles.items():
        if r == "ignore":
            groups[g] = False
        elif r in KINDS:
            groups[g] = True
    return roles


def rebuild(sec: dict, raw: list) -> dict:
    """(Re)draws a section from its file's lines with the current layer choice and scale. Replaces hand edits."""
    groups = sec.setdefault("groups", {})
    off = set(sec.pop("off", None) or [])
    hatch = (_hatch_groups(raw) | off) if not groups else set()
    for _p, _c, g in raw:
        if g not in groups:
            groups[g] = not ((sec.get("kind") == "dxf" and OFF_LAYERS.search(g or "")) or g in hatch)
    roles = _apply_roles(sec, raw)
    use = [r for r in raw if groups.get(r[2], True)]

    def extent(rs):
        ps = [p for r in rs for p in r[0]]
        return math.hypot(max(p[0] for p in ps) - min(p[0] for p in ps), max(p[1] for p in ps) - min(p[1] for p in ps)) if ps else 0

    if hatch and raw and extent(use) < 0.6 * extent(raw):  # the drawing itself was in a group switched off
        for g in {r[2] for r in raw}:
            if not (sec.get("kind") == "dxf" and OFF_LAYERS.search(g or "")):
                groups[g] = roles.get(g) != "ignore"
        use = [r for r in raw if groups.get(r[2], True)]
    if not use:
        raise GeometryError("Every layer is switched off. Tick at least one.")
    if sec.get("kind") == "pdf" and len(use) > 30:  # letters and symbols drawn as tiny strokes: leave them out
        pts = [p for r in use for p in r[0]]
        diag = math.hypot(max(p[0] for p in pts) - min(p[0] for p in pts), max(p[1] for p in pts) - min(p[1] for p in pts))
        tiny = 0.012 * diag
        kept = [r for r in use if sum(math.dist(p, q) for p, q in zip(r[0], r[0][1:])) >= tiny]
        if len(kept) >= 4:
            use = kept
    if sum(len(r[0]) for r in use) > 60000:  # very busy drawing: keep the longest lines, say so
        use = _thin(use, 60000)
        sec.setdefault("warnings", []).append("Very busy drawing: the shortest lines (text, symbols, hatching) were "
                                              "left out. Switch layers off to choose what is kept.")
    u = sec.get("unit") or 1.0
    polys = [([(x * u, y * u) for x, y in pts], closed, f"role:{roles[g]}:{g}" if roles.get(g) in KINDS else g)
             for pts, closed, g in use]
    sec.update(simplify(polys, sec.get("y_is_depth", False)))
    sec["edited"] = sec["model"] = False
    return sec


def classify_with_ai(sec: dict) -> bool:
    """With the Claude AI: looks at the simplified lines (and the CAD layer names) and marks each as soil or
    structure. Returns False when the AI is not available; the offline rules have already been applied then."""
    from .config import DRAWING_MODEL, ai_mode, api_key
    if ai_mode() != "claude" or not sec.get("segments"):
        return False
    from typing import Literal

    import anthropic
    from pydantic import BaseModel

    class Kinds(BaseModel):
        kinds: list[Literal["soil", "structure", "water"]]

    nodes = sec["nodes"]
    lines = "\n".join(f"{i}: ({fmt(nodes[a][0])}, {fmt(nodes[a][1])}) -> ({fmt(nodes[b][0])}, {fmt(nodes[b][1])})"
                      for i, (a, b, *_t) in enumerate(sec["segments"]))
    layers = ", ".join(k for k, on in (sec.get("groups") or {}).items() if on)
    prompt = ("These are the lines of a geotechnical cross section for PLAXIS, in metres. x to the right, z = depth "
              f"positive downwards. Layers / line groups in the file: {layers or 'none'}.\n{lines}\n\n"
              "For every line, in the same order, say if it belongs to the soil boxes (model boundary, ground "
              "surface, soil layer boundaries, excavation outline), to a structure (wall, pile, anchor, strut, slab, "
              f"beam, footing, lining) or is a water level (phreatic line). Return exactly {len(sec['segments'])} kinds.")
    try:
        r = anthropic.Anthropic(api_key=api_key()).beta.messages.parse(
            model=DRAWING_MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium"}, messages=[{"role": "user", "content": prompt}], output_format=Kinds)
    except Exception:  # noqa: BLE001
        return False
    k = r.parsed_output.kinds if r.parsed_output else []
    if len(k) != len(sec["segments"]):
        return False
    sec["segments"] = keep_fixed(sec, [[a, b, t] for (a, b, *_x), t in zip(sec["segments"], k)])
    return True


def keep_fixed(sec: dict, new_segs: list) -> list:
    """Lines on a layer the engineer gave a role keep that role, whatever a later automatic pass says."""
    old = sec.get("segments") or []
    for i in sec.get("fixed") or []:
        if i < len(new_segs) and i < len(old) and len(old[i]) > 2:
            new_segs[i] = [new_segs[i][0], new_segs[i][1], old[i][2]]
    return new_segs


def set_geometry(sec: dict, nodes: list, segments: list) -> dict:
    """Hand edits from the coordinate table."""
    clean = []
    for n in nodes:
        try:
            clean.append([_r(float(n[0])), _r(float(n[1]))])
        except (TypeError, ValueError, IndexError) as e:
            raise GeometryError("Every node needs a number for x and for z.") from e
    segs = {}
    for s in segments:
        try:
            a, b = int(s[0]), int(s[1])
        except (TypeError, ValueError, IndexError):
            continue
        if a != b and 0 <= a < len(clean) and 0 <= b < len(clean):
            segs[(min(a, b), max(a, b))] = s[2] if len(s) > 2 and s[2] in ("structure", "water") else "soil"
    xs, zs = [n[0] for n in clean] or [0], [n[1] for n in clean] or [0]
    sec.pop("fixed", None)
    sec.update(nodes=clean, segments=[[a, b, t] for (a, b), t in sorted(segs.items())], edited=True,
               width=_r(max(xs) - min(xs)), depth=_r(max(zs) - min(zs)))
    return sec


# --------------------------------------------------------------------------------------------------------------
# Simplify: snap nodes, split at junctions, merge collinear segments, origin at upper left.
# --------------------------------------------------------------------------------------------------------------
def simplify(polylines, y_is_depth: bool = False) -> dict:
    pts = [p for line, *_ in polylines for p in line]
    if not pts:
        raise GeometryError("The drawing has no lines.")
    xmin = min(p[0] for p in pts)
    ytop = min(p[1] for p in pts) if y_is_depth else max(p[1] for p in pts)

    def tr(p):
        return (p[0] - xmin, (p[1] - ytop) if y_is_depth else (ytop - p[1]))

    span = max(max(p[0] for p in pts) - xmin, abs(max(p[1] for p in pts) - min(p[1] for p in pts)), 1e-9)
    tol = max(0.005, span * 1e-5)  # 5 mm, or less precise for very large drawings
    nodes, index = [], {}

    def node(p):
        x, z = tr(p)
        key = (round(x / tol), round(z / tol))
        for dx in (-1, 0, 1):  # neighbouring cells, so points just either side of a cell edge still merge
            for dz in (-1, 0, 1):
                k = (key[0] + dx, key[1] + dz)
                if k in index and math.dist(nodes[index[k]], (x, z)) <= tol:
                    return index[k]
        index[key] = len(nodes)
        nodes.append((x, z))
        return index[key]

    segs = {}  # (a, b) -> layer / line group it came from
    for line, closed, *rest in polylines:
        grp = rest[0] if rest else ""
        ids = [node(p) for p in line]
        if closed and len(ids) > 2:
            ids.append(ids[0])
        for a, b in zip(ids, ids[1:]):
            if a != b:
                k = (min(a, b), max(a, b))
                if k not in segs or (grp and STRUCT_WORDS.search(grp)):  # a wall drawn on a soil edge stays a wall
                    segs[k] = grp

    # split segments where another node lies on them (T-junctions); candidates found through sorted x and z lists
    import bisect
    by_x = sorted(range(len(nodes)), key=lambda i: nodes[i][0])
    xs = [nodes[i][0] for i in by_x]
    by_z = sorted(range(len(nodes)), key=lambda i: nodes[i][1])
    zs = [nodes[i][1] for i in by_z]
    split = {}
    for (a, b), grp in segs.items():
        (x1, z1), (x2, z2) = nodes[a], nodes[b]
        L = math.dist(nodes[a], nodes[b])
        if abs(x2 - x1) <= abs(z2 - z1):
            lo, hi = bisect.bisect_left(xs, min(x1, x2) - tol), bisect.bisect_right(xs, max(x1, x2) + tol)
            cand = by_x[lo:hi]
        else:
            lo, hi = bisect.bisect_left(zs, min(z1, z2) - tol), bisect.bisect_right(zs, max(z1, z2) + tol)
            cand = by_z[lo:hi]
        on = []
        for n in cand:
            if n in (a, b):
                continue
            x, z = nodes[n]
            t = ((x - x1) * (x2 - x1) + (z - z1) * (z2 - z1)) / (L * L)
            if 0 < t < 1 and abs((x2 - x1) * (z - z1) - (z2 - z1) * (x - x1)) / L <= tol:
                on.append((t, n))
        chain = [a] + [n for _t, n in sorted(on)] + [b]
        for p, q in zip(chain, chain[1:]):
            if p != q:
                k = (min(p, q), max(p, q))
                if k not in split or (grp and STRUCT_WORDS.search(grp)):
                    split[k] = grp
    segs = split

    # merge straight runs: a node joining exactly two collinear segments is not a real node
    adj = {}
    for a, b in segs:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    for n in list(adj):
        nb = adj.get(n)
        if not nb or len(nb) != 2:
            continue
        a, b = nb
        (x1, z1), (x2, z2), (x, z) = nodes[a], nodes[b], nodes[n]
        L = math.dist(nodes[a], nodes[b])
        if L > 0 and b not in adj[a] and abs((x2 - x1) * (z - z1) - (z2 - z1) * (x - x1)) / L <= tol \
                and ((x - x1) * (x2 - x) + (z - z1) * (z2 - z)) >= 0:
            segs[(min(a, b), max(a, b))] = segs.get((min(a, n), max(a, n)), "")
            adj[a].discard(n)
            adj[b].discard(n)
            adj[a].add(b)
            adj[b].add(a)
            del adj[n]
    segs = {(min(a, b), max(a, b)): segs.get((min(a, b), max(a, b)), "") for a, nb in adj.items() for b in nb}

    used = sorted({i for s in segs for i in s}, key=lambda i: (round(nodes[i][1], 3), round(nodes[i][0], 3)))
    remap = {old: new for new, old in enumerate(used)}
    out_nodes = [[_r(nodes[i][0]), _r(nodes[i][1])] for i in used]
    out = sorted((sorted([remap[a], remap[b]]), g) for (a, b), g in segs.items())
    types = classify(out_nodes, [s for s, _g in out], [g for _s, g in out])
    out_segs = [[a, b, t] for (a, b), t in zip((s for s, _g in out), types)]
    return {"nodes": out_nodes, "segments": out_segs,
            "fixed": [i for i, (_s, g) in enumerate(out) if (g or "").startswith("role:")],
            "width": _r(max(n[0] for n in out_nodes)) if out_nodes else 0,
            "depth": _r(max(n[1] for n in out_nodes)) if out_nodes else 0}


SOIL_WORDS = re.compile(r"(soil|layer|strat|ground|geo|boring|borehole|clay|sand|silt|gravel|rock|fill|boundary|"
                        r"model|terrain|excav|cluster|polygon)", re.I)
WATER_WORDS = re.compile(r"(water|gwl|gwt|phreatic|piezo|wl\b|hydro)", re.I)
STRUCT_WORDS = re.compile(r"(wall|pile|plate|anchor|tieback|strut|prop|slab|beam|column|conc|struct|sheet|diaphragm|"
                          r"secant|contiguous|nail|raft|footing|found|waler|kingpost|geogrid|tunnel|lining|rebar|"
                          r"steel|retain|d-wall|dwall|capping)", re.I)


def classify(nodes: list, segs: list, groups: list | None = None) -> list[str]:
    """Tells soil lines (soil boxes: model boundary, ground surface, layer boundaries, excavation) from structure
    lines (walls, piles, anchors, struts, slabs). Rules, in order:
    1. the CAD layer name, when it says so (e.g. "SOIL_LAYERS", "D-WALL", "ANCHORS");
    2. thin rectangles / pairs of close parallel lines (a wall or slab drawn with its thickness) are structures;
    3. lines that end in the soil without closing a box (a wall below the excavation, an anchor, a strut) are
       structures; lines that form closed boxes are soil.
    Every line can be changed in the table."""
    groups = groups or [""] * len(segs)
    n = len(nodes)
    if not segs:
        return []
    types = [None] * len(segs)
    for i, g in enumerate(groups):
        if g and g.startswith("role:"):
            types[i] = g.split(":")[1]
        elif g and (WATER_WORDS.search(g) or "dashed" in g):
            types[i] = "water"
        elif g and STRUCT_WORDS.search(g):
            types[i] = "structure"
        elif g and SOIL_WORDS.search(g):
            types[i] = "soil"
    xs, zs = [p[0] for p in nodes], [p[1] for p in nodes]
    size = max(max(xs) - min(xs), max(zs) - min(zs), 1e-9)
    thin = min(0.03 * size, 2.5)  # a wall / slab is at most this thick
    # 2. close parallel pairs (not the long soil layer lines that run across the whole section)
    width = max(xs) - min(xs)
    long_ = [abs(nodes[b][0] - nodes[a][0]) > 0.75 * width for a, b in segs]
    for i, (a, b) in enumerate(segs):
        if types[i] or long_[i] or len(segs) > 3000:
            continue
        (x1, z1), (x2, z2) = nodes[a], nodes[b]
        L = math.dist(nodes[a], nodes[b])
        if L < 1e-9:
            continue
        ux, uz = (x2 - x1) / L, (z2 - z1) / L
        for j, (c, d) in enumerate(segs):
            if j == i or long_[j] or types[j] in ("water", "soil"):
                continue
            (x3, z3), (x4, z4) = nodes[c], nodes[d]
            L2 = math.dist(nodes[c], nodes[d])
            if L2 < 1e-9 or abs(ux * (z4 - z3) / L2 - uz * (x4 - x3) / L2) > 0.02:
                continue  # not parallel
            off = abs(ux * (z3 - z1) - uz * (x3 - x1))
            if not (1e-6 < off <= thin) or L < 0.5 * off or L2 < 0.5 * off:
                continue
            t3, t4 = (x3 - x1) * ux + (z3 - z1) * uz, (x4 - x1) * ux + (z4 - z1) * uz
            overlap = min(L, max(t3, t4)) - max(0, min(t3, t4))
            if overlap > 0.5 * min(L, L2):
                types[i] = types[j] = "structure"
                break
    # caps of thin rectangles: short lines joining two structure lines
    for i, (a, b) in enumerate(segs):
        if types[i] is None and math.dist(nodes[a], nodes[b]) <= thin:
            ends = [any(types[j] == "structure" and v in segs[j] for j in range(len(segs))) for v in (a, b)]
            if all(ends):
                types[i] = "structure"
    # 3. dangling lines: peel off lines that end at a node with no other line
    deg = [0] * n
    alive = [True] * len(segs)  # structure lines still close soil boxes (a wall is also the excavation face)
    for i, (a, b) in enumerate(segs):
        if alive[i]:
            deg[a] += 1
            deg[b] += 1
    changed = True
    while changed:
        changed = False
        for i, (a, b) in enumerate(segs):
            if alive[i] and (deg[a] == 1 or deg[b] == 1):
                alive[i] = False
                deg[a] -= 1
                deg[b] -= 1
                if types[i] is None:
                    types[i] = "structure"
                changed = True
    return [t or "soil" for t in types]


def _r(v: float) -> float:
    v = round(v, 3)
    return 0.0 if v == 0 else v


def fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# --------------------------------------------------------------------------------------------------------------
# Drawing (SVG, black on white)
# --------------------------------------------------------------------------------------------------------------
def _levels(values, tol=0.01):
    out = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def svg(section: dict, labels: bool = True, dims: bool = True, numbers: bool = False) -> str:
    nodes, segs = section["nodes"], section["segments"]
    if not nodes:
        return ('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80"><rect width="400" height="80" '
                'fill="#fff"/><text x="12" y="44" font-family="Arial" font-size="13">No nodes. Add nodes in the '
                'table.</text></svg>')
    y = section.get("y") or 0.0
    xmin, zmin = min(min(n[0] for n in nodes), 0), min(min(n[1] for n in nodes), 0)
    W = max(max(n[0] for n in nodes) - xmin, 1e-6)
    H = max(max(n[1] for n in nodes) - zmin, 1e-6)
    s = min(1000 / W, 620 / H)
    if W * s < 200:  # very narrow sections: keep them readable
        s = 200 / W
    top = 92 if dims else 34
    left = 100 if dims else 34
    right, bottom = (150 if labels else 34), 50
    cw, ch = left + W * s + right, top + H * s + bottom

    def X(x):
        return left + (x - xmin) * s

    def Z(z):
        return top + (z - zmin) * s

    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cw:.0f} {ch:.0f}" width="{cw:.0f}" height="{ch:.0f}" '
         f'font-family="Arial, Helvetica, sans-serif" font-size="11" fill="#000">',
         f'<rect width="{cw:.0f}" height="{ch:.0f}" fill="#fff"/>',
         f'<title>{escape(section.get("name", "Cross section"))}</title>']
    # geometry
    # soil lines thin, structure lines thick (black and white, like a drawing)
    o.append('<g stroke="#000" stroke-linecap="round">')
    for k, sg in enumerate(segs):
        (x1, z1), (x2, z2) = nodes[sg[0]], nodes[sg[1]]
        t = sg[2] if len(sg) > 2 else "soil"
        st = t == "structure"
        dash = ' stroke-dasharray="7 4"' if t == "water" else ""
        o.append(f'<line class="sg {t}" data-s="{k}" x1="{X(x1):.1f}" y1="{Z(z1):.1f}" '
                 f'x2="{X(x2):.1f}" y2="{Z(z2):.1f}" stroke-width="{3.6 if st else 1.3}"{dash}/>')
    o.append("</g>")
    o.append("".join(f'<circle class="nd" data-n="{i}" cx="{X(x):.1f}" cy="{Z(z):.1f}" r="2.8"/>'
                     for i, (x, z) in enumerate(nodes)))

    taken = [(X(x) - 3, Z(z) - 3, X(x) + 3, Z(z) + 3) for x, z in nodes]
    # sample the lines so labels avoid sitting on them
    for a, b, *_t in segs:
        (x1, z1), (x2, z2) = nodes[a], nodes[b]
        n = max(1, int(math.dist((X(x1), Z(z1)), (X(x2), Z(z2))) / 6))
        for i in range(1, n):
            px, pz = X(x1 + (x2 - x1) * i / n), Z(z1 + (z2 - z1) * i / n)
            taken.append((px - 1.5, pz - 1.5, px + 1.5, pz + 1.5))
    if not any(abs(x) < 1e-6 and abs(z) < 1e-6 for x, z in nodes):  # mark the origin when no node sits there
        o.append(f'<path d="M{X(0) - 6} {Z(0)}h12M{X(0)} {Z(0) - 6}v12" stroke="#000" stroke-width="1"/>')
        if labels:
            o.append(f'<text x="{X(0) + 5}" y="{Z(0) + 13}">(0, 0, 0)</text>')
            taken.append((X(0), Z(0) + 3, X(0) + 60, Z(0) + 15))
    if dims:
        o.append('<g stroke="#000" stroke-width="0.7">')
        xs, zs = _levels([n[0] for n in nodes]), _levels([n[1] for n in nodes])
        ytext = []
        # horizontal: chain row, then overall
        for row, vals in ((top - 34, xs), (top - 66, [xs[0], xs[-1]])):
            for v in vals:
                o.append(f'<line x1="{X(v):.1f}" y1="{top - 8}" x2="{X(v):.1f}" y2="{row - 5}"/>')
                o.append(_tick(X(v), row))
            o.append(f'<line x1="{X(vals[0]):.1f}" y1="{row}" x2="{X(vals[-1]):.1f}" y2="{row}"/>')
            for i, (a, b) in enumerate(zip(vals, vals[1:])):
                txt = fmt(b - a)
                lift = 4 + (12 if (X(b) - X(a)) < len(txt) * 6.5 + 4 and i % 2 else 0)
                ytext.append(f'<text x="{(X(a) + X(b)) / 2:.1f}" y="{row - lift}" text-anchor="middle" stroke="none">{txt}</text>')
        # vertical: chain column, then overall
        for col, vals in ((left - 38, zs), (left - 72, [zs[0], zs[-1]])):
            for v in vals:
                o.append(f'<line x1="{left - 8}" y1="{Z(v):.1f}" x2="{col - 5}" y2="{Z(v):.1f}"/>')
                o.append(_tick(col, Z(v)))
            o.append(f'<line x1="{col}" y1="{Z(vals[0]):.1f}" x2="{col}" y2="{Z(vals[-1]):.1f}"/>')
            for i, (a, b) in enumerate(zip(vals, vals[1:])):
                txt = fmt(b - a)
                shift = 4 + (12 if (Z(b) - Z(a)) < len(txt) * 6.5 + 4 and i % 2 else 0)
                cx, cz = col - shift, (Z(a) + Z(b)) / 2
                ytext.append(f'<text x="{cx:.1f}" y="{cz:.1f}" text-anchor="middle" stroke="none" '
                             f'transform="rotate(-90 {cx:.1f} {cz:.1f})">{txt}</text>')
        o.append("</g>")
        o += ytext
    if labels:
        for i, (x, z) in enumerate(nodes):
            txt = (f"{i + 1}: " if numbers else "") + f"({fmt(x)}, {fmt(y)}, {fmt(z)})"
            w = len(txt) * 5.9
            px, pz = X(x), Z(z)
            best = None
            for dx, dz, anchor in ((5, -5, "start"), (5, 13, "start"), (-5, -5, "end"), (-5, 13, "end"),
                                   (5, -17, "start"), (-5, -17, "end"), (5, 25, "start"), (-5, 25, "end")):
                x0 = px + dx if anchor == "start" else px + dx - w
                box = (x0, pz + dz - 10, x0 + w, pz + dz + 2)
                if box[0] < 2 or box[2] > cw - 2 or box[1] < (top - 6 if dims else 2) or box[3] > ch - 22:
                    continue
                hit = sum(_overlap(box, t) for t in taken)
                if best is None or hit < best[0]:
                    best = (hit, dx, dz, anchor, box)
                if hit == 0:
                    break
            if best is None:
                best = (0, 5, -5, "start", (px + 5, pz - 15, px + 5 + w, pz - 3))
            _, dx, dz, anchor, box = best
            taken.append(box)
            o.append(f'<text class="lb" data-n="{i}" x="{px + dx:.1f}" y="{pz + dz:.1f}" text-anchor="{anchor}">{txt}</text>')
    n_st = sum(1 for sg in segs if len(sg) > 2 and sg[2] == "structure")
    n_w = sum(1 for sg in segs if len(sg) > 2 and sg[2] == "water")
    lx = cw - 420
    o.append(f'<g font-size="10.5" stroke="#000"><line x1="{lx}" y1="{ch - 14:.0f}" x2="{lx + 26}" y2="{ch - 14:.0f}" '
             f'stroke-width="1.3"/><text stroke="none" x="{lx + 32}" y="{ch - 10:.0f}">soil ({len(segs) - n_st - n_w})</text>'
             f'<line x1="{lx + 100}" y1="{ch - 14:.0f}" x2="{lx + 126}" y2="{ch - 14:.0f}" stroke-width="3.6"/>'
             f'<text stroke="none" x="{lx + 132}" y="{ch - 10:.0f}">structure ({n_st})</text>'
             f'<line x1="{lx + 230}" y1="{ch - 14:.0f}" x2="{lx + 256}" y2="{ch - 14:.0f}" stroke-width="1.3" '
             f'stroke-dasharray="7 4"/><text stroke="none" x="{lx + 262}" y="{ch - 10:.0f}">water ({n_w})</text></g>')
    o.append(f'<text x="8" y="{ch - 10:.0f}" font-size="10.5">Origin (0, 0, 0) at upper left · x to the right · '
             f'z = depth, positive downwards · y = {fmt(y)} · units: m · {len(nodes)} nodes</text>')
    o.append("</svg>")
    return "\n".join(o)


def _tick(x, y):
    return f'<line x1="{x - 4:.1f}" y1="{y + 4:.1f}" x2="{x + 4:.1f}" y2="{y - 4:.1f}" stroke-width="1.3"/>'


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0


# --------------------------------------------------------------------------------------------------------------
# PLAXIS model geometry: straighten, close the soil boxes, split where lines cross, soil layer levels
# --------------------------------------------------------------------------------------------------------------
def _intersect(p1, p2, p3, p4):
    d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0])
    if abs(d) < 1e-12:
        return None
    t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d
    u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d
    if 1e-6 < t < 1 - 1e-6 and -1e-6 <= u <= 1 + 1e-6 or -1e-6 <= t <= 1 + 1e-6 and 1e-6 < u < 1 - 1e-6:
        return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
    return None


def model_geometry(sec: dict, grid: float = 0.05) -> dict:
    """Turns the traced drawing into geometry PLAXIS can use:
    - lines within 3 degrees of horizontal / vertical are made exactly horizontal / vertical;
    - coordinates are rounded to 5 cm;
    - soil lines that stop just short of the model boundary are extended to it, and the model box is closed;
    - lines that cross get a node where they cross (soil layers through a wall, anchors through layers);
    - the soil layer levels at the left and right boundaries are listed (they become the boreholes)."""
    nodes = [list(n) for n in sec["nodes"]]
    segs = [list(s[:2]) + [s[2] if len(s) > 2 else "soil"] for s in sec["segments"]]
    if not segs:
        raise GeometryError("No lines to work with.")
    # 1. straighten
    for a, b, _t in segs:
        (x1, z1), (x2, z2) = nodes[a], nodes[b]
        ang = abs(math.degrees(math.atan2(z2 - z1, x2 - x1))) % 180
        if min(ang, 180 - ang) < 3:
            zm = (z1 + z2) / 2
            nodes[a][1] = nodes[b][1] = zm
        elif abs(ang - 90) < 3:
            xm = (x1 + x2) / 2
            nodes[a][0] = nodes[b][0] = xm
    nodes = [[round(x / grid) * grid, round(z / grid) * grid] for x, z in nodes]
    xmin, xmax = min(n[0] for n in nodes), max(n[0] for n in nodes)
    zmax = max(n[1] for n in nodes)
    W = xmax - xmin
    lines = [(tuple(nodes[a]), tuple(nodes[b]), t) for a, b, t in segs]
    # 2. extend horizontal soil lines that stop short of the boundary
    ext = []
    for p, q, t in lines:
        if t != "structure" and abs(p[1] - q[1]) < 1e-9 and abs(q[0] - p[0]) > 0.4 * W:
            lo, hi = sorted([p, q])
            if 0 < lo[0] - xmin < 0.15 * W:
                ext.append(((xmin, lo[1]), lo, t))
            if 0 < xmax - hi[0] < 0.15 * W:
                ext.append((hi, (xmax, hi[1]), t))
    lines += ext
    # 3. close the model box
    # (excavated zones stay inside the box as soil clusters, to be switched off in the staged construction)
    lines += [((xmin, 0.0), (xmax, 0.0), "soil"), ((xmin, zmax), (xmax, zmax), "soil"),
              ((xmin, 0.0), (xmin, zmax), "soil"), ((xmax, 0.0), (xmax, zmax), "soil")]
    # 4. split where lines cross
    if len(lines) <= 3000:
        cuts = [[] for _ in lines]
        for i in range(len(lines)):
            for j in range(i + 1, len(lines)):
                x = _intersect(lines[i][0], lines[i][1], lines[j][0], lines[j][1])
                if x:
                    x = (round(x[0] / grid) * grid, round(x[1] / grid) * grid)
                    cuts[i].append(x)
                    cuts[j].append(x)
        pieces = []
        for (p, q, t), cs in zip(lines, cuts):
            chain = sorted({p, q, *cs}, key=lambda c: math.dist(p, c))
            pieces += [(u, v, t) for u, v in zip(chain, chain[1:]) if math.dist(u, v) > 1e-9]
        lines = pieces
    # rebuild nodes / segments (structure wins where a wall lies on a soil edge)
    index, out_nodes, seg = {}, [], {}
    def nid(p):
        k = (round(p[0], 3), round(p[1], 3))
        if k not in index:
            index[k] = len(out_nodes)
            out_nodes.append([_r(p[0] - xmin), _r(p[1])])
        return index[k]
    rank = {"soil": 0, "water": 1, "structure": 2}
    for p, q, t in lines:
        a, b = nid(p), nid(q)
        if a == b:
            continue
        k = (min(a, b), max(a, b))
        if k not in seg or rank[t] > rank[seg[k]]:
            seg[k] = t
    order = sorted(range(len(out_nodes)), key=lambda i: (out_nodes[i][1], out_nodes[i][0]))
    remap = {o: n for n, o in enumerate(order)}
    sec["nodes"] = [out_nodes[o] for o in order]
    sec["segments"] = sorted([sorted([remap[a], remap[b]]) + [t] for (a, b), t in seg.items()])
    sec["width"] = _r(max(n[0] for n in sec["nodes"]))
    sec["depth"] = _r(max(n[1] for n in sec["nodes"]))
    sec["layers"] = soil_layers(sec)
    sec.pop("fixed", None)  # roles are already in the line types; the line numbers changed
    sec["edited"] = True
    sec["model"] = True
    return sec


def soil_layers(sec: dict) -> dict:
    """Soil layer boundaries (depths) met by a vertical line at the left and right model boundaries."""
    nodes, out = sec["nodes"], {}
    if not nodes:
        return out
    xs = [n[0] for n in nodes]
    for side, x in (("left", min(xs)), ("right", max(xs))):
        lv = set()
        for a, b, *t in sec["segments"]:
            if t and t[0] != "soil":
                continue
            (x1, z1), (x2, z2) = nodes[a], nodes[b]
            if abs(z1 - z2) < 0.5 * abs(x1 - x2) and min(x1, x2) - 1e-6 <= x <= max(x1, x2) + 1e-6 and x1 != x2:
                lv.add(_r(z1 + (z2 - z1) * (x - x1) / (x2 - x1)))
        out[side] = sorted(lv)
    return out


# --------------------------------------------------------------------------------------------------------------
# Quality of a reading: how far the engineer can trust it before confirming it.
# --------------------------------------------------------------------------------------------------------------
def quality(sec: dict) -> dict:
    """high: CAD lines that close into soil boxes at a sensible size; check: usable but read from a picture or
    with something to look at; low: probably not the drawing (tiny, no closed box, or almost no lines)."""
    nodes, segs = sec.get("nodes") or [], sec.get("segments") or []
    low, check = [], []
    if len(segs) < 4:
        low.append(f"Only {len(segs)} line(s) were read.")
    w, d = float(sec.get("width") or 0), float(sec.get("depth") or 0)
    if segs and max(w, d) < 3:
        low.append(f"The drawing read is only {fmt(w)} x {fmt(d)} m: probably a symbol, a title block or a wrong "
                   "scale, not the cross section.")
    elif max(w, d) > 3000:
        check.append(f"The drawing is {fmt(w)} x {fmt(d)} m: check the scale or the units.")
    if segs:
        from .design import faces
        boxes = faces(nodes, segs)
        if not boxes:
            low.append("No closed soil box: the soil layers do not close (lines missing or not joined).")
        deg = {}
        for a, b, *t in segs:
            if (t[0] if t else "soil") == "soil":
                deg[a] = deg.get(a, 0) + 1
                deg[b] = deg.get(b, 0) + 1
        loose = sum(1 for v in deg.values() if v == 1)
        if loose > max(4, 0.25 * len(deg)):
            check.append(f"{loose} soil line ends are loose (not joined to another line).")
        if not any((t[0] if t else "soil") == "structure" for _a, _b, *t in segs):
            check.append("No structure found (wall, pile, anchor, slab): fine if there is none.")
    src = sec.get("source") or sec.get("kind")
    if src == "picture":
        check.append("Traced from a picture of the page: sloped lines and thin lines may be missing.")
    elif src == "ai":
        check.append("Read by Claude Fable from the picture: compare the levels with the drawing.")
    if any("scale" in x.lower() and "assumed" in x.lower() for x in sec.get("warnings") or []):
        check.append("The drawing scale was assumed: set the real scale.")
    level = "low" if low else "check" if check else "high"
    return {"level": level, "reasons": low + check}
