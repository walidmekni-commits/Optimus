"""Compiled results: a summary of every run and figures of each output against each parameter that changed."""
import math
from html import escape

from .planner import numeric, parse_value
from .sequence import run_no, scenario, short, value_text

GREEN_ORANGE = ["#1f8a4c", "#2f7fd0", "#2a6f97", "#7b5ea7", "#65a30d", "#c9a227", "#0f766e", "#a16207"]


def _x(v: dict, r: dict):
    """The parameter value used in a run (the existing value when the run did not change it)."""
    raw = (r.get("inputs") or {}).get(v["name"])
    if raw is None:
        raw = parse_value(v["current"]) if v.get("current") not in (None, "") else None
    if isinstance(raw, list):
        raw = tuple(raw)
    return raw


def _num_out(r, t):
    val = (r.get("outputs") or {}).get(t["name"])
    return float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None


def build(task: dict, runs: list[dict]) -> dict:
    plan = task["plan"]
    variables, targets = plan["variables"], plan["targets"]
    ok = [r for r in runs if r["status"] == "succeeded"]
    numeric_targets = [t for t in targets if t["result"].lower() != "file" and any(_num_out(r, t) is not None for r in ok)]
    changed = [v for v in variables if len({str(_x(v, r)) for r in runs}) > 1]
    rows = [{"run": run_no(r["idx"], plan), "title": f"Run {run_no(r['idx'], plan)}: {r['label']}",
             "values": {v["name"]: value_text(v, _x(v, r)) if _x(v, r) is not None else "" for v in variables},
             "outputs": {t["name"]: _num_out(r, t) for t in numeric_targets}, "passed": r.get("passed"),
             "inputs": r.get("inputs") or {},
             "status": r["status"]} for r in runs]
    stats, sens = [], []
    for t in numeric_targets:
        vals = [(r, _num_out(r, t)) for r in ok if _num_out(r, t) is not None]
        if not vals:
            continue
        lo = min(vals, key=lambda x: x[1])
        hi = max(vals, key=lambda x: x[1])
        mean = sum(v for _, v in vals) / len(vals)
        base = next((v for r, v in vals if not r.get("inputs")), None)
        stats.append({"name": t["name"], "label": t["label"], "unit": t.get("unit") or "", "n": len(vals),
                      "min": lo[1], "min_run": f"Run {run_no(lo[0]['idx'], plan)}: {lo[0]['label']}",
                      "max": hi[1], "max_run": f"Run {run_no(hi[0]['idx'], plan)}: {hi[0]['label']}",
                      "mean": mean, "baseline": base,
                      "criterion": "" if t["operator"] == "report" else f"{t['operator']} {t['value']:g}",
                      "passing": sum(1 for r, _ in vals if (r.get("checks") or {}).get(t["name"]) is True)})
        varied = [(r, val) for r, val in vals if r.get("inputs")] or vals  # the existing model is drawn apart
        for v in changed:
            groups = {}
            for r, val in varied:
                groups.setdefault(str(_x(v, r)), []).append(val)
            if len(groups) < 2:
                continue
            xs = sorted(groups, key=lambda k: _order(v, k))
            first, last = sum(groups[xs[0]]) / len(groups[xs[0]]), sum(groups[xs[-1]]) / len(groups[xs[-1]])
            pct = (last - first) / abs(first) * 100 if first else None
            sens.append({"output": t["name"], "variable": v["name"], "label": short(v), "from": _disp(v, xs[0]),
                         "to": _disp(v, xs[-1]), "out_from": first, "out_to": last, "change_pct": pct})
    from .report import DEFAULT_OUTPUT
    opts = {**DEFAULT_OUTPUT, **(plan.get("output") or {})}
    figures = []
    for t in numeric_targets:
        if opts["fig_runs"]:
            figures.append({"title": f"{t['label'] or t['name']} in every run", "svg": bar_chart(t, ok, plan)})
        for v in (changed if opts["fig_params"] else []):
            fig = line_chart(v, t, ok, variables, plan, lines=not opts["fig_points"])
            if fig:
                figures.append({"title": f"{t['label'] or t['name']} against {short(v)}", "svg": fig})
    text = _text(stats, sens, len(ok), len(runs))
    return {"rows": rows, "stats": stats, "sensitivity": sens, "figures": figures, "text": text,
            "variables": [{"name": v["name"], "label": short(v), "unit": v.get("unit") or ""} for v in variables],
            "outputs": [{"name": t["name"], "label": t["label"], "unit": t.get("unit") or ""} for t in numeric_targets]}


