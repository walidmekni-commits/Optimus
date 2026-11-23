"""End-to-end tests of the workflow paths, using demo AI + simulated solver (no API key, no PLAXIS)."""
import io
import os

os.environ.update({"AI_MODE": "demo", "SOLVER_BACKEND": "simulated", "RETRY_DELAY_SECONDS": "0",
                   "MAX_RETRIES": "3", "USER_EMAIL": "user@example.com", "ADMIN_EMAIL": "admin@example.com"})
os.environ.pop("SMTP_HOST", None)

import pytest
from fastapi.testclient import TestClient

from app import db, main, notify, solvers


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SIM_FAIL_RATE", "0")
    db.reset(str(tmp_path / "t.db"))
    notify.OUTBOX.clear()
    main.RUN_IN_BACKGROUND = False
    return TestClient(main.app)


def new_project(client, objective):
    r = client.post("/api/projects", data={"objective": objective, "title": "Zayed wall", "user": "Walid",
                                           "date": "2026-10-31", "source": "upload"},
                    files=[("files", ("wall.p3d", b"fake", "application/octet-stream"))])
    assert r.status_code == 200, r.text
    return r.json()["project"]["id"]


def events(client, pid):
    return [e["event"] for e in reversed(client.get(f"/api/log?project_id={pid}").json())]


def test_plan_without_setup_asks_for_parameters(client):
    pid = new_project(client, "optimize")
    r = client.post(f"/api/projects/{pid}/plan").json()
    assert r["type"] == "clarify" and "variable" in r["message"]


def test_optimize_end_to_end(client):
    pid = new_project(client, "optimize")
    r = client.post(f"/api/projects/{pid}/chat", data={"message": "Optimize the wall, FoS above 1.5"}).json()
    assert r["type"] == "ready" and len(r["setup"]["variables"]) == 3
    plan = client.post(f"/api/projects/{pid}/plan").json()
    assert plan["type"] == "task_summary"
    tid = plan["task"]["id"]
    assert plan["task"]["progress"]["total"] == 1 + 5 * 3 * 3  # existing design + grid
    client.post(f"/api/tasks/{tid}/approve")
    t = client.get(f"/api/tasks/{tid}").json()
    assert t["status"] == "succeeded"
    best = t["runs"][t["result"]["best_run"]]
    assert best["passed"] == 1
    # best = lowest wall thickness among passing designs
    passing = [r for r in t["runs"] if r["passed"] == 1 and r["inputs"]]
    assert best["inputs"]["x1"] == min(r["inputs"]["x1"] for r in passing)
    ev = events(client, pid)
    for e in ("request", "parameters_identified", "task_definition", "approval", "attempt", "result"):
        assert e in ev
    csv = client.get(f"/api/tasks/{tid}/results.csv").text
    lines = csv.splitlines()
    assert lines[0] == "Project,Zayed wall" and lines[5].startswith("run,label,x1,x2,x3,T1,T2")


def test_combinations_collect_files(client):
    pid = new_project(client, "combinations")
    client.post(f"/api/projects/{pid}/chat", data={"message": "run all load combinations"})
    tid = client.post(f"/api/projects/{pid}/plan").json()["task"]["id"]
    client.post(f"/api/tasks/{tid}/approve")
    t = client.get(f"/api/tasks/{tid}").json()
    assert t["status"] == "succeeded" and len(t["runs"]) == 6 and all(r["has_file"] for r in t["runs"])
    assert t["runs"][0]["inputs_display"]["x1"] == "LC1 (1)"
    z = client.get(f"/api/tasks/{tid}/files.zip")
    assert z.status_code == 200 and len(z.content) > 100


def test_design_change_compares_with_existing(client):
    pid = new_project(client, "design_change")
    client.post(f"/api/projects/{pid}/chat", data={"message": "make the wall 0.6 m"})
    tid = client.post(f"/api/projects/{pid}/plan").json()["task"]["id"]
    client.post(f"/api/tasks/{tid}/approve")
    t = client.get(f"/api/tasks/{tid}").json()
    assert [r["label"] for r in t["runs"]] == ["Existing design", "Changed design"]
    assert "Existing → changed" in t["result"]["text"]


def test_customize_tables_by_hand(client):
    pid = new_project(client, "new_design")
    setup = {"variables": [{"name": "x1", "label": "Pile length", "unit": "m", "plaxis_ref": "EmbeddedBeam_1.L",
                            "kind": "range", "min": "10", "max": "20", "step": "5"}],
             "targets": [{"name": "T1", "label": "FoS", "result": "FoS", "operator": ">=", "value": "1.3"}],
             "goal": {"direction": "minimize", "of": "x1"}}
    s = client.put(f"/api/projects/{pid}/setup", json=setup).json()
    assert s["variables"][0]["min"] == 10.0
    assert "customize" in events(client, pid)
    plan = client.post(f"/api/projects/{pid}/plan").json()
    assert plan["task"]["progress"]["total"] == 3


def test_failures_retry_then_email_then_retry_button(client, monkeypatch):
    pid = new_project(client, "design_change")
    client.post(f"/api/projects/{pid}/chat", data={"message": "go"})
    tid = client.post(f"/api/projects/{pid}/plan").json()["task"]["id"]

    calls = {"n": 0}
    real = solvers.SimulatedSolver.run

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] <= 5:  # run 1: 4 attempts fail; run 2: attempt 1 fails, attempt 2 passes
            raise solvers.SolverError("did not converge")
        return real(self, *a, **k)

    monkeypatch.setattr(solvers.SimulatedSolver, "run", flaky)
    client.post(f"/api/tasks/{tid}/approve")
    t = client.get(f"/api/tasks/{tid}").json()
    assert t["status"] == "failed"
    assert t["runs"][0]["status"] == "failed" and t["runs"][0]["attempts"] == 4
    assert t["runs"][1]["status"] == "succeeded" and t["runs"][1]["attempts"] == 2
    assert notify.OUTBOX[-1]["to"] == ["user@example.com", "admin@example.com"]
    assert events(client, pid)[-1] == "error_alert"

    client.post(f"/api/tasks/{tid}/retry")  # only the failed run is re-run
    t = client.get(f"/api/tasks/{tid}").json()
    assert t["status"] == "succeeded" and t["runs"][0]["attempts"] == 5 and t["runs"][1]["attempts"] == 2


def test_cancel_and_edit(client):
    pid = new_project(client, "optimize")
    client.post(f"/api/projects/{pid}/chat", data={"message": "x"})
    tid = client.post(f"/api/projects/{pid}/plan").json()["task"]["id"]
    assert client.post(f"/api/tasks/{tid}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/tasks/{tid}/approve").status_code == 409
    tid2 = client.post(f"/api/projects/{pid}/plan").json()["task"]["id"]
    assert client.post(f"/api/tasks/{tid2}/edit").json()["status"] == "replaced"


