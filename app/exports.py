"""Results of a study as professional figures (PNG), an Excel workbook and a Word report."""
import base64
import io
from datetime import date

from . import study as S

INK, GRID, BLUE, ORANGE, GREEN, RED, GREY = "#1b2b22", "#d9e2dc", "#1f5f8b", "#d4a514", "#2e8b57", "#c0392b", "#8a9a90"


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
        "axes.labelsize": 10.5, "axes.edgecolor": "#6b7a70", "axes.linewidth": 0.8, "axes.grid": True,
        "grid.color": GRID, "grid.linewidth": 0.7, "axes.axisbelow": True, "xtick.color": INK, "ytick.color": INK,
        "axes.labelcolor": INK, "axes.titlecolor": INK, "legend.frameon": False, "figure.dpi": 100,
        "axes.spines.top": False, "axes.spines.right": False})
    return plt


def _unit(x: dict) -> str:
    u = x.get("unit") or ""
    return f" ({u})" if u and u != "-" else ""


def figure(spec: dict) -> bytes:
    """One figure as a PNG (6.8 x 4 in, 200 dpi)."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(6.8, 4.0))
    k = spec["kind"]
    if k == "time":  # V26.1.8: a result over time (stage dates + the long-term phase), one line per run
        t = spec["target"]
        cmap = plt.get_cmap("viridis")
        n = max(1, len(spec["series"]) - 1)
        for i, s in enumerate(spec["series"]):
            pts = sorted(s["points"])
            if not pts:
                continue
            base = s.get("run") == 0 and s.get("existing")
            ax.plot([p[0] for p in pts], [p[1] for p in pts], "-o", ms=4, lw=2.4 if s.get("best") else 1.4,
                    color=ORANGE if base else (RED if s.get("best") else cmap(i / n * 0.85)),
                    label=s["label"] + (" (best fit)" if s.get("best") else ""), zorder=3 if s.get("best") else 2)
        if spec.get("measured"):
            m = sorted(spec["measured"])
            ax.plot([p[0] for p in m], [p[1] for p in m], "s", color="black", ms=6, mfc="white", mew=1.6,
                    label="Field readings (benchmark)", zorder=5)
        for d, lab in spec.get("stages") or []:
            ax.axvline(d, color="#c8d0c8", lw=0.8, zorder=0)
            ax.annotate(lab, (d, 1.0), xycoords=("data", "axes fraction"), rotation=90, fontsize=6.5, va="top",
                        ha="right", color="#56695c")
        ax.set_xlabel("Time (days)")
        ax.set_ylabel(f"{t['label']}{_unit(t)}")
        ax.legend(loc="best", fontsize=7.5)
    elif k == "profile":  # V26.1.9 (batch 2): the settlement trough / wall deflection along its line, one line per run
        t = spec["target"]
        cmap = plt.get_cmap("viridis")
        n = max(1, len(spec["series"]) - 1)
        wall = spec.get("along") == "wall"
        for i, s in enumerate(spec["series"]):
            pts = s["points"]
            if not pts:
                continue
            col = ORANGE if s.get("existing") else (RED if s.get("best") else cmap(i / n * 0.85))
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            ax.plot(ys, xs, "-", color=col, lw=2.4 if s.get("best") else 1.4, label=s["label"]) if wall else \
                ax.plot(xs, ys, "-", color=col, lw=2.4 if s.get("best") else 1.4, label=s["label"])
        if wall:
            ax.set_xlabel(f"{t['label'].split(' (')[0]}{_unit(t)}")
            ax.set_ylabel("Level (m)")
            ax.axvline(0, color="#9aa59c", lw=0.8)
        else:
            ax.set_xlabel("Distance x (m)")
            ax.set_ylabel(f"{t['label'].split(' (')[0]}{_unit(t)}")
            ax.axhline(0, color="#9aa59c", lw=0.8)
        ax.legend(loc="best", fontsize=7.5)
    elif k == "sensitivity":
        v, t = spec["var"], spec["target"]
        pts = sorted(spec["points"], key=lambda p: (str(p[0]) if isinstance(p[0], str) else p[0]))
        cat = any(isinstance(p[0], str) for p in pts)
        xs = list(range(len(pts))) if cat else [p[0] for p in pts]
        ys = [p[1] for p in pts]
        xl, yl = f"{v['label']}{'' if cat else _unit(v)}", f"{t['label']}{_unit(t)}"
        X, Y = (ys, xs) if spec.get("swap") else (xs, ys)
        ax.plot(X, Y, "-o", color=BLUE, lw=2, ms=6, mfc="white", mew=2, label="Runs", zorder=3)
        for (a, b, no) in zip(X, Y, [p[2] for p in pts]):
            ax.annotate(f"Run {no}", (a, b), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=7.5,
                        color="#56695c")
        if spec.get("base") and not cat and spec["base"][0] is not None:
            bx, by = spec["base"][0], spec["base"][1]
            ax.plot(*((by, bx) if spec.get("swap") else (bx, by)), "D", color=ORANGE, ms=8, label="Existing design",
                    zorder=4)
        ref, lo, hi = t.get("ref"), t.get("lo"), t.get("hi")
        line = ax.axvline if spec.get("swap") else ax.axhline
        span = ax.axvspan if spec.get("swap") else ax.axhspan
        if t.get("how") == "range" and lo is not None and hi is not None:
            span(lo, hi, color=GREEN, alpha=0.12, label=f"Control range {S.fmt(lo)} – {S.fmt(hi)}")
        elif ref is not None:
            lab = {"max": "Maximum allowed", "min": "Minimum required"}.get(t.get("how"), "Control value")
            line(ref, color=RED, ls="--", lw=1.4, label=f"{lab} {S.fmt(ref)}")
        if cat:
            labels = [p[0] for p in pts]
            (ax.set_yticks if spec.get("swap") else ax.set_xticks)(xs, labels)
        ax.set_xlabel(yl if spec.get("swap") else xl)
        ax.set_ylabel(xl if spec.get("swap") else yl)
        ax.legend(loc="best", fontsize=8.5)
    elif k == "achievement":
        rows = spec["rows"]
        xs = [str(r["run"]) for r in rows]
        ys = [r["achievement"] for r in rows]
        cols = [GREEN if y >= 100 else (ORANGE if y >= 80 else RED) for y in ys]
        bars = ax.bar(xs, ys, color=cols, width=0.65, zorder=3)
        if spec.get("best") is not None and str(spec["best"]) in xs:
            bars[xs.index(str(spec["best"]))].set_edgecolor(INK)
            bars[xs.index(str(spec["best"]))].set_linewidth(2)
        ax.axhline(100, color=INK, lw=1.2, ls="--")
        ax.text(len(xs) - 0.5, 101, "target met", ha="right", va="bottom", fontsize=8, color=INK)
        ax.set_ylim(0, 110)
        ax.set_xlabel("Run")
        ax.set_ylabel("Achievement of the targets (%)")
        _thin_ticks(ax, xs)
    elif k == "runs":
        t = spec["target"]
        xs = [str(a) for a, _ in spec["points"]]
        ys = [b for _, b in spec["points"]]
        cols = [BLUE] * len(xs)
        if spec.get("best") is not None and str(spec["best"]) in xs:
            cols[xs.index(str(spec["best"]))] = GREEN
        if xs and xs[0] == "0":
            cols[0] = GREY
        ax.bar(xs, ys, color=cols, width=0.65, zorder=3)
        if t.get("operator") not in (None, "report") and t.get("value") is not None:
            ax.axhline(t["value"], color=RED, ls="--", lw=1.4, label=f"Target {t['operator']} {S.fmt(t['value'])}")
            ax.legend(loc="best", fontsize=8.5)
        if spec.get("measured") is not None:
            ax.axhline(spec["measured"], color=ORANGE, ls="--", lw=1.6, label=f"Measured {S.fmt(spec['measured'])}")
            ax.legend(loc="best", fontsize=8.5)
        ax.set_xlabel("Scenario" if spec.get("scenario") else "Run")
        ax.set_ylabel(f"{t['label']}{_unit(t)}")
        _thin_ticks(ax, xs)
    elif k == "tornado":
        import textwrap
        ps = list(reversed(spec["params"]))
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(6.8, max(2.2, 0.5 * len(ps) + 1.0)))
        ys = range(len(ps))
        ax.barh(ys, [q["lo"] for q in ps], color=ORANGE, height=0.55, zorder=3, label="decrease")
        ax.barh(ys, [q["hi"] for q in ps], color=BLUE, height=0.55, zorder=3, label="increase")
        ax.set_yticks(list(ys), [textwrap.shorten(q["label"], 40) for q in ps])
        for i, q in enumerate(ps):
            if q["lo"] < 0:
                ax.annotate(f"{q['lo']:+.1f} % ({q['lo_x']})", (q["lo"], i), textcoords="offset points", xytext=(-4, 0),
                            ha="right", va="center", fontsize=7.5)
            if q["hi"] > 0:
                ax.annotate(f"{q['hi']:+.1f} % ({q['hi_x']})", (q["hi"], i), textcoords="offset points", xytext=(4, 0),
                            ha="left", va="center", fontsize=7.5)
        lim = max([abs(q["lo"]) for q in ps] + [abs(q["hi"]) for q in ps] + [1.0]) * 2.0
        ax.set_xlim(-lim, lim)
        ax.axvline(0, color=INK, lw=1)
        ax.set_xlabel("Change against the existing design (%)")
        ax.grid(axis="y", visible=False)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.3 if len(ps) > 2 else -0.5), ncol=2, fontsize=8)
    elif k == "spread":
        import numpy as np
        import textwrap
        items = spec["items"]
        names = list(dict.fromkeys(q["label"] for it in items for q in it["params"]))
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(6.8, 4.0))
        w = 0.8 / max(1, len(items))
        cols = [BLUE, ORANGE, GREEN, "#7b5ea7", RED, GREY]
        x = np.arange(len(names))
        for j, it in enumerate(items):
            d = {q["label"]: q["span"] for q in it["params"]}
            ax.bar(x + (j - (len(items) - 1) / 2) * w, [d.get(nm, 0) for nm in names], w * 0.92, color=cols[j % len(cols)],
                   label=textwrap.shorten(it["label"], 34), zorder=3)
        ax.set_xticks(x, [textwrap.fill(textwrap.shorten(nm, 34), 16) for nm in names], fontsize=8)
        ax.set_ylabel("Spread of the result (%)")
        ax.legend(fontsize=8)
    elif k == "rank":
        items = list(reversed(spec["items"]))
        import textwrap
        labels = [textwrap.shorten(a, 42) for a, _ in items]
        vals = [b for _, b in items]
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(6.8, max(2.2, 0.38 * len(items) + 0.9)))
        ax.barh(labels, vals, color=[BLUE if v >= 0 else ORANGE for v in vals], height=0.6, zorder=3)
        for i, v in enumerate(vals):
            ax.annotate(f"{v:+.1f} %" if min(vals) < 0 else f"{v:.1f} %", (v, i), textcoords="offset points",
                        xytext=(4 if v >= 0 else -4, 0), ha="left" if v >= 0 else "right", va="center", fontsize=8)
        ax.axvline(0, color=INK, lw=0.8)
        ax.set_xlabel("Change (%)")
        ax.grid(axis="y", visible=False)
    elif k == "misfit":
        rows = spec["rows"]
        xs = [str(r["run"]) for r in rows]
        ys = [r["misfit"] for r in rows]
        cols = [GREEN if str(spec.get("best")) == x else BLUE for x in xs]
        ax.bar(xs, ys, color=cols, width=0.65, zorder=3)
        ax.set_xlabel("Run")
        ax.set_ylabel("Misfit with the measurements (%)")
        _thin_ticks(ax, xs)
    elif k == "compare":
        items = spec["items"]
        n = len(items)
        plt.close(fig)
        fig, axes = plt.subplots(1, n, figsize=(max(3.2, 2.3 * n), 3.8), squeeze=False)
        for a, c in zip(axes[0], items):
            vals = [c["old"] or 0, c["new"] or 0]
            b = a.bar(["Existing", "Modified"], vals, color=[GREY, BLUE], width=0.6, zorder=3)
            for bar, val in zip(b, vals):
                a.annotate(S.fmt(float(val)), (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                           textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8)
            ch = c.get("change")
            import textwrap
            a.set_title(textwrap.fill(f"{c['label']}{_unit(c)}", 24) + (f"\n{ch:+.1f} %" if ch is not None else ""), fontsize=9)
        fig.tight_layout()  # the title is the caption under the figure (web page, Excel and Word)
        return _png(fig, plt)
    elif k == "compare_many":  # Rev B W1.5: existing design (grey) and every modification (blue, chosen darker)
        t, bars = spec["target"], spec["bars"]
        labels = ["Existing"] + [b[0] for b in bars]
        vals = [spec["existing"] if S._isnum(spec.get("existing")) else 0] + [float(b[1]) for b in bars]
        cols = [GREY] + [GREEN if b[2] else BLUE for b in bars]
        import textwrap
        b = ax.bar([textwrap.fill(x, 14) for x in labels], vals, color=cols, width=0.62, zorder=3)
        for bar, val in zip(b, vals):
            ax.annotate(S.fmt(float(val)), (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8)
        if S._isnum(spec.get("limit")):
            ax.axhline(float(spec["limit"]), color=RED, lw=1.3, ls="--", zorder=4, label=f"Criterion {t['operator']} {S.fmt(float(spec['limit']))}")
            ax.legend(loc="best", fontsize=8)
        ax.set_ylabel(f"{t['label']}{_unit(t)}")
        ax.grid(axis="x", visible=False)
        if len(labels) > 5:
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=8)
    elif k == "change_heat":  # Rev B W1.5: change % of every result in every modification (green improves, red worsens)
        rows, cols, vals = spec["rows"], spec["cols"], spec["values"]
        plt.close(fig)
        fig, ax = plt.subplots(figsize=(max(5.0, 1.25 * len(cols) + 3.2), max(1.8, 0.48 * len(rows) + 1.2)))
        import numpy as np
        z = np.array([[(-(v or 0.0) if low else (v or 0.0)) for v in row] for row, low in zip(vals, spec["lower"])], dtype=float)
        lim = max(1.0, float(np.nanmax(np.abs(z))) if z.size else 1.0)
        ax.imshow(z, cmap="RdYlGn", vmin=-lim, vmax=lim, aspect="auto")
        for i, row in enumerate(vals):
            for j, v in enumerate(row):
                strong = v is not None and abs(z[i][j]) > 0.6 * lim
                ax.text(j, i, "—" if v is None else f"{v:+.1f} %", ha="center", va="center", fontsize=8,
                        color="white" if strong else INK, fontweight="bold" if strong else "normal")
        import textwrap
        ax.set_xticks(range(len(cols)), [textwrap.fill(c, 14) for c in cols], fontsize=8)
        ax.set_yticks(range(len(rows)), [textwrap.fill(r, 34) for r in rows], fontsize=8)
        ax.grid(False)
        for sp in ax.spines.values():
            sp.set_visible(False)
    fig.tight_layout()
    return _png(fig, plt)


def _thin_ticks(ax, xs):
    if len(xs) > 25:
        step = -(-len(xs) // 20)
        ax.set_xticks(range(0, len(xs), step), [xs[i] for i in range(0, len(xs), step)])


def _png(fig, plt) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def figures(specs: list[dict]) -> list[dict]:
    out = []
    for i, sp in enumerate(specs, 1):
        try:
            png = figure(sp)
        except Exception as e:  # noqa: BLE001 (a figure that cannot be drawn is left out, not the results)
            out.append({"title": sp["title"], "error": str(e)})
            continue
        out.append({"title": f"Figure {i}. {sp['title']}", "png": png})
    return out


def figures_b64(figs: list[dict]) -> list[dict]:
    return [{"title": f["title"], "src": "data:image/png;base64," + base64.b64encode(f["png"]).decode()}
            for f in figs if f.get("png")]


# --- Excel ---------------------------------------------------------------------------------------------------
def excel(project: dict, task: dict, out: dict, runs_all: list[dict], figs: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    plan = task["plan"]
    m = (project.get("setup") or {}).get("plaxis_model") or {}
    head = PatternFill("solid", fgColor="1D6B43")
    hfont = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="C8D6CC")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    info = (project.get("setup") or {}).get("info") or {}
    ws["A1"] = f"{out['title']} – {info.get('title') or 'PLAXIS study'}"
    ws["A1"].font = Font(bold=True, size=14, color="1D6B43")
    meta = [("Project", info.get("title", "")), ("Engineer", info.get("user", "")),
            ("Date", info.get("date") or date.today().isoformat()), ("Study", out["title"]),
            ("PLAXIS", (plan.get("plaxis") or "").upper()), ("Runs", f"{len(runs_all)}"), ("Conclusion", out["text"])]
    for i, (k, v) in enumerate(meta, 3):
        ws.cell(i, 1, k).font = Font(bold=True)
        c = ws.cell(i, 2, v)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 110
    r0 = len(meta) + 5
    ws.cell(r0, 1, "Variable input parameters").font = Font(bold=True, size=12)
    hdr = ["Parameter", "PLAXIS reference", "Unit", "Existing", "Minimum / new", "Maximum"]
    _table(ws, r0 + 1, hdr, [[v["label"], v["plaxis_ref"], v.get("unit") or "", v.get("current") or "",
                              v.get("min") if v["kind"] == "range" else ", ".join(v.get("values") or []),
                              v.get("max") if v["kind"] == "range" else ""] for v in plan["variables"]],
           head, hfont, box)
    r1 = r0 + 3 + len(plan["variables"])
    ws.cell(r1, 1, "PLAXIS results").font = Font(bold=True, size=12)
    _table(ws, r1 + 1, ["Result", "Unit", "Phase", "Criterion"],
           [[t["label"], t["unit"], _phase_text(t, m), _crit(t)] for t in plan["targets"]], head, hfont, box)

    ws2 = wb.create_sheet("Runs")
    vs, ts = plan["variables"], plan["targets"]
    hdr = ["Run"] + [f"{v['label']}{_unit(v)}" for v in vs] + [f"{t['label']}{_unit(t)}" for t in ts] + ["Status"]
    if out["mode"] == "optimize":
        hdr.insert(len(hdr) - 1, "Achievement (%)")
    rows = []
    for r in runs_all:
        row = [r["no"]]
        for v in vs:
            val = (r.get("inputs") or {}).get(v["name"])
            row.append("existing" if val is None else (val[0] if isinstance(val, (list, tuple)) else val))
        for t in ts:
            val = (r.get("outputs") or {}).get(t["name"])
            row.append(val if isinstance(val, (int, float)) else "")
        if out["mode"] == "optimize":
            row.append(S.achievement(ts, r.get("outputs")) if r["status"] == "succeeded" else "")
        row.append(r["status"])
        rows.append(row)
    _table(ws2, 1, hdr, rows, head, hfont, box)
    for i in range(1, len(hdr) + 1):
        ws2.column_dimensions[get_column_letter(i)].width = 8 if i == 1 else 20
    ws2.freeze_panes = "B2"

    done = [c for c in out.get("modifications") or [] if "results" in c]
    if len(out.get("modifications") or []) > 1 and done:  # Rev B W1.6: one column per modification, then the ranking
        ws3 = wb.create_sheet("Modifications")
        labels = [x["label"] for x in done[0]["results"]]
        rows = []
        for lab in labels:
            t0 = next(x for x in done[0]["results"] if x["label"] == lab)
            row = [lab, t0["unit"], t0["old"]]
            for c in done:
                x = next((y for y in c["results"] if y["label"] == lab), None)
                row += [x["new"] if x else "", round(x["change"], 2) if x and x.get("change") is not None else ""]
            row.append(f"{t0['operator']} {S.fmt(float(t0['limit']))}" if t0["operator"] != "report" and S._isnum(t0.get("limit")) else "")
            rows.append(row)
        hdr3 = ["Result", "Unit", "Existing design"] + [h for c in done for h in (c["title"], "Change (%)")] + ["Criterion"]
        _table(ws3, 1, hdr3, rows, head, hfont, box)
        r0 = len(rows) + 3
        _table(ws3, r0, ["Rank", "Modification", "Run", "Package of changes", "Meets every criterion", "Governing margin (%)",
                         "Improves", "Worsens", "Chosen"],
               [[c["rank"], c["title"], c["run"], c["values"], {True: "yes", False: "no", None: "no criterion"}[c["meets"]],
                 c["margin"] if c.get("margin") is not None else "", c["improves"], c["worsens"], "★" if c["chosen"] else ""]
                for c in sorted(done, key=lambda c: c["rank"])], head, hfont, box)
        for i in range(1, len(hdr3) + 1):
            ws3.column_dimensions[get_column_letter(i)].width = 34 if i == 1 else 18
    elif out.get("comparison"):
        ws3 = wb.create_sheet("Old vs new")
        _table(ws3, 1, ["Result", "Unit", "Existing design", "Modified design", "Change (%)"],
               [[c["label"], c["unit"], c["old"], c["new"], round(c["change"], 2) if c.get("change") is not None else ""]
                for c in out["comparison"]], head, hfont, box)
        for col, w in zip("ABCDE", (34, 10, 18, 18, 14)):
            ws3.column_dimensions[col].width = w

    fr, sr = features_rows(task, m), stages_rows(runs_all)
    if fr or sr or model_objects(m):  # V26.1.9: the study's PLAXIS features and what each run set in the stages
        ws4 = wb.create_sheet("PLAXIS features")
        r = 1
        for title, hdr, rows in (("PLAXIS features of the study", ["Feature", "Setting"], fr),
                                 ("Other PLAXIS objects of the model", ["Object", "Type", "Details"], model_objects(m)),
                                 ("What each run set in the stages", ["Run", "Set in PLAXIS"], sr)):
            if not rows:
                continue
            ws4.cell(r, 1, title).font = Font(bold=True, size=12)
            _table(ws4, r + 1, hdr, rows, head, hfont, box)
            r += len(rows) + 4
        for col, w in zip("ABC", (34, 90, 60)):
            ws4.column_dimensions[col].width = w

    wsf = wb.create_sheet("Figures")
    row = 1
    for f in figs:
        if not f.get("png"):
            continue
        wsf.cell(row, 1, f["title"]).font = Font(bold=True)
        img = XLImage(io.BytesIO(f["png"]))
        img.width, img.height = 680, 400
        wsf.add_image(img, f"A{row + 1}")
        row += 23
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _table(ws, r, hdr, rows, fill, font, border):
    from openpyxl.styles import Alignment
    for j, h in enumerate(hdr, 1):
        c = ws.cell(r, j, h)
        c.fill, c.font, c.border = fill, font, border
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, row in enumerate(rows, r + 1):
        for j, v in enumerate(row, 1):
            c = ws.cell(i, j, v)
            c.border = border
            if isinstance(v, float):
                c.number_format = "0.000"


def _phase_text(t: dict, m: dict | None) -> str:
    """The phase a result is read in, as the report shows it (V26.1.9: the phases OPTIMUS adds have their own names)."""
    ph = t.get("phase") or ""
    ids = {x["name"]: x.get("id") or x["name"] for x in (m or {}).get("phases") or []}
    if ph.startswith("@safety:"):
        return f"safety phase after {ids.get(ph[8:], ph[8:])} (added)"
    if ph == "@dynamic":
        return "dynamic phase (added)"
    if ph == "@longterm":
        return "long term (added)"
    return t.get("phase_label") or ids.get(ph, ph) or "last phase"


def _crit(t: dict) -> str:
    if t.get("detector"):
        return "Sensitivity Detector"
    if t.get("how") in ("max", "min") and t.get("ref") is not None:
        return f"{'maximum' if t['how'] == 'max' else 'minimum'} {S.fmt(t['ref'])}"
    if t.get("measured") is not None:
        return f"measured {S.fmt(t['measured'])}"
    if t.get("how") == "range" and t.get("lo") is not None:
        return f"control range {S.fmt(t['lo'])} – {S.fmt(t['hi'])}"
    if t.get("ref") is not None:
        return f"control value {S.fmt(t['ref'])}"
    if t["operator"] == "report":
        return "reported"
    return f"{t['operator']} {S.fmt(t['value'])}"


# --- Word ----------------------------------------------------------------------------------------------------
VIS_DEFAULT = ["figures", "tables", "output", "geometry"]


def vis_of(plan: dict) -> list[str]:
    return list(((plan.get("study") or {}).get("report") or {}).get("vis") or VIS_DEFAULT)


def _soil_matrix(m: dict) -> tuple[list[str], list[list[str]]]:
    """The soil materials of the model: one column per material, one row per parameter."""
    soils = [x for x in m.get("materials") or [] if x.get("kind") == "soil"]
    keys = []
    for x in soils:
        for k in (x.get("props") or {}):
            if k not in keys:
                keys.append(k)
    hdr = ["Parameter"] + [f"{x['name']} ({x.get('model') or ''})".replace(" ()", "") for x in soils]
    rows = [["Material model"] + [x.get("model") or "—" for x in soils]] + \
           ([["Drainage type"] + [x.get("drainage") or "—" for x in soils]] if any(x.get("drainage") for x in soils) else []) + \
           [[SOIL_NAMES.get(k, k)] + [S.fmt(float(v)) if S._isnum(v) else (str(v) if v not in (None, "") else "—")
                                      for v in ((x.get("props") or {}).get(k) for x in soils)] for k in keys]
    return hdr, rows


SOIL_NAMES = {"gammaUnsat": "Unit weight γunsat [kN/m³]", "gammaSat": "Unit weight γsat [kN/m³]",
              "E50ref": "Secant stiffness E50,ref [kN/m²]", "Eoedref": "Oedometer stiffness Eoed,ref [kN/m²]",
              "Eurref": "Unloading stiffness Eur,ref [kN/m²]", "Eref": "Young's modulus E' [kN/m²]",
              "cref": "Cohesion c'ref [kN/m²]", "phi": "Friction angle φ' [°]", "psi": "Dilatancy angle ψ [°]",
              "nu": "Poisson's ratio ν'", "m": "Power m", "K0nc": "K0,nc", "Rinter": "Interface Rinter",
              "Gref": "Shear modulus Gref [kN/m²]", "kx": "Permeability kx [m/day]", "ky": "Permeability ky [m/day]"}


def plaxis_model_calc(t) -> str:
    from .plaxis_model import calc_type_name
    return calc_type_name(t) if t not in (None, "") else ""


def model_inputs(m: dict) -> dict:
    """The main inputs of the PLAXIS model, as tables for the report."""
    mats = {x["name"]: x for x in m.get("materials") or []}
    phl = {ph["name"]: ph.get("id") or ph["name"] for ph in m.get("phases") or []}
    staged = m.get("staged") or []
    act = {x["name"]: x.get("active") or [] for x in m.get("structures") or []}
    mine = lambda o, n: str(o or "") == n or str(o or "").startswith(n + "_")  # E2: SurfaceLoad_1 staged as SurfaceLoad_1_1
    stages = lambda n: ", ".join(dict.fromkeys([phl.get(x["phase"], x["phase"]) for x in staged if mine(x.get("object"), n)]
                                               + [phl.get(p, p) for p in act.get(n, [])])) or "—"
    structs = [[s["name"], s.get("kind") or "", s.get("material") or "⚠ none",
                "; ".join(f"{k} = {S.fmt(float(v)) if S._isnum(v) else v}" for k, v in ((mats.get(s.get("material")) or {}).get("props") or {}).items()) or "—",
                stages(s["name"])] for s in m.get("structures") or []]
    unit = {(x.get("object"), x.get("prop")): x.get("unit") or "" for x in staged}
    loads = [[ld["name"], (ld.get("kind") or "") + " load",
              "; ".join(f"{k} = {S.fmt(float(v))} {unit.get((ld['name'], k), '')}".strip() for k, v in (ld.get("props") or {}).items() if S._isnum(v)) or "⚠ no value",
              stages(ld["name"])] for ld in m.get("loads") or []]
    phases = [[str(i), ph.get("id") or ph["name"], ph["name"], plaxis_model_calc(ph.get("type"))] for i, ph in enumerate(m.get("phases") or [])]
    return {"soil": _soil_matrix(m), "structures": structs, "loads": loads, "phases": phases, "objects": model_objects(m),
            "mesh": m.get("mesh") or {}, "counts": m.get("counts") or {}, "checks": model_checks(m)}


def model_objects(m: dict) -> list[list[str]]:
    """V26.1.9: the other PLAXIS objects read from the model (interfaces, tunnels, contractions, wells, drains,
    prescribed displacements, soil clusters), as rows [object, type, details] for the report."""
    mats = {x["name"]: x for x in m.get("materials") or []}
    num = lambda v: S.fmt(float(v)) if S._isnum(v) else "—"
    rows = []
    for x in m.get("interfaces") or []:
        mat = mats.get(x.get("material")) or {}
        ri = (mat.get("props") or {}).get("Rinter")
        rows.append([x["name"], f"{x.get('side') or ''} interface".strip(), f"material {mat.get('id') or x.get('material') or 'from the adjacent soil'}"
                     + (f", Rinter {num(ri)}" if ri is not None else "") + (f", {x['mode']}" if x.get("mode") else "")])
    for x in m.get("tunnels") or []:
        rows.append([x["name"], "tunnel", "—"])
    for x in m.get("contractions") or []:
        rows.append([x["name"], "contraction", f"Cref {num(x.get('value'))} %"])
    for coll, lab, unit in (("wells", "well", "m³/day"), ("drains", "drain", "m")):
        for x in m.get(coll) or []:
            rows.append([x["name"], lab, f"{x.get('prop')} = {num(x.get('value'))} {unit}"])
    for x in m.get("displacements") or []:
        rows.append([x["name"], f"prescribed displacement ({x.get('kind') or ''})".replace(" ()", ""),
                     "; ".join(f"{k} = {num(v)} m" for k, v in (x.get("props") or {}).items()) or "—"])
    if m.get("clusters"):
        rows.append([f"{len(m['clusters'])} soil clusters", "soil clusters", ", ".join(str(c.get("name", "") if isinstance(c, dict) else c) for c in m["clusters"][:12])
                     + (" …" if len(m["clusters"]) > 12 else "")])
    return rows


def features_rows(task: dict, m: dict) -> list[list[str]]:
    """V26.1.9: the PLAXIS features of the study (safety phases, result points, new structures, dynamics)."""
    from . import features
    return features.describe(m or {}, (task.get("plan") or {}).get("study") or {})


def stages_rows(runs_all: list[dict]) -> list[list[str]]:
    """V26.1.9: what each run set in PLAXIS beyond the variables' values: the stage values (prestress, water, wells,
    new structures, added safety / dynamic phases) and the soil switches (drainage, interfaces, design approach)."""
    rows = []
    for r in runs_all:
        o = r.get("outputs") or {}
        for x in (o.get("_stages_applied") or []) + (o.get("_soil_models") or []):
            if x:
                rows.append([f"Run {r.get('no')}", str(x)])
    return rows


def model_checks(m: dict) -> list[str]:
    """What is missing or incomplete in the PLAXIS model (the same check as on the study page)."""
    out, staged = [], m.get("staged") or []
    mats = {x["name"] for x in m.get("materials") or []}
    if not m.get("loads"):
        out.append("No loads were read from the model.")
    for ld in m.get("loads") or []:
        vals = [v for v in (ld.get("props") or {}).values() if S._isnum(v)]
        if not vals:
            out.append(f"{ld['name']}: no load value was read.")
        elif all(float(v) == 0 for v in vals):
            out.append(f"{ld['name']}: every load value is 0.")
        if staged and not any(str(x.get("object") or "") == ld["name"] or str(x.get("object") or "").startswith(ld["name"] + "_")
                              for x in staged):  # re-QA N3: PLAXIS 3D stages the load as SurfaceLoad_1_1
            out.append(f"{ld['name']}: not active in any stage.")
    for s in m.get("structures") or []:
        if not s.get("material"):
            out.append(f"{s['name']} ({s.get('kind')}): no material assigned.")
        elif s["material"] not in mats:
            out.append(f"{s['name']}: its material {s['material']} was not read.")
    if not any(x.get("kind") == "soil" for x in m.get("materials") or []):
        out.append("No soil material was read from the model.")
    if len(m.get("phases") or []) < 2:
        out.append("Only the initial phase was read.")
    return out


def geometry_png(m: dict, study: dict | None = None) -> bytes | None:
    """The model geometry (front view): soil layers, structures, loads, water level, with the point names. V26.1.9:
    with the study's new structures (dashed) and result points."""
    if not m or not m.get("points"):
        return None
    plt = _plt()
    from matplotlib.patches import Polygon
    is3 = m.get("version") == "3d"
    up = 2 if is3 else 1
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=130)
    cols = ["#e7d3a3", "#cfb98a", "#b9a77f", "#d9c9a6", "#c4b393", "#e2d6b5"]
    mats = []
    for i, sp in enumerate(m.get("soil_polygons") or []):
        pts = [(p[0], p[up]) for p in sp.get("points") or []]
        if len(pts) >= 3:
            mat = sp.get("material") or ""
            if mat not in mats:
                mats.append(mat)
            ax.add_patch(Polygon(pts, closed=True, fc=cols[mats.index(mat) % len(cols)], ec="#7b6d55", lw=.6,
                                 label=mat if mats.count(mat) == 1 and mat and mat not in [h.get_label() for h in ax.patches] else None))
    kc = {"plate": "#37474f", "anchor": "#8d6e63", "geogrid": "#2e7d32", "embedded": "#6a1b9a"}
    for s in m.get("structures") or []:
        pts = s.get("points") or []
        if len(pts) >= 2:
            c = next((v for k, v in kc.items() if k in (s.get("kind") or "")), "#455a64")
            ax.plot([p[0] for p in pts], [p[up] for p in pts], color=c, lw=3 if s.get("kind") == "plate" else 2, solid_capstyle="round")
            mx, my = sum(p[0] for p in pts) / len(pts), sum(p[up] for p in pts) / len(pts)
            ax.annotate(s["name"], (mx, my), fontsize=7, color=c, xytext=(4, 4), textcoords="offset points")
    from . import features
    fp = features.points(m, study or {})
    for a in fp["added"]:
        c = next((v for k, v in kc.items() if k in a["kind"]), "#455a64")
        ax.plot([a["a"][0], a["b"][0]], [a["a"][up], a["b"][up]], "--", color=c, lw=2.2, label=None)
        ax.annotate(a["name"] + " (added)", ((a["a"][0] + a["b"][0]) / 2, (a["a"][up] + a["b"][up]) / 2), fontsize=7,
                    color=c, xytext=(4, -9), textcoords="offset points")
    for p in fp["probes"]:
        ax.plot([p["at"][0]], [p["at"][up]], marker="D", ms=5, color="#c2185b", ls="none")
        ax.annotate(p["name"], (p["at"][0], p["at"][up]), fontsize=7, color="#c2185b", xytext=(4, 4), textcoords="offset points")
    for w in [x for coll in ("wells", "drains") for x in m.get(coll) or []]:
        pts = w.get("points") or []
        if len(pts) >= 2:
            ax.plot([q[0] for q in pts], [q[up] for q in pts], ":", color="#0277bd", lw=1.6)
            ax.annotate(w["name"], (pts[0][0], pts[0][up]), fontsize=6.5, color="#0277bd", xytext=(3, 3), textcoords="offset points")
    xs = [p["x"] for p in m["points"]]
    ys = [p["z"] if is3 else p["y"] for p in m["points"]]
    span = (max(ys) - min(ys)) or 1
    for ld in m.get("loads") or []:
        pts = ld.get("points") or []
        if len(pts) >= 2:
            a, b = min(p[0] for p in pts), max(p[0] for p in pts)
            y = max(p[up] for p in pts)
            ax.plot([a, b], [y + span * .06] * 2, color="#d32f2f", lw=1.5)
            for k in range(6):
                x = a + (b - a) * k / 5
                ax.annotate("", (x, y), xytext=(x, y + span * .06), arrowprops={"arrowstyle": "->", "color": "#d32f2f", "lw": 1.1})
            v = next((S.fmt(float(x)) for x in (ld.get("props") or {}).values() if S._isnum(x)), "")
            ax.annotate(f"{ld['name']} {v}", ((a + b) / 2, y + span * .07), ha="center", fontsize=7, color="#d32f2f")
    w = m.get("water") or []
    for line in ((w.get("levels") or w.get("lines") or []) if isinstance(w, dict) else w)[:1]:
        pts = line.get("points") if isinstance(line, dict) else None
        if pts:
            ax.plot([p[0] for p in pts], [p[up] for p in pts], "--", color="#1565c0", lw=1.2, label="Water level")
    ax.set_xlim(min(xs) - 1, max(xs) + 1)
    ax.set_ylim(min(ys) - span * .04, max(ys) + span * .2)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("x [m]")
    ax.set_ylabel(("z" if is3 else "y") + " [m]")
    ax.set_title("Model geometry" + (" (front view)" if is3 else ""))
    ax.grid(False)
    if mats:
        ax.legend(loc="lower right", fontsize=7, frameon=True)
    return _png(fig, plt)