def _order(v, key: str):
    val = parse_value(key.strip("()'").split(",")[-1]) if key.startswith("(") else parse_value(key)
    n = numeric(val)
    if isinstance(val, str) and n is None:
        try:
            n = float(key)
        except ValueError:
            n = 0
    # mesh: coarse (large size) first
    return -n if (v.get("section") == "mesh") else n


def _disp(v, key: str):
    if key.startswith("("):
        lab = key.strip("()").split(",")[0].strip().strip("'\"")
        return f"{lab} mesh" if v.get("section") == "mesh" else lab
    try:
        key = f"{float(key):g}"
    except ValueError:
        pass
    return f"{key} {v.get('unit') or ''}".strip()


def _fmt(x):
    if x is None:
        return "–"
    return f"{x:.4g}"


def _text(stats, sens, ok, total):
    out = [f"{ok} of {total} run(s) finished."]
    for s in stats:
        line = (f"{s['label'] or s['name']}: from {_fmt(s['min'])} ({s['min_run']}) to {_fmt(s['max'])} ({s['max_run']}), "
                f"mean {_fmt(s['mean'])} {s['unit']}".rstrip())
        if s["criterion"]:
            line += f"; {s['passing']} of {s['n']} meet {s['criterion']}"
        out.append(line + ".")
        mine = sorted([x for x in sens if x["output"] == s["name"] and x["change_pct"] is not None],
                      key=lambda x: -abs(x["change_pct"]))
        if mine:
            top = mine[0]
            out.append(f"  Most influential on {s['label'] or s['name']}: {top['label']} ({top['from']} → {top['to']} changes it "
                       f"by {top['change_pct']:+.1f} %).")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------- SVG charts
def _nice(lo, hi, n=5):
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    a = math.floor(lo / step) * step
    b = math.ceil(hi / step) * step
    ticks, x = [], a
    while x <= b + step * 1e-6:
        ticks.append(round(x, 10))
        x += step
    return a, b, ticks


def _frame(W, H, L, R, T, B, ylo, yhi, yticks, ylabel, title):
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="Segoe UI, Arial, sans-serif" font-size="19">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>',
         f'<text x="{L}" y="30" font-weight="700" font-size="22" fill="#14301f">{escape(title)}</text>']
    Y = lambda y: T + (yhi - y) / (yhi - ylo) * (H - T - B)  # noqa: E731
    for t in yticks:
        o.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="#e3ece6"/>'
                 f'<text x="{L - 8}" y="{Y(t) + 4:.1f}" text-anchor="end" fill="#4b5b52">{t:g}</text>')
    o.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{H - B}" stroke="#4b5b52"/><line x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}" stroke="#4b5b52"/>')
    o.append(f'<text transform="translate(16 {(T + H - B) / 2:.0f}) rotate(-90)" text-anchor="middle" fill="#4b5b52">{escape(ylabel)}</text>')
    return o, Y


def _limit(o, t, Y, L, R, W, ylo, yhi):
    if t["operator"] != "report" and t.get("value") is not None and ylo <= t["value"] <= yhi:
        y = Y(t["value"])
        o.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="#c0362c" stroke-width="2" stroke-dasharray="9 5"/>'
                 f'<text x="{W - R}" y="{y - 5:.1f}" text-anchor="end" fill="#c0362c">criterion {escape(t["operator"])} {t["value"]:g}</text>')


def _yrange(vals, t):
    ys = list(vals)
    if t["operator"] != "report" and t.get("value") is not None:
        ys.append(t["value"])
    return _nice(min(ys), max(ys))