def test_too_many_runs_is_refused(client, monkeypatch):
    monkeypatch.setenv("MAX_RUNS", "4")
    pid = new_project(client, "combinations")
    client.post(f"/api/projects/{pid}/chat", data={"message": "x"})  # 3 x 2 = 6 combinations
    r = client.post(f"/api/projects/{pid}/plan").json()
    assert r["type"] == "clarify" and "limit" in r["message"]


def test_title_required_and_info_saved(client):
    r = client.post("/api/projects", data={"objective": "optimize", "source": "upload"})
    assert r.status_code == 400
    pid = new_project(client, "optimize")
    info = client.get(f"/api/projects/{pid}").json()["project"]["setup"]["info"]
    assert info == {"title": "Zayed wall", "user": "Walid", "date": "2026-10-31", "discipline": "geotechnical"}


def test_local_file_path_source(client, tmp_path):
    f = tmp_path / "Zayed_SS2.p3d"
    f.write_text("x")
    r = client.post("/api/projects", data={"objective": "optimize", "title": "T", "source": "path",
                                           "local_path": f'"{f}"'})
    assert r.status_code == 200 and r.json()["project"]["files"][0]["source"] == "path"
    bad = client.post("/api/projects", data={"objective": "optimize", "title": "T", "source": "path",
                                             "local_path": "C:/nope.p3d"})
    assert bad.status_code == 400


def test_settings_roundtrip_and_test_without_plaxis(client):
    s = client.put("/api/settings", json={"solver": "plaxis", "host": "localhost", "port": 10000,
                                          "password": "secret"}).json()
    assert s["solver"] == "plaxis" and s["password_set"] and "password" not in s
    s = client.put("/api/settings", json={"password": ""}).json()
    assert s["password_set"]  # blank keeps the saved one
    r = client.post("/api/settings/test").json()
    assert r["ok"] is False and "plxscripting" in r["message"]
    client.put("/api/settings", json={"solver": "simulated"})