def _caption(d, text, kind="Figure"):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"{kind} {_caption.n[kind]}. ")
    r.bold = True
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(0x1D, 0x6B, 0x43)
    r2 = p.add_run(text)
    r2.italic = True
    r2.font.size = Pt(9)
    _caption.n[kind] += 1


def _field(par, code):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    r = par.add_run()
    for kind, text in (("begin", None), (None, code), ("end", None)):
        if kind:
            e = OxmlElement("w:fldChar")
            e.set(qn("w:fldCharType"), kind)
        else:
            e = OxmlElement("w:instrText")
            e.set(qn("xml:space"), "preserve")
            e.text = text
        r._r.append(e)


def word(project: dict, task: dict, out: dict, runs_all: list[dict], figs: list[dict]) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    plan = task["plan"]
    info = (project.get("setup") or {}).get("info") or {}
    d = Document()
    st = d.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    from docx.enum.text import WD_BREAK
    from pathlib import Path as _P
    _caption.n = {"Figure": 1, "Table": 1}
    vis = vis_of(plan)
    m = (project.get("setup") or {}).get("plaxis_model") or {}
    disc = "Structural" if info.get("discipline") == "structural" else "Geotechnical"
    GREEN = RGBColor(0x1D, 0x6B, 0x43)
    for hs, sz in (("Heading 1", 15), ("Heading 2", 12.5), ("Heading 3", 11)):
        try:
            d.styles[hs].font.color.rgb = GREEN if hs != "Heading 3" else RGBColor(0x2F, 0x5E, 0x48)
            d.styles[hs].font.size = Pt(sz)
        except KeyError:
            pass
    for s in d.sections:
        s.left_margin = s.right_margin = Inches(0.9)
        s.top_margin = s.bottom_margin = Inches(0.8)
        hp = s.header.paragraphs[0]
        hp.text = f"OPTIMUS®  ·  {info.get('title') or 'PLAXIS study'}  ·  {out['title']}"
        hp.runs[0].font.size, hp.runs[0].font.color.rgb = Pt(8), RGBColor(0x6B, 0x7A, 0x70)
        fp = s.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        fp.add_run(f"{disc} Engineering report · {info.get('date') or date.today().isoformat()} · Page ").font.size = Pt(8)
        _field(fp, "PAGE")
        fp.add_run(" of ").font.size = Pt(8)
        _field(fp, "NUMPAGES")
    # the cover page
    logo = _P(__file__).resolve().parent.parent / "static" / "img" / "optimus-256.png"
    for _ in range(3):
        d.add_paragraph()
    if logo.is_file():
        d.add_picture(str(logo), width=Inches(1.3))
        d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("OPTIMUS®")
    r.bold, r.font.size, r.font.color.rgb = True, Pt(30), RGBColor(0x14, 0x2B, 0x22)
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("ENGINEERING OPTIMIZATION ENGINE")
    r.font.size, r.font.color.rgb = Pt(10), RGBColor(0xB0, 0x7F, 0x00)
    for _ in range(2):
        d.add_paragraph()
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"{out['title']}")
    r.bold, r.font.size, r.font.color.rgb = True, Pt(22), GREEN
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(f"{disc} Engineering report")
    r.font.size = Pt(13)
    d.add_paragraph()
    _wtable(d, ["", ""], [["Project", info.get("title", "") or "—"], ["Engineer", info.get("user", "") or "—"],
                          ["Date", info.get("date") or date.today().isoformat()],
                          ["Discipline", f"{disc} Engineering"],
                          ["PLAXIS model", ", ".join(f["name"] for f in project.get("files") or []) or (m.get("name") or "—")],
                          ["PLAXIS", (plan.get("plaxis") or "").upper()],
                          ["Runs", f"{sum(1 for r in runs_all if r.get('status') == 'succeeded')} of {len(runs_all)} calculated"]], header=False)
    d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

    d.add_heading("1. Conclusion", 1)
    para = d.add_paragraph(out["text"])
    if out["mode"] == "optimize" and out.get("optimization"):
        o = out["optimization"]
        r = para.insert_paragraph_before(("OPTIMIZATION ACHIEVED" if o["achieved"] else
                                          f"OPTIMIZATION NOT ACHIEVED – {o['percent']:.0f} % of the targets")).runs
        r[0].bold = True
        r[0].font.color.rgb = RGBColor(0x2E, 0x8B, 0x57) if o["achieved"] else RGBColor(0xC0, 0x39, 0x2B)
    for x in key_findings(out, runs_all):
        d.add_paragraph(x, style="List Bullet")

    d.add_heading("2. Main inputs", 1)
    if m:
        mi = model_inputs(m)
        c = mi["counts"]
        d.add_paragraph(f"PLAXIS {(m.get('version') or plan.get('plaxis') or '').upper()} model"
                        f"{' ' + m['name'] if m.get('name') else ''}: {c.get('points', 0)} points, {c.get('structures', 0)} structures, "
                        f"{c.get('loads', 0)} loads, {c.get('materials', 0)} materials, {c.get('phases', 0)} phases (the Original File).")
        hdr, rows = mi["soil"]
        if rows:
            d.add_paragraph("2.1 Soil materials", style="Heading 3")
            _caption(d, "Soil parameters of the Original File.", "Table")
            _wtable(d, hdr, rows)
        if mi["structures"]:
            d.add_paragraph("2.2 Structures", style="Heading 3")
            _caption(d, "Structures, their material and the stages where they are active.", "Table")
            _wtable(d, ["Structure", "Type", "Material", "Material properties", "Active in"], mi["structures"])
        d.add_paragraph("2.3 Loads", style="Heading 3")
        if mi["loads"]:
            _caption(d, "Loads of the Original File.", "Table")
            _wtable(d, ["Load", "Type", "Value", "Active in"], mi["loads"])
        else:
            d.add_paragraph("No loads were read from the model.")
        if mi["phases"]:
            d.add_paragraph("2.4 Construction stages", style="Heading 3")
            _caption(d, "Calculation phases.", "Table")
            _wtable(d, ["#", "Stage", "PLAXIS phase", "Calculation type"], mi["phases"])
        if mi["objects"]:  # V26.1.9
            d.add_paragraph("2.5 Interfaces, tunnels, wells, drains and other objects", style="Heading 3")
            _caption(d, "Other PLAXIS objects of the Original File.", "Table")
            _wtable(d, ["Object", "Type", "Details"], mi["objects"])
        if mi["mesh"]:
            d.add_paragraph("Mesh: " + ", ".join(f"{k} {v}" for k, v in mi["mesh"].items() if not isinstance(v, (dict, list))))
        d.add_paragraph("2.6 Model check", style="Heading 3")
        if mi["checks"]:
            for x in mi["checks"]:
                d.add_paragraph(f"⚠ {x}", style="List Bullet")
        else:
            d.add_paragraph("Loads, structures and materials read: nothing missing.")
    else:
        d.add_paragraph("The PLAXIS model was not read: its inputs are not listed.")

    d.add_heading("3. Study definition", 1)
    d.add_paragraph("Variable input parameters", style="Heading 3")
    _wtable(d, ["Parameter", "Unit", "Existing", "Range / new value"],
            [[v["label"], v.get("unit") or "", str(v.get("current") or ""),
              (f"{S.fmt(v['min'])} – {S.fmt(v['max'])}" if v["kind"] == "range" else ", ".join(x.split(':')[0] for x in v.get("values") or []))]
             for v in plan["variables"]])
    d.add_paragraph("PLAXIS results", style="Heading 3")
    _wtable(d, ["Result", "Unit", "Phase", "Criterion"],
            [[t["label"], t["unit"], _phase_text(t, m), _crit(t)] for t in plan["targets"]])
    fr = features_rows(task, m)
    if fr:  # V26.1.9
        d.add_paragraph("PLAXIS features of the study", style="Heading 3")
        d.add_paragraph("Added by OPTIMUS in PLAXIS in every run (the Original File is not changed).")
        _wtable(d, ["Feature", "Setting"], fr)

    from . import project_files
    shots = []
    for r in runs_all:
        if r.get("status") == "succeeded" and r.get("task_id") is not None:
            for pl in project_files.plots_of(r["task_id"], r["idx"]):
                f = project_files.plot_file(r["task_id"], r["idx"], pl["name"])
                if f:
                    shots.append((r, pl, f))
    out_shots = [x for x in shots if x[1]["key"] != "mesh"]
    mesh = [x for x in shots if x[1]["key"] == "mesh"][:1]

    n = 4
    d.add_heading(f"{n}. Calculation quality and checks", 1)
    q = quality(plan, out, runs_all, m, len(out_shots), bool(mesh), vis)
    d.add_paragraph(q["text"])
    _caption(d, "Quality of the calculation: runs, checks and saved output.", "Table")
    _wtable(d, ["Check", "Result"], q["table"])
    if q["geo"]:
        d.add_paragraph("Geometry checks of the runs before PLAXIS", style="Heading 3")
        for x in q["geo"]:
            d.add_paragraph(x, style="List Bullet")
    if q["failed"]:
        d.add_paragraph("Failed runs", style="Heading 3")
        _caption(d, "Failed runs: the reason given by PLAXIS and the proposed solution.", "Table")
        _wtable(d, ["Run", "Parameter values", "Failure reason", "Proposed solution"], q["failed"])
    n += 1
    if out.get("calibration") and out["calibration"].get("best_run") is not None:
        c = out["calibration"]
        d.add_heading(f"{n}. Calibration against the measurements", 1)
        d.add_paragraph(f"Best fit: Run {c['best_run']} ({c['best_values']}), root mean square misfit "
                        f"{c['misfit']:.2f} %.")
        best = next((r for r in runs_all if r.get("no") == c["best_run"]), None)
        _wtable(d, ["Result", "Measured", "PLAXIS (best fit)", "Difference"],
                [[f"{t['label']}{_unit(t)}", S.fmt(t.get("measured")),
                  S.fmt(float((best["outputs"] or {}).get(t["name"]))) if best and S._isnum((best["outputs"] or {}).get(t["name"])) else "—",
                  (f"{((best['outputs'][t['name']] - t['measured']) / (abs(t['measured']) or 1) * 100):+.1f} %"
                   if best and t.get("measured") is not None and S._isnum((best["outputs"] or {}).get(t["name"])) else "—")]
                 for t in plan["targets"]])
        n += 1
    if out.get("summary"):
        d.add_heading(f"{n}. Sensitivity summary", 1)
        d.add_paragraph("For each control parameter, the effect of each variable parameter from its lowest to its "
                        "highest value, against the existing design (%), the most influential first.")
        for sm in out["summary"]:
            d.add_paragraph(f"{sm['label']}{_unit(sm)}" + (f" (existing design {S.fmt(sm['base'])})" if sm.get("base") is not None else ""),
                            style="Heading 3")
            _wtable(d, ["#", "Parameter", "Largest decrease", "Largest increase", "Spread"],
                    [[str(i), p["label"], f"{p['lo']:+.1f} % ({p['lo_x']}, Run {p['lo_run']})" if p["lo"] < 0 else "—",
                      f"{p['hi']:+.1f} % ({p['hi_x']}, Run {p['hi_run']})" if p["hi"] > 0 else "—", f"{p['span']:.1f} %"]
                     for i, p in enumerate(sm["params"], 1)])
        n += 1
    if out.get("detector"):
        d.add_heading(f"{n}. Sensitivity Detector: the most affected results", 1)
        d.add_paragraph("The PLAXIS results that change the most over the runs (spread = (maximum − minimum) relative "
                        "to the first calculated run), with the input that changes each one the most.")
        _wtable(d, ["#", "Result", "Spread", "Minimum (run)", "Maximum (run)", "Most effective input"],
                [[str(i), f"{x['label']}{_unit(x)}", f"{x['spread']:.1f} %", f"{S.fmt(x['min'])} (Run {x['min_run']})",
                  f"{S.fmt(x['max'])} (Run {x['max_run']})", x.get("driver") or "—"] for i, x in enumerate(out["detector"], 1)])
        n += 1
    if out.get("screen"):
        d.add_heading(f"{n}. Sensitivity Detector: the items that affect the control parameter most", 1)
        names = [c["label"] for c in out["screen"][0]["changes"]] if out["screen"] else []
        _wtable(d, ["#", "Item (changed alone)", "Run"] + names,
                [[str(i), x["label"], str(x["run"])] + [f"{c['change']:+.1f} %" for c in x["changes"]]
                 for i, x in enumerate(out["screen"], 1)])
        n += 1
    if out.get("stats"):
        d.add_heading(f"{n}. Statistics of the results", 1)
        d.add_paragraph(f"Over the {max(st['n'] for st in out['stats'])} calculated run(s). The spread is "
                        "(maximum − minimum) relative to the first calculated run.")
        _wtable(d, ["Result", "Minimum (run)", "Maximum (run)", "Mean", "Spread"],
                [[f"{st['label']}{_unit(st)}", f"{S.fmt(st['min'])} (Run {st['min_run']})",
                  f"{S.fmt(st['max'])} (Run {st['max_run']})", S.fmt(st["mean"]),
                  f"{st['spread']:.1f} %" if st.get("spread") is not None else "—"] for st in out["stats"]])
        n += 1
    many = [c for c in out.get("modifications") or []]
    if len(many) > 1:  # Rev B W1.6: the modifications, their comparison, the ranking and the chosen one
        done = [c for c in many if "results" in c]
        d.add_heading(f"{n}. The modifications", 1)
        d.add_paragraph("Each modification is one package of changes to the existing design (Run 0, the full run of the "
                        "Original File), calculated as one PLAXIS run.")
        _caption(d, "The modifications and their package of changes.", "Table")
        _wtable(d, ["Modification", "Run", "Package of changes", "Status"],
                [[c["title"], str(c["run"]), c["values"], c["status"]] for c in many])
        n += 1
        if done:
            ex = out.get("existing") or {}
            d.add_heading(f"{n}. Comparison of every modification with the existing design", 1)
            d.add_paragraph("Value of each result, its change against the existing design and ✓ / ✗ against its "
                            "acceptance criterion.")
            _caption(d, "Existing design and every modification.", "Table")
            labels = [x["label"] for x in done[0]["results"]]
            rows = []
            for lab in labels:
                t0 = next(x for x in done[0]["results"] if x["label"] == lab)
                o = t0["old"]
                row = [f"{lab}{_unit(t0)}", (S.fmt(float(o)) if S._isnum(o) else "—") + _ok(t0["meets_old"])]
                for c in done:
                    x = next((y for y in c["results"] if y["label"] == lab), None)
                    row.append("—" if not x else S.fmt(float(x["new"])) + _ok(x["meets_new"])
                               + (f" ({x['change']:+.1f} %)" if x.get("change") is not None else ""))
                row.append(f"{t0['operator']} {S.fmt(float(t0['limit']))}" if t0["operator"] != "report" and S._isnum(t0.get("limit")) else "none")
                rows.append(row)
            rows.append(["Effect", ""] + [f"improves {c['improves']} · worsens {c['worsens']}" for c in done] + [""])
            rows.append(["Governing margin · rank", (f"{ex['margin']:+.1f} %" if ex.get("margin") is not None else "—")]
                        + [(f"{c['margin']:+.1f} % · " if c.get("margin") is not None else "") + f"rank {c['rank']}" + (" ★" if c["chosen"] else "")
                           for c in done] + [""])
            _wtable(d, ["Result", "Existing (Run 0)"] + [c["title"] for c in done] + ["Criterion"], rows)
            n += 1
            d.add_heading(f"{n}. Ranking and the chosen modification", 1)
            v = out.get("verdict") or {}
            d.add_paragraph(f"Ranked by {out['rank_rule']['text']}. The chosen modification (★) is {v.get('chosen_title', '—')}: "
                            "it is the one handed over to Design scenarios as the base scenario.")
            _caption(d, "Ranking of the modifications.", "Table")
            _wtable(d, ["Rank", "Modification", "Meets every criterion", "Governing margin", "Change from the existing design"],
                    [[str(c["rank"]) + (" ★" if c["chosen"] else ""), c["title"],
                      {True: "yes", False: "no: " + ", ".join(c["failing"]), None: "no criterion"}[c["meets"]],
                      f"{c['margin']:+.1f} %" if c.get("margin") is not None else "—", f"{c['size'] * 100:.1f} %"]
                     for c in sorted(done, key=lambda c: c["rank"])])
            n += 1
    elif out.get("comparison"):
        d.add_heading(f"{n}. Existing and modified design", 1)
        _wtable(d, ["Result", "Existing design", "Modified design", "Change"],
                [[f"{c['label']}{_unit(c)}", S.fmt(c["old"]) if c["old"] is not None else "—", S.fmt(c["new"]),
                  f"{c['change']:+.1f} %" if c.get("change") is not None else "—"] for c in out["comparison"]])
        n += 1
    eff = out.get("effects") or []
    if eff:
        d.add_heading(f"{n}. Effect of each variable on the control parameters", 1)
        d.add_paragraph("Each control parameter is fitted with a linear model of the variable inputs on the "
                        f"{max(e['n'] for e in eff)} calculated run(s) (R² shows how well the model fits). The change is over the "
                        "full range of the variable, the share is its part of the total change, the direction is the way to move "
                        "it to reach the criterion and the value is the estimate that reaches the criterion with that variable alone.")
        for e in eff:
            d.add_paragraph(f"{e['label']}{(' [' + e['unit'] + ']') if e['unit'] else ''} · criterion {e['goal']}", style="Heading 3")
            d.add_paragraph(effect_text(e))
            _caption(d, f"Effect of the variable inputs on {e['label']}.", "Table")
            _wtable(d, ["#", "Variable input", "Change over its range", "Share", "To reach the criterion", "Value that reaches it"],
                    [[str(it["rank"]), it["label"] + (f" [{it['unit']}]" if it["unit"] else ""),
                      f"{S.fmt(it['change'])}" + (f" ({it['pct']:+.1f} %)" if it.get("pct") is not None else ""), f"{it['share']:.0f} %",
                      it["direction"] or "—",
                      (S.fmt(it["reach"]) + ("" if it["reach_in_range"] else " (outside the range)")) if it.get("reach") is not None else "—"]
                     for it in e["items"]])
        n += 1
    pr = out.get("proposals") or []
    if pr:
        d.add_heading(f"{n}. Proposed combinations for the next runs", 1)
        d.add_paragraph("From the runs already calculated, OPTIMUS puts the variable inputs together so that every control "
                        "parameter meets its criterion: with the least change from the Original File, with the largest safety "
                        "margin, and a balanced one. The values are estimates from the linear model above: run them in PLAXIS "
                        "(＋ Add to the runs) to confirm them.")
        vl = [x["label"] + (f" [{x['unit']}]" if x["unit"] else "") for x in pr[0]["inputs"]]
        tl = [x["label"] + (f" [{x['unit']}]" if x["unit"] else "") for x in pr[0]["predicted"]]
        _caption(d, "Proposed combinations and their estimated results.", "Table")
        _wtable(d, ["#", "Proposal"] + vl + [f"{t} (estimate)" for t in tl] + ["All criteria"],
                [[str(i), p["why"]] + [S.fmt(x["value"]) for x in p["inputs"]] +
                 [f"{S.fmt(x['value'])} {'✓' if x['ok'] else '✗'}" for x in p["predicted"]] + ["met" if p["meets"] else "not met"]
                 for i, p in enumerate(pr, 1)])
        n += 1
    ts0 = [t for t in plan["targets"] if (t.get("result") or "").lower() != "file"]
    base = next((r for r in runs_all if not (r.get("inputs") or {}) and r.get("status") == "succeeded"), None)
    if "tables" in vis and base and ts0 and len(many) <= 1:  # Rev B W1.6 / M7: not repeated after the comparison chapter
        d.add_heading(f"{n}. Comparison with the Original File", 1)
        d.add_paragraph("Each run against the Original File (Run 0): the value and its change in %.")
        _caption(d, "Results of every run and their change against the Original File.", "Table")
        rows = []
        for r in runs_all:
            if r.get("status") != "succeeded":
                continue
            row = [f"Run {r.get('no')}" + (" (Original File)" if r is base else "")]
            for t in ts0:
                v, b0 = (r.get("outputs") or {}).get(t["name"]), (base.get("outputs") or {}).get(t["name"])
                if not S._isnum(v):
                    row.append("—")
                elif r is base or not S._isnum(b0) or not float(b0):
                    row.append(S.fmt(float(v)))
                else:
                    row.append(f"{S.fmt(float(v))} ({(float(v) - float(b0)) / abs(float(b0)) * 100:+.1f} %)")
            rows.append(row)
        _wtable(d, ["Run"] + [f"{t['label']}{_unit(t)}" for t in ts0], rows)
        n += 1
    d.add_heading(f"{n}. Runs", 1)
    nf = sum(1 for r in out["rows"] if r["status"] == "failed")
    d.add_paragraph(f"{len(out['rows'])} run(s): {sum(1 for r in out['rows'] if r['status'] == 'succeeded')} calculated"
                    + (f", {nf} failed (reason and proposed solution in the table)" if nf else "")
                    + ". ✓ / ✗: the result meets / does not meet its criterion.")
    _caption(d, "The runs, their parameter values and their results.", "Table")
    if nf:
        _wtable(d, ["Run", "Parameter values", "Result", "Failure reason", "Proposed solution"],
                [[str(r["run"]), r["values"], r["result"] if r["status"] != "failed" else "failed",
                  r.get("reason", "") if r["status"] == "failed" else "", r.get("solution", "") if r["status"] == "failed" else ""]
                 for r in out["rows"]])
    else:
        _wtable(d, ["Run", "Parameter values", "Result"], [[str(r["run"]), r["values"], r["result"]] for r in out["rows"]])
    vs, ts = plan["variables"], plan["targets"]
    if runs_all and (vs or ts) and len(vs) + len(ts) <= 9:
        d.add_paragraph("Every input and result of every run", style="Heading 3")
        hdr = ["Run"] + [f"{v['label']}{_unit(v)}" for v in vs] + [f"{t['label']}{_unit(t)}" for t in ts]
        rows = []
        for r in runs_all:
            row = [str(r.get("no"))]
            for v in vs:
                val = (r.get("inputs") or {}).get(v["name"])
                val = val[0] if isinstance(val, (list, tuple)) else val
                row.append("existing" if val is None else (S.fmt(float(val)) if S._isnum(val) else str(val)))
            for t in ts:
                val = (r.get("outputs") or {}).get(t["name"])
                row.append(S.fmt(float(val)) if S._isnum(val) else r["status"])
            rows.append(row)
        _wtable(d, hdr, rows)
    sr = stages_rows(runs_all)
    if sr:  # V26.1.9: what each run set in the stages (kept in stages_applied.txt / soil_models.txt of the run)
        d.add_paragraph("What each run set in PLAXIS", style="Heading 3")
        _caption(d, "Stage values, added phases and structures, and soil switches set in each run.", "Table")
        _wtable(d, ["Run", "Set in PLAXIS"], sr[:400])
    n += 1
    def pic(data_or_path, cap):
        d.add_picture(io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else str(data_or_path), width=Inches(6.3))
        d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        _caption(d, cap)
    if "geometry" in vis and m:
        try:
            g = geometry_png(m, plan.get("study"))
        except Exception:  # noqa: BLE001
            g = None
        if g:
            d.add_heading(f"{n}. Model geometry", 1)
            pic(g, "Model geometry of the Original File (soil layers, structures and loads)"
                + (", with the study's new structures (dashed) and result points." if features_rows(task, m) else "."))
            n += 1
    if "figures" in vis and any(f.get("png") for f in figs):
        d.add_heading(f"{n}. Figures", 1)
        for f in figs:
            if f.get("png"):
                import re as _re
                pic(f["png"], _re.sub(r"^Figure \d+\.\s*", "", f["title"]))
        n += 1
    if "output" in vis and out_shots:
        d.add_heading(f"{n}. Model output (PLAXIS Output)", 1)
        d.add_paragraph("The contour plots saved from PLAXIS Output for each calculated run, in the last phase: "
                        + ", ".join(dict.fromkeys(pl["label"] for _, pl, _ in out_shots)) + ".")
        for r, pl, f in out_shots:
            pic(f, f"{pl['label']} · Run {r.get('no')}" + (" (Original File)" if not (r.get("inputs") or {}) else ""))
        n += 1
    if "mesh" in vis and mesh:
        d.add_heading(f"{n}. Model mesh", 1)
        ms = m.get("mesh") or {}
        d.add_paragraph("The finite element mesh of the Original File, from PLAXIS Output."
                        + (" Mesh settings: " + ", ".join(f"{k} {v}" for k, v in ms.items() if not isinstance(v, (dict, list))) + "." if ms else ""))
        pic(mesh[0][2], "Finite element mesh (PLAXIS Output).")
        n += 1
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _ok(b) -> str:
    return " ✓" if b is True else " ✗" if b is False else ""