def bar_chart(t, runs, plan) -> str:
    pts = [(r, _num_out(r, t)) for r in runs if _num_out(r, t) is not None]
    if not pts:
        return ""
    W, H, L, R, T, B = 900, 480, 96, 24, 56, 104
    ylo, yhi, ticks = _yrange([v for _, v in pts], t)
    ylo = min(ylo, 0) if ylo >= 0 else ylo
    ylo, yhi, ticks = _nice(ylo, yhi)
    unit = f" ({t['unit']})" if t.get("unit") else ""
    o, Y = _frame(W, H, L, R, T, B, ylo, yhi, ticks, (t["label"] or t["name"]) + unit, f"{t['label'] or t['name']} in every run")
    n = len(pts)
    bw = (W - L - R) / n
    for i, (r, v) in enumerate(pts):
        x = L + i * bw
        passed = (r.get("checks") or {}).get(t["name"])
        c = "#1f8a4c" if passed is not False else "#2f7fd0"
        if not r.get("inputs"):
            c = "#7a8b80"
        y0, y1 = Y(max(ylo, 0) if ylo < 0 < yhi else ylo), Y(v)
        no = run_no(r["idx"], plan)
        o.append(f'<rect x="{x + bw * 0.12:.1f}" y="{min(y0, y1):.1f}" width="{bw * 0.76:.1f}" height="{abs(y1 - y0):.1f}" fill="{c}" rx="2">'
                 f'<title>Run {no}: {escape(r["label"])} → {v:.4g}</title></rect>')
        if n <= 40 or i % max(1, n // 30) == 0:
            o.append(f'<text x="{x + bw / 2:.1f}" y="{H - B + 24}" text-anchor="middle" font-size="16" fill="#4b5b52">{no}</text>')
    o.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - B + 52}" text-anchor="middle" fill="#4b5b52">Run (hover a bar for its scenario)</text>')
    o.append(f'<g font-size="17"><rect x="{L}" y="{H - 24}" width="16" height="16" fill="#1f8a4c"/><text x="{L + 22}" y="{H - 10}">meets the criteria / collected</text>'
             f'<rect x="{L + 300}" y="{H - 24}" width="16" height="16" fill="#2f7fd0"/><text x="{L + 322}" y="{H - 10}">does not meet</text>'
             f'<rect x="{L + 480}" y="{H - 24}" width="16" height="16" fill="#7a8b80"/><text x="{L + 502}" y="{H - 10}">existing model</text></g>')
    _limit(o, t, Y, L, R, W, ylo, yhi)
    o.append("</svg>")
    return "\n".join(o)


def line_chart(v, t, runs, variables, plan, lines: bool = True) -> str:
    """Output against one parameter. Runs that share the values of every other parameter form one line."""
    allpts = [(r, _x(v, r), _num_out(r, t)) for r in runs if _num_out(r, t) is not None and _x(v, r) is not None]
    base = [p for p in allpts if not p[0].get("inputs")]
    pts = [p for p in allpts if p[0].get("inputs")] or allpts
    if len({str(p[1]) for p in allpts}) < 2:
        return ""
    pts_x = pts + [p for p in base if p not in pts]
    is_list = any(isinstance(p[1], (tuple, str)) for p in pts)
    if is_list:
        cats = sorted({str(p[1]) for p in pts_x}, key=lambda k: _order(v, k))
        xpos = {c: i for i, c in enumerate(cats)}
        X0, X1 = -0.5, len(cats) - 0.5
        xticks = [(i, _disp(v, c)) for i, c in enumerate(cats)]
        xv = lambda p: xpos[str(p)]  # noqa: E731
    else:
        xs = [numeric(p[1]) for p in pts_x]
        X0, X1, tk = _nice(min(xs), max(xs))
        xticks = [(x, f"{x:g}") for x in tk]
        xv = lambda p: numeric(p)  # noqa: E731
    others = [o for o in variables if o["name"] != v["name"]]
    series = {}
    for r, xval, y in pts:
        key = scenario({o["name"]: (r.get("inputs") or {})[o["name"]] for o in others if o["name"] in (r.get("inputs") or {})}, others)
        series.setdefault(key, []).append((xv(xval), y, r))
    W, H, L, R, T, B = 900, 540, 96, 24, 56, 130
    ylo, yhi, yt = _yrange([p[2] for p in pts_x], t)
    unit = f" ({t['unit']})" if t.get("unit") else ""
    o, Y = _frame(W, H, L, R, T, B, ylo, yhi, yt, (t["label"] or t["name"]) + unit,
                  f"{t['label'] or t['name']} against {short(v)}")
    X = lambda x: L + (x - X0) / (X1 - X0) * (W - L - R)  # noqa: E731
    for x, lab in xticks:
        o.append(f'<text x="{X(x):.1f}" y="{H - B + 26}" text-anchor="middle" fill="#4b5b52">{escape(lab)}</text>')
    o.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - B + 56}" text-anchor="middle" fill="#14301f" font-weight="600">'
             f'{escape(short(v))}{(" (" + escape(v["unit"]) + ")") if v.get("unit") and not is_list else ""}</text>')
    keys = list(series)
    many = len(keys) > 8
    for i, k in enumerate(keys):
        c = GREEN_ORANGE[i % len(GREEN_ORANGE)] if not many else "#1f8a4c"
        s = sorted(series[k], key=lambda p: p[0])
        if len(s) > 1 and not many and lines:
            o.append(f'<polyline points="{" ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b, _ in s)}" fill="none" stroke="{c}" stroke-width="3"/>')
        for a, b, r in s:
            o.append(f'<circle cx="{X(a):.1f}" cy="{Y(b):.1f}" r="6" fill="{c}" stroke="#fff" stroke-width="1.5">'
                     f'<title>Run {run_no(r["idx"], plan)}: {escape(r["label"])} → {b:.4g}</title></circle>')
    if many:  # too many lines: show the mean at each value instead
        by = {}
        for k in keys:
            for a, b, _ in series[k]:
                by.setdefault(a, []).append(b)
        mean = sorted((a, sum(bs) / len(bs)) for a, bs in by.items())
        o.append('' if not lines else f'<polyline points="{" ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in mean)}" fill="none" stroke="#2f7fd0" stroke-width="4"/>')
        o.append(f'<text x="{L}" y="{H - 12}" font-size="16" fill="#4b5b52">Dots: every run. Orange line: mean at each value ({len(keys)} combinations of the other parameters).</text>')
    else:
        lx = L
        for i, k in enumerate(keys[:8]):
            c = GREEN_ORANGE[i % len(GREEN_ORANGE)]
            lab = k if len(keys) > 1 else "all runs"
            lab = lab if len(lab) < 46 else lab[:44] + "…"
            o.append(f'<rect x="{lx}" y="{H - 28}" width="16" height="16" fill="{c}"/>'
                     f'<text x="{lx + 22}" y="{H - 14}" font-size="16">{escape(lab)}</text>')
            lx += 40 + 9 * len(lab)
            if lx > W - 200:
                break
    for r, xval, y in base:  # the existing model: a grey diamond, outside the lines and the mean
        if pts is not allpts:
            cx, cy = X(xv(xval)), Y(y)
            o.append(f'<path d="M{cx:.1f} {cy - 9:.1f} l9 9 l-9 9 l-9 -9z" fill="#7a8b80" stroke="#fff" stroke-width="1.5">'
                     f'<title>Run {run_no(r["idx"], plan)}: existing model → {y:.4g}</title></path>')
            right = cx > W - R - 90
            o.append(f'<text x="{cx - 12 if right else cx + 12:.1f}" y="{cy - 8:.1f}" font-size="15" fill="#56695c"'
                     f'{" text-anchor=\"end\"" if right else ""}>existing</text>')
    _limit(o, t, Y, L, R, W, ylo, yhi)
    o.append("</svg>")
    return "\n".join(o)


