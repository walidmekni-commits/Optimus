"""Design report: one printable HTML page per task (open it in the browser and use Print › Save as PDF)."""
from html import escape as e

from . import design, geometry
from .schema import OBJECTIVES, SECTIONS, norm_section
from .sequence import COMBINE

DEFAULT_OUTPUT = {
    "fig_runs": True,         # Figures: each output in every run (bars)
    "fig_params": True,       # Figures: each output against each parameter that changed
    "fig_points": False,      # Figures: points only (no lines between the runs)
    "results_csv": True,      # Results: table of every run (CSV)
    "run_files": False,       # Results: PLAXIS project file of every run (zip)
    "report": True,           # Report: printable design report
    "report_geometry": True,  # Report includes the cross-section drawings
    "report_runs": True,      # Report includes the table of every run
}


def output_options(setup: dict) -> dict:
    return {**DEFAULT_OUTPUT, **(setup.get("output") or {})}


def _crit(t: dict) -> str:
    if t["operator"] == "report":
        return "collected"
    return f"{t['operator']} {t['value']:g} {t.get('unit') or ''}".strip() if t.get("value") is not None else t["operator"]


def _range(v: dict) -> str:
    if v.get("kind") == "values":
        return ", ".join(v.get("values") or [])
    parts = [f"{geometry.fmt(v['min'])} to {geometry.fmt(v['max'])}" if v.get("min") is not None and v.get("max") is not None else ""]
    if v.get("step"):
        parts.append(f"step {geometry.fmt(v['step'])}")
    return " ".join(p for p in parts if p)


def build(project: dict, task: dict, runs: list[dict]) -> str:
    setup = project["setup"] or {}
    info = setup.get("info") or {}
    opts = output_options(task["plan"] or setup)
    plan = task["plan"]
    variables, targets = plan["variables"], plan["targets"]
    result = task.get("result") or {}
    best = result.get("best_run")
    title = info.get("title") or "PLAXIS design"

    h = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)} – design report</title>