def test_offline_assistant_without_api_key(client, monkeypatch):
    monkeypatch.setenv("AI_MODE", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert client.get("/api/objectives").json()["ai_mode"] == "offline"
    pid = new_project(client, "optimize")
    r = client.post(f"/api/projects/{pid}/chat", data={"message": (
        "I want the height of the wall as variable from 9 to 12 m step 0.5 and the moment at the top of "
        "the wall to be less than 100 kN.m. Factor of safety above 1.5")}).json()
    v, t = r["setup"]["variables"], r["setup"]["targets"]
    assert [x["label"] for x in v] == ["Wall height (top level)"] and (v[0]["min"], v[0]["max"], v[0]["step"]) == (9, 12, 0.5)
    assert t[0]["result"] == "Plate.M11 | Z = max" and t[0]["operator"] == "<" and t[0]["value"] == 100
    assert t[1]["result"] == "FoS" and t[1]["value"] == 1.5
    # the wall height has no PLAXIS points yet -> asks
    assert r["type"] == "clarify" and "points" in r["message"]
    r = client.post(f"/api/projects/{pid}/chat", data={"message": "remove the factor of safety"}).json()
    assert [x["result"] for x in r["setup"]["targets"]] == ["Plate.M11 | Z = max"]
    s = client.put("/api/settings", json={"anthropic_api_key": "sk-ant-test"}).json()
    assert s["api_key_set"] and "anthropic_api_key" not in s
    assert client.get("/api/objectives").json()["ai_mode"] == "claude"


def test_offline_csv_upload(client, monkeypatch):
    monkeypatch.setenv("AI_MODE", "")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pid = new_project(client, "combinations")
    csv = b"name,label,unit,plaxis_ref,min,max,step\nx1,Surcharge,kPa,SurfaceLoad_1.qz,10,30,10\n"
    r = client.post(f"/api/projects/{pid}/chat", data={"section": "variables"},
                    files=[("files", ("vars.csv", csv, "text/csv"))]).json()
    assert r["setup"]["variables"][0]["plaxis_ref"] == "SurfaceLoad_1.qz" and r["setup"]["variables"][0]["max"] == 30


def test_location_filter_parsing():
    assert solvers._parse_filter("Z >= 10.5") == ("Z", ">=", 10.5)
    assert solvers._parse_filter(" z = max") == ("Z", "=", "max")


def r_secs(client, pid):
    return client.get(f"/api/projects/{pid}").json()["project"]["setup"]["sections"]


def test_cross_section_upload_drawing_and_report(client):
    from pathlib import Path
    pid = new_project(client, "new_design")
    dxf = (Path(__file__).resolve().parent.parent / "samples" / "cross_sections_example.dxf").read_bytes()
    r = client.post(f"/api/projects/{pid}/sections", files=[("files", ("sec.dxf", dxf, "application/dxf")),
                                                            ("files", ("photo.png", b"\x89PNG", "image/png"))]).json()
    assert r["added"] == ["sec 1", "sec 2"] and len(r["errors"]) == 1  # two sections on the sheet; images need AI
    s = r["sections"][0]
    # origin at the upper left, depth positive downwards
    assert [0.0, 8.0] in s["nodes"] and [42.0, 20.0] in s["nodes"] and [29.6, 0.0] in s["nodes"]
    assert min(n[1] for n in s["nodes"]) == 0 and min(n[0] for n in s["nodes"]) == 0
    # layers: soil boxes thin, wall and anchor thick
    kinds = {tuple(sorted((tuple(s["nodes"][a]), tuple(s["nodes"][b])))): t for a, b, t in s["segments"]}
    assert kinds[((30.4, 2.0), (37.0, 5.5))] == "structure" and kinds[((0.0, 14.0), (42.0, 14.0))] == "soil"
    svg = client.get(f"/api/projects/{pid}/sections/{s['id']}.svg").text
    assert "(42, 0, 20)" in svg and ">29.6<" in svg  # node label and a horizontal spacing
    # live edit: move a node, the spacing follows
    nodes = [list(n) for n in s["nodes"]]
    nodes[nodes.index([42.0, 20.0])] = [45.0, 20.0]
    e = client.put(f"/api/projects/{pid}/sections/{s['id']}/geometry",
                   json={"nodes": nodes, "segments": s["segments"]}).json()
    assert "(45, 0, 20)" in e["svg"] and e["section"]["edited"] and e["section"]["width"] == 45
    # scale / layers redraw from the file
    e = client.patch(f"/api/projects/{pid}/sections/{s['id']}", json={"groups": {"ANCHOR": False}, "y": 5}).json()
    assert not any(t == "structure" and 37.0 in (e["section"]["nodes"][a][0], e["section"]["nodes"][b][0])
                   for a, b, t in e["section"]["segments"]) and "(42, 5, 20)" in e["svg"]
    # variables go under the section they belong to
    client.put(f"/api/projects/{pid}/setup", json={"variables": [
        {"name": "x1", "label": "Wall thickness", "plaxis_ref": "Plate_1.d", "min": 0.6, "max": 1.0, "step": 0.2},
        {"name": "x2", "label": "Friction angle", "plaxis_ref": "Clay.phi", "min": 25, "max": 35, "step": 5}],
        "targets": [{"name": "T1", "label": "FoS", "result": "FoS", "operator": ">", "value": 1.2}], "goal": None})
    vs = client.get(f"/api/projects/{pid}").json()["project"]["setup"]["variables"]
    assert [v["section"] for v in vs] == ["structures", "soil"]
    client.put(f"/api/projects/{pid}/output", json={"run_files": True, "report_runs": False})
    # the engineer confirms every section's geometry before anything runs
    r = client.post(f"/api/projects/{pid}/plan").json()
    assert r["type"] == "clarify" and "Confirm the geometry" in r["message"] and "sec 1" in r["message"]
    for sec in r_secs(client, pid):
        assert client.post(f"/api/projects/{pid}/sections/{sec['id']}/confirm",
                           json={"checks": ["scale", "levels"]}).json()["section"]["confirmed"]
    tid = client.post(f"/api/projects/{pid}/plan").json()["task"]["id"]
    assert client.get(f"/api/tasks/{tid}").json()["plan"]["output"]["run_files"] is True
    client.post(f"/api/tasks/{tid}/approve")
    html = client.get(f"/api/tasks/{tid}/report.html").text
    assert "Geometry: cross sections" in html and "<svg" in html and "Soil" in html and "All runs" not in html


def test_pdf_import_is_off_by_default_cad_only(client):
    """PDF, scans, pictures and SVG are set aside: they are refused with a reason, CAD and CSV still open."""
    from pathlib import Path
    samples = Path(__file__).resolve().parent.parent / "samples"
    assert client.get("/api/objectives").json()["pdf_import"] is False
    pid = new_project(client, "new_design")
    pdf = (samples / "cross_sections_example.pdf").read_bytes()
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
    r = client.post(f"/api/projects/{pid}/sections", files=[("files", ("Zayed.pdf", pdf, "application/pdf")),
                                                            ("files", ("noext", pdf, "application/octet-stream")),
                                                            ("files", ("scan.png", png, "image/png"))]).json()
    assert r["sections"] == [] and len(r["errors"]) == 3
    assert all("switched off" in e and "DXF" in e for e in r["errors"])
    dxf = (samples / "cross_sections_example.dxf").read_bytes()
    csv = b"x,depth\n0,0\n10,0\n\n0,5\n10,5\n"
    r = client.post(f"/api/projects/{pid}/sections", files=[("files", ("sec.dxf", dxf, "application/dxf")),
                                                            ("files", ("sec.csv", csv, "text/csv"))]).json()
    assert r["errors"] == [] and len(r["sections"]) >= 2


def test_cad_needs_no_cad_software(client, monkeypatch):
    """DXF and CSV are read in Python alone; DWG without a converter is refused with advice; the CSV template downloads."""
    from pathlib import Path
    from app import geometry
    monkeypatch.setattr(geometry, "_find", lambda pats: None)  # a laptop with no CAD and no converter
    monkeypatch.setattr(geometry, "_has_ezdwg", lambda: False)  # ... and the DWG reader not installed yet
    assert client.get("/api/objectives").json()["dwg_converter"] is None
    pid = new_project(client, "new_design")
    dwg = b"AC1032" + b"\0" * 64
    r = client.post(f"/api/projects/{pid}/sections", files=[("files", ("sec.dwg", dwg, "application/acad"))]).json()
    assert r["sections"] == [] and "DXF copy" in r["errors"][0] and "run.bat" in r["errors"][0]
    t = client.get("/api/samples/cross_section.csv")
    assert t.status_code == 200 and t.text.startswith("line,x,level")
    r = client.post(f"/api/projects/{pid}/sections", files=[("files", ("t.csv", t.content, "text/csv"))]).json()
    assert r["errors"] == [] and len(r["sections"]) == 1


def test_dwg_opens_with_the_built_in_reader(client, tmp_path):
    """DWG is read by ezdwg (a pip package): no CAD, no ODA converter."""
    ezdwg = pytest.importorskip("ezdwg")
    from app import geometry
    assert geometry.dwg_converter() == "the built-in DWG reader (ezdwg)"
    d = ezdwg.new()
    m = d.modelspace()
    for a, b in [((0, 0), (40, 0)), ((40, 0), (40, -20)), ((40, -20), (0, -20)), ((0, -20), (0, 0)),
                 ((0, -8), (40, -8)), ((20, 0), (20, -12))]:
        m.add_line(a, b)
    d.save(str(tmp_path / "wall.dwg"))
    pid = new_project(client, "new_design")
    r = client.post(f"/api/projects/{pid}/sections",
                    files=[("files", ("wall.dwg", (tmp_path / "wall.dwg").read_bytes(), "application/acad"))]).json()
    assert r["errors"] == [] and len(r["sections"]) == 1 and r["sections"][0]["width"] == 40


def test_big_drawing_keeps_the_sections_only(tmp_path):
    """A real-world CAD file: a busy site plan, a row of sheets with two sections each, and a stray object kilometres
    away. The stray object and the plan are left out and every section opens at its own size."""
    ezdxf = pytest.importorskip("ezdxf")
    from app import geometry
    doc = ezdxf.new()
    m = doc.modelspace()

    def box(x, y, w, h, layer):
        m.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=True, dxfattribs={"layer": layer})
    for k in range(4):  # four sheets 100 m apart, each a frame with two 40 x 18 m sections
        x = k * 100
        box(x, 0, 60, 50, "frame")
        for y in (4, 28):
            box(x + 5, y, 40, 18, "soil")
            for z in (3, 6, 9, 12, 15):  # soil layers
                m.add_line((x + 5, y + z), (x + 45, y + z), dxfattribs={"layer": "soil"})
            m.add_line((x + 30, y + 4), (x + 30, y + 18), dxfattribs={"layer": "wall"})
            for z in (14, 11, 8):  # anchors
                m.add_line((x + 30, y + z), (x + 40, y + z - 4), dxfattribs={"layer": "anchor"})
    for i in range(6000):  # site plan: a long chain of short lines
        m.add_line((-2000 + i * 0.3, 300), (-2000 + (i + 1) * 0.3, 300 + (i % 2)), dxfattribs={"layer": "plan"})
    m.add_line((5e5, 5e5), (5e5 + 10, 5e5), dxfattribs={"layer": "xref"})
    doc.saveas(tmp_path / "job.dxf")
    d = geometry.read_file(tmp_path / "job.dxf")
    sizes = sorted({(round(max(p[0] for r in s["raw"] for p in r[0]) - min(p[0] for r in s["raw"] for p in r[0])),
                     round(max(p[1] for r in s["raw"] for p in r[0]) - min(p[1] for r in s["raw"] for p in r[0])))
                    for s in d})
    assert len(d) >= 4 and all(w <= 60 and h <= 50 for w, h in sizes), sizes
    assert len(d) == 8 and all(g != "plan" for s in d for *_x, g in s["raw"])
    assert any("far away" in w for w in d[0]["warnings"])