def key_findings(out: dict, runs_all: list[dict]) -> list[str]:
    """The main findings, one line each, under the conclusion (page and Word)."""
    rows = out.get("rows") or []
    ok, bad = sum(r["status"] == "succeeded" for r in rows), sum(r["status"] == "failed" for r in rows)
    f = [f"{ok} of {len(rows)} run(s) calculated by PLAXIS" + (f"; {bad} failed (see Calculation quality and checks)." if bad else ".")]
    for e in out.get("effects") or []:
        if e["items"]:
            top = e["items"][0]
            f.append(f"{e['label']}: Original File {S.fmt(e['base'])}{(' ' + e['unit']) if e['unit'] else ''} "
                     f"({'meets' if e['met_base'] else 'does not meet'} {e['goal']}); the most effective input is {top['label']} "
                     f"({top['share']:.0f} % of the change" + (f", {top['direction']} it to reach the criterion" if top["direction"] else "") + ").")
    pr = out.get("proposals") or []
    if pr:
        p = pr[0]
        f.append(f"Proposed next run ({p['why'].lower()}): " + ", ".join(f"{x['label']} = {S.fmt(x['value'])}{(' ' + x['unit']) if x['unit'] else ''}" for x in p["inputs"])
                 + (" (estimated to meet every criterion)." if p["meets"] else " (closest to the criteria)."))
    for st in (out.get("stats") or [])[:3]:
        f.append(f"{st['label']}: from {S.fmt(st['min'])} (Run {st['min_run']}) to {S.fmt(st['max'])} (Run {st['max_run']})"
                 + (f", a spread of {st['spread']:.1f} %." if st.get("spread") is not None else "."))
    # QA V26.1.8: what each module concludes, and what PLAXIS did not give
    o = out.get("optimization") or {}
    for l in o.get("limits") or []:
        f.append(f"{l['target']} reaches its limit {l['operator']} {S.fmt(l['limit'])} at {l['var']} ≈ {S.fmt(l['x'])}"
                 f"{(' ' + l['var_unit']) if l['var_unit'] else ''} (interpolated between Run {l['between'][0]} and Run {l['between'][1]}).")
    for m in o.get("margins") or []:
        if m["margin"] is not None:
            f.append(f"Recommended run: {m['label']} margin {m['margin']:+.1f} % against its criterion.")
    c = out.get("calibration") or {}
    for r in c.get("residuals") or []:
        if r.get("diff") is not None:
            f.append(f"Best fit: {r['label']} calculated {S.fmt(r['calculated'])} against measured {S.fmt(r['measured'])} ({r['diff']:+.1f} %).")
    e = c.get("estimate") or {}
    if e.get("interpolated"):
        f.append(f"Calibrated value from the misfit curve: {e['var']} = {S.fmt(e['x'])}{(' ' + e['unit']) if e['unit'] else ''} (misfit ≈ {e['misfit']:.1f} %).")
    for g in out.get("governing") or []:
        f.append(f"Governing scenario for {g['label']}: Run {g['governing_run']} ({g['values']}) with {S.fmt(g['governing'])}{(' ' + g['unit']) if g['unit'] else ''}.")
    v = out.get("verdict") or {}
    if "improves" in v and v.get("total", 1) > 1:
        f.append(f"{v['passing']} of {v['calculated']} calculated modifications meet every acceptance criterion.")
        for c in sorted([c for c in out.get("modifications") or [] if c.get("rank")], key=lambda c: c["rank"]):
            f.append(f"Rank {c['rank']}{' ★ chosen' if c['chosen'] else ''}: {c['title']} ({c['values']}): {c['improves']} result(s) "
                     f"improve, {c['worsens']} worsen" + (f", governing margin {c['margin']:+.1f} %" if c.get("margin") is not None else "")
                     + ("; NOT met: " + ", ".join(c["failing"]) if c["meets"] is False else "") + ".")
    elif "improves" in v:
        f.append(f"Modification: {v['improves']} result(s) improve, {v['worsens']} worsen"
                 + ("; all acceptance criteria met." if v.get("meets") else "; NOT met: " + ", ".join(v["failing"]) + "." if v.get("meets") is False else "."))
    for m in out.get("missing") or []:
        f.append(f"Not read from PLAXIS: {m['label']} ({m['reason'][:100]}).")
    return f