<style>
 body {{ font: 13px/1.45 Arial, Helvetica, sans-serif; color:#000; background:#fff; margin:0; }}
 .page {{ max-width: 1000px; margin: 0 auto; padding: 28px 32px; }}
 h1 {{ font-size: 22px; margin: 0 0 4px; }} h2 {{ font-size: 15px; margin: 26px 0 8px; border-bottom: 1.5px solid #000; padding-bottom: 3px; }}
 h3 {{ font-size: 13px; margin: 14px 0 6px; }}
 table {{ border-collapse: collapse; width: 100%; margin: 4px 0 8px; }}
 th, td {{ border: 1px solid #999; padding: 4px 6px; text-align: left; vertical-align: top; }}
 th {{ background: #eee; }}
 tr.best td {{ font-weight: bold; }}
 .meta td:first-child {{ width: 160px; font-weight: bold; background: #f5f5f5; }}
 .fig {{ border: 1px solid #000; margin: 6px 0 4px; overflow: auto; }} .fig svg {{ max-width: 100%; height: auto; display: block; }}
 .cap {{ font-size: 12px; margin-bottom: 14px; }}
 .bar {{ position: sticky; top: 0; background: #fff; border-bottom: 1px solid #ccc; padding: 8px 32px; text-align: right; }}
 .bar button {{ font: inherit; padding: 5px 14px; cursor: pointer; }}
 @media print {{ .bar {{ display: none; }} .page {{ padding: 0; }} h2 {{ break-after: avoid; }} .fig, table {{ break-inside: avoid; }} }}
</style></head><body>
<div class="bar"><button onclick="window.print()">Print / Save as PDF</button></div><div class="page">
<h1>{e(title)}</h1><div>Design report · {e(OBJECTIVES[task['objective']]['title'])}</div>
<h2>1. Project</h2><table class="meta">
<tr><td>Project</td><td>{e(title)}</td></tr><tr><td>User</td><td>{e(info.get('user', ''))}</td></tr>
<tr><td>Date</td><td>{e(info.get('date', ''))}</td></tr>
<tr><td>Objective</td><td>{e(OBJECTIVES[task['objective']]['title'])}</td></tr>
<tr><td>Input model</td><td>{e(', '.join(f['name'] for f in project.get('files') or []))}</td></tr>
<tr><td>Task</td><td>{e(task['summary'])}</td></tr>
<tr><td>Status</td><td>{e(task['status'])}{(' – ' + e(result.get('text', ''))) if result.get('text') else ''}</td></tr></table>"""]

    n = 2
    h.append(f"<h2>{n}. Variables</h2>")
    for key, label in SECTIONS.items():
        rows = [v for v in variables if norm_section(v.get("section"), v.get("plaxis_ref") or "") == key]
        if not rows:
            continue
        h.append(f"<h3>{e(label)}</h3><table><tr><th>Name</th><th>Parameter</th><th>Unit</th><th>PLAXIS ref</th>"
                 "<th>Range / values</th><th>Current / new</th></tr>")
        h += [f"<tr><td>{e(v['name'])}</td><td>{e(v['label'])}</td><td>{e(v['unit'])}</td><td>{e(v['plaxis_ref'])}</td>"
              f"<td>{e(_range(v))}</td><td>{e(v.get('current') or '')}</td></tr>" for v in rows]
        h.append("</table>")

    sections = setup.get("sections") or []
    if opts["report_geometry"] and sections:
        n += 1
        h.append(f"<h2>{n}. Geometry: cross sections</h2>")
        for i, s in enumerate(sections, 1):
            h.append(f'<div class="fig">{design.svg(s["design"], s["name"], s.get("y")) if s.get("design") else geometry.svg(s)}</div><div class="cap">Figure {i}: {e(s["name"])} '
                     f'({e(s.get("file", ""))}). Width {geometry.fmt(s["width"])} m, depth {geometry.fmt(s["depth"])} m.</div>')
            h.append("<table><tr><th>Node</th><th>x (m)</th><th>y (m)</th><th>z, depth (m)</th></tr>" +
                     "".join(f"<tr><td>{j}</td><td>{geometry.fmt(x)}</td><td>{geometry.fmt(s.get('y') or 0)}</td>"
                             f"<td>{geometry.fmt(z)}</td></tr>" for j, (x, z) in enumerate(s["nodes"], 1)) + "</table>")

    n += 1
    h.append(f"<h2>{n}. Target outputs</h2><table><tr><th>Name</th><th>Output</th><th>Result</th><th>Phase</th>"
             "<th>Criterion</th></tr>")
    h += [f"<tr><td>{e(t['name'])}</td><td>{e(t['label'])}</td><td>{e(t['result'])}</td><td>{e(t.get('phase') or 'last')}</td>"
          f"<td>{e(_crit(t))}</td></tr>" for t in targets]
    h.append("</table>")
    if plan.get("goal"):
        h.append(f"<p>Goal: {e(plan['goal']['direction'])} {e(plan['goal']['of'])} among designs that meet every target.</p>")

    n += 1
    h.append(f"<h2>{n}. Results</h2>")
    if best is not None:
        r = next((r for r in runs if r["idx"] == best), None)
        if r:
            h.append(f"<h3>Selected design: {e(r.get('title') or r['label'])}</h3><table><tr><th>Item</th><th>Value</th></tr>" +
                     "".join(f"<tr><td>{e(v['name'])} – {e(v['label'])}</td><td>{e(r['inputs_display'].get(v['name'], 'existing'))} "
                             f"{e(v['unit'])}</td></tr>" for v in variables) +
                     "".join(f"<tr><td>{e(t['name'])} – {e(t['label'])}</td><td>{e(_out(r, t))} {e(t.get('unit') or '')}</td></tr>"
                             for t in targets) + "</table>")
    elif task["status"] == "succeeded":
        h.append("<p>No design met every target.</p>")
    ok = sum(1 for r in runs if r["passed"] == 1)
    h.append(f"<p>{len(runs)} run(s), {ok} meet every criterion"
             f"{', ' + str(sum(r['status'] == 'failed' for r in runs)) + ' failed' if any(r['status'] == 'failed' for r in runs) else ''}.</p>")
    if opts["report_runs"]:
        h.append("<h3>All runs</h3><table><tr><th>Run</th><th>Scenario</th>" + "".join(f"<th>{e(v['name'])}</th>" for v in variables) +
                 "".join(f"<th>{e(t['name'])} {e(_crit(t)) if t['operator'] != 'report' else ''}</th>" for t in targets) +
                 "<th>Meets criteria</th><th>Status</th></tr>")
        for r in runs:
            h.append(f"<tr class=\"{'best' if r['idx'] == best else ''}\"><td>{r.get('run_no', r['idx'] + 1)}{' ★' if r['idx'] == best else ''}</td>"
                     f"<td>{e(r['label'])}</td>" +
                     "".join(f"<td>{e(r['inputs_display'].get(v['name'], 'existing'))}</td>" for v in variables) +
                     "".join(f"<td>{e(_out(r, t))}</td>" for t in targets) +
                     f"<td>{ {1: 'yes', 0: 'no'}.get(r['passed'], '') }</td><td>{e(r['status'])}</td></tr>")
        h.append("</table>")
    if plan.get("sequence"):
        seq = plan["sequence"]
        n += 1
        h.append(f"<h2>{n}. Compiled scenarios</h2><p>Runs per criterion: " +
                 ", ".join(f"{e(SECTIONS.get(k, k))} {c['runs']} ({'logical' if c['mode'] == 'logical' else 'customized'})"
                           for k, c in seq.get("criteria", {}).items()) +
                 f". Criteria combined: {e(COMBINE.get(seq.get('combine'), ''))}.</p><table><tr><th>Run</th>" +
                 "".join(f"<th>{e(v['label'] or v['name'])}{(' (' + e(v['unit']) + ')') if v.get('unit') else ''}</th>" for v in variables) +
                 "</tr>")
        for r in runs:
            h.append(f"<tr><td>{e(r.get('title') or r['label'])}</td>" +
                     "".join(f"<td>{e(r['inputs_display'].get(v['name'], 'existing'))}</td>" for v in variables) + "</tr>")
        h.append("</table>")
    if task["status"] in ("succeeded", "failed") and any(r["status"] == "succeeded" for r in runs):
        from . import compiled
        n += 1
        rs = [{**r, "inputs": {k: tuple(v) if isinstance(v, list) else v for k, v in (r["inputs"] or {}).items()}} for r in runs]
        h.append(f"<h2>{n}. Compiled results</h2>" + compiled.html(compiled.build(task, rs)))
    h.append("<p style='margin-top:30px;font-size:11px'>Generated by PLAXIS AI Design Automation.</p></div></body></html>")
    return "\n".join(h)


def _out(r: dict, t: dict) -> str:
    v = (r.get("outputs") or {}).get(t["name"])
    if v is None:
        return ""
    if t["result"].lower() == "file":
        return "saved"
    return f"{v:.4g}" if isinstance(v, float) else str(v)