def test_pdf_cross_sections_offline(client, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("PDF_IMPORT", "1")  # the PDF reader is kept, switched off by default
    assert client.get("/api/objectives").json()["pdf_import"] is True
    pid = new_project(client, "new_design")
    pdf = (Path(__file__).resolve().parent.parent / "samples" / "cross_sections_example.pdf").read_bytes()
    r = client.post(f"/api/projects/{pid}/sections", files=[("files", ("Zayed.pdf", pdf, "application/pdf"))]).json()
    # 4 sections on the sheet; frame, title block, letters drawn as strokes, borehole logs and dimensions left out
    assert r["errors"] == [] and len(r["sections"]) == 4
    assert [s["y"] for s in r["sections"]] == [0, 45, 120, 0]  # chainages written next to the sections
    assert [s["chainage_from"] for s in r["sections"]] == ["drawing", "drawing", "drawing", None]
    s = r["sections"][1]
    assert s["scale"] == 200 and s["width"] == 45 and s["depth"] == 25  # scale 1:200 read from the title block
    assert s["groups"]["line weight 0.09"] is False  # hatching off
    kinds = {t for *_ab, t in s["segments"]}
    assert kinds == {"soil", "structure", "water"}
    walls = [(s["nodes"][a], s["nodes"][b]) for a, b, t in s["segments"] if t == "structure"]
    assert ([29.6, 0.0], [30.4, 0.0]) in walls
    # PLAXIS model geometry: closed box, nodes where the layer crosses the wall, soil layers per side
    m = client.patch(f"/api/projects/{pid}/sections/{s['id']}", json={"model": True}).json()["section"]
    assert m["model"] and [29.6, 12.0] in m["nodes"] and [30.4, 12.0] in m["nodes"]
    assert m["layers"]["left"] == [0, 10, 12, 17, 25] and m["layers"]["right"] == [0, 12, 17, 25]
    e = client.patch(f"/api/projects/{pid}/sections/{s['id']}", json={"scale": 100}).json()
    assert e["section"]["width"] == 22.5 and not e["section"]["model"]


def test_csv_cross_section_reader():
    from app import geometry
    polys, depth, _ = geometry.read_table("x,depth\n0,0\n10,0\n\n0,5\n10,5\n")
    s = geometry.simplify(polys, depth)
    assert s["nodes"] == [[0.0, 0.0], [10.0, 0.0], [0.0, 5.0], [10.0, 5.0]] and len(s["segments"]) == 2


def test_any_drawing_opens_offline(tmp_path):
    """Scanned PDF (picture only), a picture, a file without extension and an SVG all open as sections."""
    from pathlib import Path

    import pytest
    fitz = pytest.importorskip("fitz")
    from app import geometry
    sample = Path(__file__).resolve().parent.parent / "samples" / "cross_sections_example.pdf"
    png = tmp_path / "scan.png"
    fitz.open(str(sample))[0].get_pixmap(dpi=120).save(str(png))
    scan = fitz.open()
    page = scan.new_page(width=2384, height=1684)
    page.insert_image(page.rect, filename=str(png))
    scan.save(str(tmp_path / "scan.pdf"))
    noext = tmp_path / "drawing"
    noext.write_bytes(sample.read_bytes())
    svg = tmp_path / "s.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><line x1="0" y1="0" x2="100" y2="0"/>'
                   '<polyline points="0,0 0,50 100,50 100,0"/><line x1="0" y1="20" x2="100" y2="20"/></svg>')
    for f, n in ((tmp_path / "scan.pdf", 4), (png, 4), (noext, 4), (svg, 1)):
        drafts = geometry.read_file(f)
        assert len(drafts) == n, f
        for d in drafts:
            raw = d.pop("raw"); d.pop("chainage", None)
            geometry.rebuild(d, raw)
            assert len(d["segments"]) >= 3, f


def test_dwg_without_converter_explains(tmp_path, monkeypatch):
    from app import geometry
    monkeypatch.setattr(geometry, "_find", lambda pats: None)
    f = tmp_path / "x.dwg"
    f.write_bytes(b"AC1032" + b"\0" * 100)
    try:
        geometry.read_file(f)
    except geometry.GeometryError as e:
        assert "ODA File Converter" in str(e)
    else:
        raise AssertionError("expected a clear message")


def test_design_consultant_model():
    """The sample PDF section C-C is read as a full model; the review, options, PLAXIS geometry and 3D slices work."""
    from pathlib import Path

    from app import design, geometry
    d = geometry.read_file(Path(__file__).resolve().parent.parent / "samples" / "cross_sections_example.pdf")
    s = d[2]
    raw = s.pop("raw"); s.pop("chainage", None)
    geometry.rebuild(s, raw)
    p = design.interpret(s)
    assert p["side"] == "left" and p["excavation"]["depth"] == 12.5 and p["wall"]["toe"] == 18.0
    assert [a["z"] for a in p["anchors"]] == [2.0, 6.0, 9.5]
    assert [l["bottom"] for l in p["layers"]] == [12.0, 18.0, 25.0]
    rec = design.recommended(p)
    assert rec["width"] > p["width"] and all(r["level"] != "warn" or "gradient" in r["text"] for r in design.remarks(rec))
    nodes, segs = design.to_geometry(rec)
    assert len(nodes) > 20 and {t for *_x, t in segs} == {"soil", "structure", "water"}
    assert "<svg" in design.svg(rec, "C-C", 120)
    m = design.model3d([{"name": "a", "y": 0, "design": p}, {"name": "b", "y": 45, "design": rec}])
    assert len(m["slices"]) > 10 and m["sections"][1]["y"] == 45