def effect_text(e: dict) -> str:
    it = e["items"]
    if not it:
        return ""
    t = (f"In the Original File, {e['label']} is {S.fmt(e['base'])}{(' ' + e['unit']) if e['unit'] else ''}: the criterion "
         f"{e['goal']} is {'met' if e['met_base'] else 'not met'}. {it[0]['label']} has the largest effect "
         f"({it[0]['share']:.0f} % of the change)")
    if len(it) > 1:
        t += ", then " + ", ".join(f"{x['label']} ({x['share']:.0f} %)" for x in it[1:4])
    t += "."
    r = [x for x in it if x.get("reach") is not None and x["reach_in_range"]]
    if r and not e["met_base"]:
        t += " Alone, " + "; ".join(f"{x['label']} = {S.fmt(x['reach'])}" for x in r[:3]) + " reaches the criterion."
    return t + f" (R² = {e['r2']:.2f} on {e['n']} runs.)"


def quality(plan: dict, out: dict, runs_all: list[dict], m: dict, n_out: int, has_mesh: bool, vis: list) -> dict:
    """The chapter "Calculation quality and checks": runs, retries, geometry checks, model check, saved output."""
    from . import geocheck, geosel
    rows = out.get("rows") or []
    ok = sum(r["status"] == "succeeded" for r in rows)
    bad = [r for r in rows if r["status"] == "failed"]
    wait = sum(r["status"] in ("queued", "pending", "running", "stopped") for r in rows)
    tries = sum(int(r.get("attempts") or 0) for r in runs_all)
    geo, ngeo = [], 0
    for r in runs_all:
        try:
            cs = geocheck.check(m, geosel.displace(m, plan["variables"], r.get("inputs") or {})) if m and r.get("inputs") else []
        except Exception:  # noqa: BLE001
            cs = []
        ngeo += 1 if r.get("inputs") else 0
        geo += [f"{'⛔' if c['level'] == 'error' else '⚠'} Run {r.get('no')}: {c['text']}" for c in cs]
    mc = model_inputs(m)["checks"] if m else []
    out_txt = (f"{n_out} image(s) saved" if n_out else "not saved (the images come from PLAXIS Output during a PLAXIS run"
               + ("" if "output" in vis else "; choose Model Output before running") + ")")
    table = [["Runs calculated", f"{ok} of {len(rows)}"], ["Runs failed", str(len(bad)) + (" (reasons below)" if bad else "")],
             ["Runs awaiting", str(wait)], ["PLAXIS attempts (with the retries)", str(tries or ok + len(bad))],
             ["Geometry checks before PLAXIS", f"{len(geo)} finding(s) over {ngeo} changed run(s)" if geo else
              f"no overlapping lines, no item without length and no item turned over in the {ngeo} changed run(s)"],
             ["Model check of the Original File", f"{len(mc)} item(s) to check (section 2.5)" if mc else "loads, structures and materials complete"],
             ["PLAXIS Output images", out_txt], ["Mesh image", "saved" if has_mesh else "not saved"],
             ["Calculation", "PLAXIS " + ((plan.get("plaxis") or "").upper() or "")]]
    text = (f"{ok} of {len(rows)} run(s) were calculated by PLAXIS" + (f" and {len(bad)} failed" if bad else "") +
            (f"; {wait} are awaiting" if wait else "") + ". Before PLAXIS, the geometry of every changed run was checked for "
            "overlapping lines, items without length and items turned over" + (": see the findings below." if geo else ": nothing was found.") +
            (" The model check of the Original File lists items to look at." if mc else ""))
    failed = [[str(r["run"]), r["values"], r.get("reason", ""), r.get("solution", "")] for r in bad]
    return {"table": table, "text": text, "geo": geo, "failed": failed}


def _wtable(d, hdr, rows, header=True):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    tb = d.add_table(rows=(1 if header else 0) + len(rows), cols=len(hdr))
    tb.style = "Table Grid"
    if header:
        for j, h in enumerate(hdr):
            c = tb.rows[0].cells[j]
            c.text = h
            run = c.paragraphs[0].runs[0]
            run.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:fill"), "1D6B43")
            c._tc.get_or_add_tcPr().append(shd)
    for i, row in enumerate(rows, 1 if header else 0):
        for j, v in enumerate(row):
            cell = tb.rows[i].cells[j]
            cell.text = "" if v is None else str(v)
            for par in cell.paragraphs:
                for run in par.runs:
                    run.font.size = Pt(9.5)
                    if not header and j == 0:
                        run.bold = True
    d.add_paragraph()