def html(c: dict) -> str:
    """The compiled results as an HTML block (used in the report)."""
    h = ["<h3>Summary</h3>", "<p>" + "<br>".join(escape(x) for x in c["text"].splitlines()) + "</p>"]
    if c["stats"]:
        h.append("<table><tr><th>Output</th><th>Lowest</th><th>Highest</th><th>Mean</th><th>Existing model</th><th>Criterion</th></tr>")
        for s in c["stats"]:
            h.append(f"<tr><td>{escape(s['label'] or s['name'])} {escape(s['unit'])}</td><td>{_fmt(s['min'])}<br><small>{escape(s['min_run'])}</small></td>"
                     f"<td>{_fmt(s['max'])}<br><small>{escape(s['max_run'])}</small></td><td>{_fmt(s['mean'])}</td>"
                     f"<td>{_fmt(s['baseline'])}</td><td>{escape(s['criterion'] or 'collected')}"
                     f"{(' – ' + str(s['passing']) + ' of ' + str(s['n']) + ' pass') if s['criterion'] else ''}</td></tr>")
        h.append("</table>")
    if c["sensitivity"]:
        h.append("<h3>Effect of each changed parameter</h3><table><tr><th>Output</th><th>Parameter</th><th>From → to</th>"
                 "<th>Output (mean) from → to</th><th>Change</th></tr>")
        for s in c["sensitivity"]:
            h.append(f"<tr><td>{escape(s['output'])}</td><td>{escape(s['label'])}</td><td>{escape(s['from'])} → {escape(s['to'])}</td>"
                     f"<td>{_fmt(s['out_from'])} → {_fmt(s['out_to'])}</td>"
                     f"<td>{'' if s['change_pct'] is None else format(s['change_pct'], '+.1f') + ' %'}</td></tr>")
        h.append("</table>")
    for i, f in enumerate(c["figures"], 1):
        h.append(f'<div class="fig">{f["svg"]}</div><div class="cap">Chart {i}: {escape(f["title"])}</div>')
    return "\n".join(h)