def test_one_pen_sheet_keeps_the_drawing(tmp_path):
    """Drawing, hatching and CAD text all drawn with the same pen (common in AutoCAD plots): the group must stay on,
    or only a few scraps of the section are left."""
    import math as _m
    import random

    import pytest
    pytest.importorskip("reportlab")
    from pathlib import Path

    from reportlab.lib.pagesizes import A1, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    from app import design, geometry
    W, H = landscape(A1)
    f = tmp_path / "onepen.pdf"
    c = canvas.Canvas(str(f), pagesize=(W, H))
    c.setFont("Helvetica", 10); c.drawString(W - 180 * mm, 15 * mm, "SCALE 1:200")
    c.setLineWidth(0.24)
    random.seed(2)
    s = 1000 * mm / 200
    ox, oy = 40 * mm, H - 60 * mm
    P = lambda x, z: (ox + x * s, oy - z * s)  # noqa: E731
    for a, b in (((0, 8), (29.6, 8)), ((29.6, 8), (29.6, 0)), ((29.6, 0), (45, 0)), ((45, 0), (45, 25)),
                 ((45, 25), (0, 25)), ((0, 25), (0, 8)), ((0, 11), (45, 11)), ((0, 16), (45, 16)),
                 ((30.4, 0), (30.4, 13)), ((29.6, 13), (30.4, 13)), ((30.4, 2), (42, 8))):
        c.line(*P(*a), *P(*b))
    for _ in range(4000):  # letters as strokes, same pen
        x, y = random.random() * W * 0.9, random.random() * H * 0.25 + 20
        c.line(x, y, x + random.random() * 4, y + random.random() * 4)
    c.setLineWidth(0.48)
    for k in range(6):
        c.line(100 + k * 20, 100, 105 + k * 20, 104)
    c.showPage(); c.save()
    d = [x for x in geometry.read_file(f) if x["raw"]]
    best = None
    for sec in d:
        raw = sec.pop("raw"); sec.pop("chainage", None)
        geometry.rebuild(sec, raw)
        if best is None or sec["width"] > best["width"]:
            best = sec
    assert _m.isclose(best["width"], 45, abs_tol=0.5) and _m.isclose(best["depth"], 25, abs_tol=0.5)
    p = design.interpret(best)
    assert p["excavation"]["depth"] == 8.0 and p["wall"]["toe"] == 13.0 and len(p["anchors"]) == 1
    del Path


def test_layer_roles_are_learnt_for_the_next_project(client):
    """Tier 1: the engineer says what a CAD layer is; the next project from the same office reads it right."""
    from pathlib import Path
    dxf = (Path(__file__).resolve().parent.parent / "samples" / "cross_sections_example.dxf").read_bytes()
    pid = new_project(client, "new_design")
    s = client.post(f"/api/projects/{pid}/sections", files=[("files", ("sec.dxf", dxf, "application/dxf"))]
                    ).json()["sections"][0]
    assert s["quality"]["level"] in ("high", "check")
    layer = next(g for g in s["groups"] if "anchor" in g.lower())
    client.post(f"/api/projects/{pid}/sections/{s['id']}/confirm", json={})
    e = client.patch(f"/api/projects/{pid}/sections/{s['id']}", json={"roles": {layer: "ignore"}}).json()["section"]
    assert e["groups"][layer] is False and "confirmed" not in e  # a change clears the confirmation
    assert client.get("/api/layer-roles").json()[layer.lower()] == "ignore"
    # a new project: the same layer is switched off at the first read
    pid2 = new_project(client, "new_design")
    s2 = client.post(f"/api/projects/{pid2}/sections", files=[("files", ("sec.dxf", dxf, "application/dxf"))]
                     ).json()["sections"][0]
    assert s2["roles"] == {layer: "ignore"} and s2["groups"][layer] is False
    assert any("layer library" in w for w in s2["warnings"])
    # a role forces the line type, whatever the automatic rules say
    soil_layer = next(g for g in s2["groups"] if "soil" in g.lower() or "layer" in g.lower())
    e = client.patch(f"/api/projects/{pid2}/sections/{s2['id']}", json={"roles": {soil_layer: "structure", layer: ""}}
                     ).json()["section"]
    assert all(t == "structure" for _a, _b, t in [e["segments"][i] for i in e["fixed"]]) and e["fixed"]
    assert layer.lower() not in client.get("/api/layer-roles").json()


def test_quality_flags_a_symbol_read_instead_of_the_section():
    from app import geometry
    tiny = {"nodes": [[0, 0], [1.29, 0], [1.29, 1.69], [0, 1.69]], "segments": [[0, 1, "soil"], [1, 2, "soil"]],
            "width": 1.29, "depth": 1.69, "kind": "pdf"}
    q = geometry.quality(tiny)
    assert q["level"] == "low" and any("1.29" in r for r in q["reasons"])
    box = {"nodes": [[0, 0], [40, 0], [40, 20], [0, 20], [0, 8], [40, 8]],
           "segments": [[0, 1, "soil"], [1, 5, "soil"], [5, 2, "soil"], [2, 3, "soil"], [3, 4, "soil"],
                        [4, 0, "soil"], [4, 5, "soil"], [0, 3, "structure"]], "width": 40, "depth": 20, "kind": "dxf"}
    assert geometry.quality(box)["level"] == "high"


def test_poor_pdf_reading_is_read_again_by_fable(monkeypatch, tmp_path):
    """Tier 2: a PDF that reads as a tiny symbol is read again by Claude Fable, and the better reading is kept."""
    from app import config, geometry
    box = [[[0, 0], [40, 0], [40, 20], [0, 20], [0, 0]], False, "soil"]
    layer = [[[0, 8], [40, 8]], False, "soil"]
    wall = [[[20, 0], [20, 15]], False, "structure"]
    monkeypatch.setattr(config, "ai_mode", lambda: "claude")
    monkeypatch.setattr(geometry, "classify_with_ai", lambda sec: False)
    monkeypatch.setattr(main, "_read_again", lambda path, page, how: (
        {"kind": "ai", "source": "ai", "y_is_depth": True, "unit": 1.0, "warnings": []}, [box, layer, wall]))
    sec = {"id": "s1", "name": "A-A", "kind": "pdf", "page": 0, "unit": 1.0, "y_is_depth": True}
    tiny = [[[[0, 0], [1.2, 0], [1.2, 1.6]], False, "x"]]
    geometry.rebuild(sec, tiny[0:1] * 1)
    main._touch(sec)
    assert sec["quality"]["level"] == "low"
    raw = main._second_reading(tmp_path / "f.pdf", sec, tiny, lambda m: None)
    assert raw == [box, layer, wall] and sec["kind"] == "ai" and sec["width"] == 40
    assert sec["quality"]["level"] != "low" and any("Claude Fable read the page again" in w for w in sec["warnings"])
    # without the API key it says what would help, and keeps the reading
    monkeypatch.setattr(config, "ai_mode", lambda: "offline")
    sec2 = {"id": "s2", "name": "B-B", "kind": "pdf", "unit": 1.0, "y_is_depth": True}
    geometry.rebuild(sec2, tiny)
    main._touch(sec2)
    assert main._second_reading(tmp_path / "f.pdf", sec2, tiny, lambda m: None) == tiny
    assert any("Claude API key" in w for w in sec2["warnings"])


# --- CAD import on many kinds of drawing (not one project): units, layouts, one or many sections, DWG -------------
def _section(m, x, y, s=1.0, layer_prefix=""):
    """A 40 m x 18 m retaining-wall section drawn at (x, y) in drawing units s (1 = metres, 1000 = millimetres)."""
    L = lambda a, b, lay: m.add_line((x + a[0] * s, y + a[1] * s), (x + b[0] * s, y + b[1] * s),
                                     dxfattribs={"layer": layer_prefix + lay})
    for a, b in [((0, 0), (40, 0)), ((40, 0), (40, 18)), ((0, 18), (0, 0)), ((0, 18), (20, 18)), ((20, 18), (20, 10)),
                 ((20, 10), (40, 10)), ((0, 12), (20, 12)), ((20, 6), (40, 6)), ((0, 4), (40, 4))]:
        L(a, b, "SOIL")
    L((40, 10), (40, 18), "SOIL")
    for a, b in [((20, 18), (20, 3)), ((20.8, 18), (20.8, 3)), ((20, 3), (20.8, 3))]:
        L(a, b, "WALL")
    for z in (16, 13):
        L((20, z), (10, z - 4), "ANCHOR")
    m.add_text("SECTION", dxfattribs={"layer": layer_prefix + "TEXT", "height": 0.5 * s}).set_placement((x, y - 2 * s))


def _frame(m, x, y, w, h, s=1.0):
    m.add_lwpolyline([(x, y), (x + w * s, y), (x + w * s, y + h * s), (x, y + h * s)], close=True,
                     dxfattribs={"layer": "FRAME"})
    m.add_lwpolyline([(x + (w - 15) * s, y), (x + w * s, y), (x + w * s, y + 6 * s), (x + (w - 15) * s, y + 6 * s)],
                     close=True, dxfattribs={"layer": "FRAME"})  # title block in the corner


def _widths(d):
    return sorted(round((max(p[0] for r in s["raw"] for p in r[0]) - min(p[0] for r in s["raw"] for p in r[0]))
                        * s["unit"], 1) for s in d)


@pytest.mark.parametrize("case", ["m", "mm", "mm_no_units", "mm_saved_as_m", "ft", "grid_of_6", "sheets_3x2",
                                  "plan_and_sections", "far_away_xref", "blocks_and_arcs", "sheet_frames_mm",
                                  "site_coordinates", "polylines", "twelve_framed"])
def test_cad_import_many_kinds_of_drawing(tmp_path, case):
    """Every case should give each section once, about 40 m wide, whatever the units or the layout."""
    ezdxf = pytest.importorskip("ezdxf")
    from app import geometry
    doc = ezdxf.new()
    m = doc.modelspace()
    s, units, n = 1.0, 6, 1
    if case == "m":
        _section(m, 0, 0)
    elif case in ("mm", "mm_no_units", "mm_saved_as_m"):
        s, units = 1000.0, {"mm": 4, "mm_no_units": 0, "mm_saved_as_m": 6}[case]
        _section(m, 0, 0, s)
    elif case == "ft":
        s, units = 1 / 0.3048, 2
        _section(m, 0, 0, s)
    elif case == "grid_of_6":
        n = 6
        for k in range(6):
            _section(m, (k % 3) * 60, (k // 3) * 40)
    elif case == "sheets_3x2":
        n = 6
        for k in range(3):
            _frame(m, k * 120, 0, 100, 60)
            _section(m, k * 120 + 5, 8)
            _section(m, k * 120 + 50, 30)
    elif case == "plan_and_sections":
        n = 4
        for i in range(3000):  # a site plan: a dense mesh of road edges
            m.add_line((-1500 + (i % 60) * 20, 400 + (i // 60) * 8), (-1480 + (i % 60) * 20, 400 + (i // 60) * 8),
                       dxfattribs={"layer": "XR-ROAD"})
            m.add_line((-1500 + (i % 60) * 20, 400 + (i // 60) * 8), (-1500 + (i % 60) * 20, 408 + (i // 60) * 8),
                       dxfattribs={"layer": "XR-ROAD"})
        for k in range(4):
            _section(m, k * 70, 0)
    elif case == "far_away_xref":
        n = 3
        for k in range(3):
            _section(m, k * 60, 0)
        for i in range(200):
            m.add_line((i * 3.0, -10), (i * 3.0 + 1, -10), dxfattribs={"layer": "DIMS"})
        m.add_line((2.5e6, 7.1e6), (2.5e6 + 30, 7.1e6), dxfattribs={"layer": "XREF"})
    elif case == "blocks_and_arcs":
        blk = doc.blocks.new("SEC")
        _section(blk, 0, 0)
        blk.add_arc((30, 10), 3, 0, 180, dxfattribs={"layer": "SOIL"})
        n = 2
        m.add_blockref("SEC", (0, 0))
        m.add_blockref("SEC", (60, 0))
    elif case == "sheet_frames_mm":
        s, units, n = 1000.0, 4, 4
        for k in range(2):
            _frame(m, k * 120000, 0, 100, 60, s)
            _section(m, k * 120000 + 5000, 8000, s)
            _section(m, k * 120000 + 50000, 30000, s)
    elif case == "site_coordinates":  # real easting / level, e.g. x = 254 310, levels -15 to +3
        n = 2
        _section(m, 254310, -15)
        _section(m, 254410, -15)
    elif case == "polylines":  # the whole section as closed polylines (one per soil layer)
        for y0, y1 in ((0, 4), (4, 12), (12, 18)):
            m.add_lwpolyline([(0, y0), (40, y0), (40, y1), (0, y1)], close=True, dxfattribs={"layer": "SOIL"})
        m.add_lwpolyline([(20, 18), (20.8, 18), (20.8, 3), (20, 3)], close=True, dxfattribs={"layer": "WALL"})
    elif case == "twelve_framed":
        n = 12
        for k in range(12):
            _frame(m, (k % 4) * 80, (k // 4) * 50, 60, 40)
            _section(m, (k % 4) * 80 + 5, (k // 4) * 50 + 10)
    doc.header["$INSUNITS"] = units
    doc.saveas(tmp_path / "d.dxf")
    d = geometry.read_file(tmp_path / "d.dxf")
    assert len(d) == n, (len(d), _widths(d), d[0]["warnings"])
    assert all(39 <= w <= 42 for w in _widths(d)), (_widths(d), d[0]["warnings"])


def test_dwg_many_sections(tmp_path):
    """The same through DWG (written by ezdwg): a row of sheets with frames, each holding two sections."""
    ezdwg = pytest.importorskip("ezdwg")
    from app import geometry
    d = ezdwg.new()
    m = d.modelspace()
    for k in range(3):
        x = k * 120
        for a, b in [((x, 0), (x + 100, 0)), ((x + 100, 0), (x + 100, 60)), ((x + 100, 60), (x, 60)), ((x, 60), (x, 0))]:
            m.add_line(a, b)
        for ox, oy in ((5, 8), (50, 30)):
            for a, b in [((0, 0), (40, 0)), ((40, 0), (40, 18)), ((40, 18), (0, 18)), ((0, 18), (0, 0)),
                         ((0, 12), (40, 12)), ((0, 6), (40, 6)), ((20, 18), (20, 3))]:
                m.add_line((x + ox + a[0], oy + a[1]), (x + ox + b[0], oy + b[1]))
    d.save(str(tmp_path / "sheets.dwg"))
    out = geometry.read_file(tmp_path / "sheets.dwg")
    assert len(out) == 6 and all(39 <= w <= 41 for w in _widths(out)), (_widths(out), out[0]["warnings"])


# --- Calibration / Back-analysis: Reference Results uploaded as an Excel file (app/reference.py) -------------------
def _ref_xlsx(rows, header=("Result", "Value", "Unit", "Stage", "Day")):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Reference Results"
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cal_project(client):
    pid = new_project(client, "calibration")
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    r = client.put(f"/api/projects/{pid}/study", json={"mode": "calibration", "runs": 4,
                                                        "params": [{"item": "mat:Clay.phi", "min": 22, "max": 34}]})
    assert r.status_code == 200, r.text
    return pid


def _upload_ref(client, pid, data, name="reference.xlsx"):
    return client.post(f"/api/projects/{pid}/study/reference", files=[("file", (name, data, "application/octet-stream"))])


def test_reference_results_valid_file(client):
    pid = _cal_project(client)
    data = _ref_xlsx([("Wall horizontal deflection", 12.5, "mm", None, None),   # final value, by its name, in mm
                      ("anchor", "410", "kN", None, None),                       # by its key, a number typed as text
                      ("fos", 1.4, "-", None, None),                             # no unit for the factor of safety
                      ("wall_ux", 4.1, "mm", None, 30),                          # readings by day
                      ("wall_ux", 8.7, "mm", None, 60),
                      ("Utot_max", 0.9, "cm", "Anchor + prestress", None),      # by its PLAXIS name, at a stage label
                      ("settle", 9.8, "mm", "Long term", None)])
    r = _upload_ref(client, pid, data)
    assert r.status_code == 200, r.text
    s, ref = r.json()["study"], r.json()["reference"]
    assert ref["rows"] == 7 and ref["finals"] == 3 and ref["readings"] == 4
    meas = {x["output"]: x["value"] for x in s["measured"]}
    assert meas == {"wall_ux": pytest.approx(0.0125), "anchor": pytest.approx(410.0), "fos": pytest.approx(1.4)}
    ser = {x["output"]: x["points"] for x in s["series"]}
    assert [p["day"] for p in ser["wall_ux"]] == [30, 60] and ser["wall_ux"][1]["value"] == pytest.approx(0.0087)
    assert ser["utot"] == [{"day": 90.0, "value": pytest.approx(0.009)}]  # Anchor + prestress is the 3rd stage, day 90
    assert ser["settle"][0]["day"] == 120 + 365 and s["time"]["longterm"] == 365
    assert r.json()["errors"] == []
    # saved with the project, and used by the calibration run
    assert client.get(f"/api/projects/{pid}/study").json()["study"]["reference"]["name"] == "reference.xlsx"
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["status"] == "succeeded"
    got = {m["label"]: m["value"] for m in out["calibration"]["measured"]}
    assert got["Max anchor force"] == pytest.approx(410.0)
    # removing the file clears what it gave
    s = client.delete(f"/api/projects/{pid}/study/reference").json()["study"]
    assert s["measured"] == [] and s["series"] == [] and s["reference"] is None


def test_reference_results_missing_column(client):
    pid = _cal_project(client)
    before = client.get(f"/api/projects/{pid}/study").json()["study"]
    r = _upload_ref(client, pid, _ref_xlsx([("utot", 12, None)], header=("Result", "Measured value", "Stage")))
    assert r.status_code == 400
    msg = r.json()["detail"]
    assert "Missing column: Unit" in msg and "Result, Measured value, Stage" in msg
    r = _upload_ref(client, pid, _ref_xlsx([("utot", "mm")], header=("Result", "Unit")))
    assert r.status_code == 400 and "Missing column: Value" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/study").json()["study"] == before  # nothing was loaded


def test_reference_results_wrong_unit(client):
    pid = _cal_project(client)
    r = _upload_ref(client, pid, _ref_xlsx([("utot", 12, "kN", None, None),          # a force for a displacement
                                            ("anchor", 400, "furlong", None, None),  # not a unit
                                            ("ux", 5, None, None, None),             # no unit
                                            ("ux", 5, "mm", None, None)]))           # this row is right
    assert r.status_code == 400
    msg = r.json()["detail"]
    assert 'Row 2: Unit "kN" does not fit Max total displacement |u|, which needs a length' in msg
    assert 'Row 3: Unit "furlong" is not a unit the app knows' in msg
    assert "Row 4: Unit is empty" in msg and "Row 5" not in msg
    assert client.get(f"/api/projects/{pid}/study").json()["study"]["measured"] == []


def test_reference_results_other_mistakes_and_formats(client):
    pid = _cal_project(client)
    r = _upload_ref(client, pid, _ref_xlsx([("foo", 1, "mm", None, None), ("utot", "abc", "mm", None, None),
                                            ("utot", 1, "mm", "Phase_99", None), ("utot", 1, "mm", None, -3),
                                            ("uv", 1, "mm", None, None), ("uv", 2, "mm", None, None)]))
    msg = r.json()["detail"]
    assert r.status_code == 400 and all(f"Row {n}:" in msg for n in (2, 3, 4, 5, 7)) and "Row 6:" not in msg
    assert _upload_ref(client, pid, _ref_xlsx([])).status_code == 400  # header only
    assert "not an Excel file" in _upload_ref(client, pid, b"x", "ref.pdf").json()["detail"]
    csv_data = "Result;Value;Unit;Day\nwall_ux;12,5;mm;\nwall_ux;4,1;mm;30\n".encode()
    r = _upload_ref(client, pid, csv_data, "ref.csv")
    assert r.status_code == 200, r.text
    assert r.json()["study"]["measured"] == [{"output": "wall_ux", "value": pytest.approx(0.0125)}]
    assert r.json()["study"]["series"][0]["points"] == [{"day": 30.0, "value": pytest.approx(0.0041)}]
    t = client.get("/api/reference-results-template.xlsx")  # the template loads as it is
    assert t.status_code == 200 and _upload_ref(client, pid, t.content).status_code == 200


def test_reference_results_time_in_minutes_and_x_axis(client):
    """Pile load test: readings in minutes, and the figures against time (minutes), stage or the applied load."""
    from app import study
    pid = _cal_project(client)
    data = _ref_xlsx([("uv", 1.2, "mm", 30), ("uv", 2.6, "mm", 60), ("uv", 3.1, "mm", 120)],
                     header=("Result", "Value", "Unit", "Time (min)"))
    r = _upload_ref(client, pid, data)
    assert r.status_code == 200, r.text
    pts = r.json()["study"]["series"][0]["points"]
    assert [p["day"] for p in pts] == pytest.approx([30 / 1440, 60 / 1440, 120 / 1440])
    # a Time unit column per row, and a wrong unit of time
    r = _upload_ref(client, pid, _ref_xlsx([("uv", 1.2, "mm", 2, "h"), ("uv", 1.3, "mm", 2, "weeks")],
                                           header=("Result", "Value", "Unit", "Time", "Time unit")))
    assert r.status_code == 400 and 'Row 3: Time unit "weeks" is not min, h or day' in r.json()["detail"]
    assert "Row 2" not in r.json()["detail"]
    # X without an X-axis variable is refused; with one, it goes on the stage of the nearest load
    r = _upload_ref(client, pid, _ref_xlsx([("uv", 1.2, "mm", 500)], header=("Result", "Value", "Unit", "X")))
    assert r.status_code == 400 and "X-axis of the study is not a variable" in r.json()["detail"]
    xa = {"kind": "variable", "label": "Applied load", "unit_label": "kN",
          "values": {"Phase_1": 250, "Phase_2": 500, "Phase_3": 750, "Phase_4": 1000}}
    s = client.get(f"/api/projects/{pid}/study").json()["study"]
    assert client.put(f"/api/projects/{pid}/study", json={**s, "xaxis": xa}).status_code == 200
    r = _upload_ref(client, pid, _ref_xlsx([("uv", 1.2, "mm", 480), ("uv", 3.0, "mm", 1000)],
                                           header=("Result", "Value", "Unit", "X")))
    assert r.status_code == 200, r.text
    pts = r.json()["study"]["series"][0]["points"]
    assert pts == [{"day": 60.0, "x": 480.0, "value": pytest.approx(0.0012)}, {"day": 120.0, "x": 1000.0, "value": pytest.approx(0.003)}]
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["status"] == "succeeded" and out["figures"]
    # the figure data on each X-axis
    t = main._task_or_404(tid)
    _, res, allruns, _ = main._study_results(tid)
    ts = res["time"][0]
    load = study.on_xaxis(ts, xa)
    assert load["measured"] == [(480.0, pytest.approx(0.0012)), (1000.0, pytest.approx(0.003))]
    assert [p[0] for p in load["runs"][0]["points"]] == [500, 1000]  # the stages of the readings (Results over time off)
    mins = study.on_xaxis(ts, {"kind": "time", "unit": "min"})
    assert [p[0] for p in mins["runs"][0]["points"]] == [60 * 1440, 120 * 1440] and mins["stages"][0][0] == 60 * 1440
    st = study.on_xaxis(ts, {"kind": "stage"})
    assert [p[0] for p in st["runs"][0]["points"]] == [1, 2] and st["xticks"][1][1] == "Excavation to -10 m"
    specs = [x for x in study.chart_specs(t, res, allruns) if x["kind"] == "time"]
    assert specs[0]["xlabel"] == "Applied load (kN)" and "against Applied load" in specs[0]["title"]
    # an X-axis variable without values is reported before the run
    prev = client.put(f"/api/projects/{pid}/study", json={**r.json()["study"], "xaxis": {"kind": "variable"}}).json()
    assert any("X-axis" in e for e in prev["errors"])


def test_reference_results_csv(client):
    """CSV: commas, semicolons with a decimal comma, Windows-1252, the same checks as Excel, and its template."""
    pid = _cal_project(client)
    r = _upload_ref(client, pid, b"Result,Value,Unit,Stage,Time (min)\r\nMax anchor force,410,kN,,\r\nuv,1.2,mm,,30\r\n", "ref.csv")
    assert r.status_code == 200, r.text
    s = r.json()["study"]
    assert s["measured"] == [{"output": "anchor", "value": pytest.approx(410.0)}]
    assert s["series"][0]["points"] == [{"day": pytest.approx(30 / 1440), "value": pytest.approx(0.0012)}]
    r = _upload_ref(client, pid, "Result;Value;Unit\nMax excess pore pressure;12,5;kN/m²\n".encode("cp1252"), "ref.csv")
    assert r.status_code == 200, r.text
    assert r.json()["study"]["measured"] == [{"output": "pexcess", "value": pytest.approx(12.5)}]
    r = _upload_ref(client, pid, b"Result,Reading\nutot,3\n", "ref.csv")  # missing column
    assert r.status_code == 400 and "Missing columns: Value, Unit" in r.json()["detail"]
    r = _upload_ref(client, pid, b"Result,Value,Unit\nutot,3,kN\n", "ref.csv")  # wrong unit, line 2
    assert r.status_code == 400 and 'Row 2: Unit "kN" does not fit' in r.json()["detail"]
    t = client.get("/api/reference-results-template.csv")
    assert t.status_code == 200 and t.content.startswith(b"\xef\xbb\xbfResult,Value,Unit")
    assert _upload_ref(client, pid, t.content, "template.csv").status_code == 200
