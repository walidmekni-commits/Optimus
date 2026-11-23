"""OPTIMUS V26.1: projects list, Save / Save As / Open with organized folders and the PLAXIS file of each run,
sign-in (password, forgot password, recovery key), units, Sensitivity Detector on the control parameters."""
import json
from pathlib import Path

from app import accounts, notify, project_files, units
from app.config import runs_dir
from tests.test_studies import PHI, _run, _setup
from tests.test_workflow import client  # noqa: F401  (fixture)


def test_every_project_is_listed_from_the_database(client):
    pid, _ = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}], runs=3)
    lst = client.get("/api/projects").json()["projects"]
    me = next(x for x in lst if x["id"] == pid)
    assert me["title"] == "Zayed wall" and me["unsaved"] is True and me["mode"] == "sensitivity"


def test_save_save_as_and_open_with_the_plaxis_files(client, tmp_path):
    client.put("/api/settings", json={"projects_dir": str(tmp_path / "projects")})
    pid, _ = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}], runs=4)
    tid, out = _run(client, pid)
    for i in range(4):  # what PLAXIS saves for each run: the project file and its data folder
        d = runs_dir() / tid / f"run_{i + 1:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"wall_run_{i + 1:03d}.p3d").write_text("plaxis")
        (d / f"wall_run_{i + 1:03d}.p3ddat").mkdir(exist_ok=True)
    st = client.get(f"/api/projects/{pid}/studies").json()["studies"]
    assert st[0]["folder"] == "Sensitivity Analysis" and all(r["file"] for r in st[0]["runs"])
    run3 = next(r for r in st[0]["runs"] if r["run"] == 3)

    # Save: only Run 3's PLAXIS file
    r = client.post(f"/api/projects/{pid}/save", json={"plaxis": "some", "runs": "3"})
    assert r.status_code == 200, r.text
    res = r.json()
    base = Path(res["folder"])
    assert base == tmp_path / "projects" / "Zayed wall" and Path(res["file"]).name == "Zayed wall.optimus"
    runs = sorted(p.name for p in base.glob("Sensitivity Analysis/*/Run *"))
    assert runs == ["Run 003"] and res["plaxis_files"] == 1
    assert list(base.glob("Sensitivity Analysis/*/results.xlsx"))
    assert (base / "Model" / "wall.p3d").exists()
    assert next(x for x in client.get("/api/projects").json()["projects"] if x["id"] == pid)["unsaved"] is False

    # Save again: all the PLAXIS files
    res = client.post(f"/api/projects/{pid}/save", json={"plaxis": "all"}).json()
    assert len(list(base.glob("Sensitivity Analysis/*/Run *"))) == 4 and res["folder"] == str(base)

    # the PLAXIS file of Run 3 can be opened again (path given back where the PC cannot open it)
    o = client.post(f"/api/tasks/{tid}/runs/{run3['idx']}/open", json={}).json()
    assert f"run_{run3['idx'] + 1:03d}" in o["path"] and o["path"].endswith(".p3d")
    z = client.get(f"/api/tasks/{tid}/runs/{run3['idx']}/file.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"

    # Save As: a copy with its own name, the original stays
    r = client.post(f"/api/projects/{pid}/save", json={"as_copy": True, "name": "Zayed wall v2", "plaxis": "none"}).json()
    assert r["project"] != pid and Path(r["file"]).name == "Zayed wall v2.optimus"
    copy = client.get(f"/api/projects/{r['project']}").json()["project"]
    assert copy["setup"]["info"]["title"] == "Zayed wall v2"

    # Open: a project file brings the project back, even when the database lost it
    snap = json.loads(Path(res["file"]).read_text(encoding="utf-8"))
    project_files.delete_project(pid)
    assert client.get(f"/api/projects/{pid}").status_code == 404
    v = client.post("/api/projects/open", json={"path": res["file"]}).json()
    assert v["project"]["id"] == snap["project"]["id"] == pid
    again = client.get(f"/api/tasks/{tid}/study").json()
    assert again["done"] == out["done"] and again["status"] == "succeeded"


def test_sign_in_password_reset_and_recovery_key(client):
    assert client.get("/api/auth/status").json()["required"] is False  # no account: open as before
    assert client.get("/api/projects").status_code == 200
    r = client.post("/api/auth/setup", json={"name": "Walid", "email": "Walid@Example.com", "password": "short"})
    assert r.status_code == 400 and "8 characters" in r.json()["detail"]
    r = client.post("/api/auth/setup", json={"name": "Walid", "email": "walid@example.com", "password": "Geo2026wall"})
    assert r.status_code == 200, r.text
    key = r.json()["recovery_key"]
    assert client.get("/api/auth/status").json()["user"]["name"] == "Walid"

    client.post("/api/auth/logout")
    client.cookies.clear()
    r = client.get("/api/projects")
    assert r.status_code == 401 and r.json()["signin"] is True
    assert client.get("/api/objectives").status_code == 200  # the desktop launcher still sees the server
    assert client.post("/api/auth/login", json={"email": "walid@example.com", "password": "wrong1234"}).status_code == 400
    assert client.post("/api/auth/login", json={"email": "WALID@example.com", "password": "Geo2026wall"}).status_code == 200
    assert client.get("/api/projects").status_code == 200

    # forgot password: a code by email
    client.post("/api/auth/logout")
    client.cookies.clear()
    notify.OUTBOX.clear()
    client.post("/api/auth/forgot", json={"email": "walid@example.com"})
    code = notify.OUTBOX[-1]["body"].split("code is: ")[1][:6]
    assert client.post("/api/auth/reset", json={"email": "walid@example.com", "code": "000000" if code != "000000" else "111111",
                                                "password": "NewPass2027"}).status_code == 400
    assert client.post("/api/auth/reset", json={"email": "walid@example.com", "code": code, "password": "NewPass2027"}).status_code == 200
    assert client.get("/api/projects").status_code == 200

    # or the recovery key, without email
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.post("/api/auth/reset", json={"email": "walid@example.com", "code": key.lower(), "password": "Again2028x"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "walid@example.com", "password": "Again2028x"}).status_code == 200
    # sign-in can be switched off again when signed in
    assert client.post("/api/auth/skip").json()["required"] is False


def test_units_imported_from_plaxis_or_custom(client):
    s = {"plaxis_units": {"length": "m", "force": "kN", "time": "day"},
         "units": {"mode": "custom", "displacement": "mm", "force": "kN", "length": "m", "stress": "MPa"}}
    assert units.to_display(0.052, "m", s) == (52.0, "mm")
    assert units.to_display(2000.0, "kN/m²", s) == (2.0, "MPa")
    assert units.to_display(280.0, "kN·m/m", s) == (280.0, "kN·m/m")
    assert abs(units.to_plaxis(52.0, "m", s) - 0.052) < 1e-12
    ft = {"plaxis_units": {"length": "ft", "force": "kip", "time": "day"}}  # a model in ft / kip: labels follow it
    assert units.to_display(3.0, "kN·m/m", ft) == (3.0, "kip·ft/ft") and units.input_label("kN/m³", ft) == "kip/ft³"
    assert units.from_plaxis("ft", "kip", "day") == {"length": "ft", "force": "kip", "time": "day"}

    pid, _ = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "utot", "how": "max", "value": 60}], runs=3)
    r = client.put(f"/api/projects/{pid}/units", json={"mode": "custom", "displacement": "mm"}).json()
    assert r["display"]["displacement"] == "mm"
    cat = client.get(f"/api/projects/{pid}/study").json()["catalog"]
    assert next(o for o in cat["outputs"] if o["key"] == "utot")["unit"] == "mm"
    tid, out = _run(client, pid)
    t = out["targets"][0]
    assert t["unit"] == "mm" and abs(t["ref"] - 60) < 1e-9  # typed in mm, checked in PLAXIS units (m)
    plan = client.get(f"/api/tasks/{tid}").json()
    raw = [x for x in (plan.get("plan") or {}).get("targets", []) if x.get("key") == "utot"]
    if raw:
        assert abs(raw[0]["ref"] - 0.06) < 1e-12
    vals = [row["outputs"].get(t["name"]) for row in out["rows"] if row["outputs"].get(t["name"]) is not None]
    assert vals and all(v > 1 for v in vals)  # displacements shown in mm, not m


def test_sensitivity_detector_on_the_control_parameters(client):
    pid, _ = _setup(client, "sensitivity", params=[PHI, {"item": "mat:Sand.phi", "min": 28, "max": 38, "custom": [], "new": ""}],
                    controls=[{"output": "fos", "how": "fixed", "value": 1.5}], detector={"on": True, "fields": []}, runs=5)
    tid, out = _run(client, pid)
    sm = out["summary"][0]
    assert sm["key"] == "fos" and sm["params"]  # the input each control parameter is most sensitive to
    det = out["detector"]
    assert all("key" in d and "control" in d for d in det)
    assert any(d["control"] for d in det) and any(not d["control"] for d in det)  # results to add as control parameters


def test_custom_units_with_an_optional_scale():
    s = {"units": {"mode": "custom", "displacement": "mm", "force": "kN", "length": "m", "stress": "kPa",
                   "scale": {"force": 0.001, "displacement": ""}}}
    assert units.to_display(280.4, "kN·m/m", s) == (280.4 * 0.001, "kN·m/m ×0.001")
    assert units.to_display(0.05, "m", s) == (50.0, "mm")  # empty scale = 1
    assert abs(units.to_plaxis(0.2804, "kN·m/m", s) - 280.4) < 1e-9


def test_full_run_of_the_same_plaxis_file_is_remembered(client, monkeypatch):
    """The uploaded model calculated fully once: the same file (new project, restart, update) is not run again."""
    from app import basic_cache, engine
    from tests.test_workflow import new_project
    pid = new_project(client, "sensitivity")
    p = client.get(f"/api/projects/{pid}").json()["project"]
    key = basic_cache.model_hash(p["files"])
    assert key and client.get(f"/api/projects/{pid}/basic").json()["status"] is None
    # what the full PLAXIS run leaves (PLAXIS is not on this test machine)
    t = {"name": "T1", "label": "Factor of safety", "result": "FoS", "phase": "", "operator": "report", "value": None,
         "unit": "", "key": "fos"}
    basic_cache.put(key, "wall.p3d", [t], {"T1": 1.42}, {"version": "3d", "source": "plaxis", "points": [], "counts": {}})
    pid2 = new_project(client, "sensitivity")  # the same file uploaded again
    b = client.get(f"/api/projects/{pid2}/basic").json()
    assert b["status"] == "succeeded" and b["values"]["fos"] == 1.42 and b["cached"]["file"] == "wall.p3d"
    assert client.get(f"/api/projects/{pid2}").json()["project"]["setup"]["plaxis_model"]["source"] == "plaxis"
    assert client.get(f"/api/projects/{pid2}/basic").json()["task"] == b["task"]  # taken over once


def test_run_0_comes_from_the_full_run_of_the_original_file(client):
    """Run 0 (existing design) is not calculated again when the full PLAXIS run of the first page has its results."""
    from app import db, main
    pid, _ = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}], runs=3)
    p = db.get_project(pid)
    plan = main._basic_plan(p, False)
    tid = db.create_task(pid, "reporting", "basic", plan)
    db.add_runs(tid, [{"label": "Existing design", "inputs": {}}])
    fos = next(t["name"] for t in plan["targets"] if t["key"] == "fos")
    db.update_run(db.get_runs(tid)[0]["id"], status="succeeded", outputs={t["name"]: 1.0 for t in plan["targets"]} | {fos: 1.234})
    db.update_task(tid, status="succeeded")
    stid, out = _run(client, pid)
    r0 = next(r for r in out["rows"] if r["run"] == 0)
    assert abs(list(r0["outputs"].values())[0] - 1.234) < 1e-12
    log = [e for e in client.get(f"/api/log?project_id={pid}").json() if e["event"] == "attempt" and "Original File" in e["detail"]]
    assert log


def test_each_tab_lists_its_studies_and_one_can_be_deleted(client):
    pid, _ = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}], runs=3)
    t1, _ = _run(client, pid)
    t2, _ = _run(client, pid)
    st = [s for s in client.get(f"/api/projects/{pid}/studies").json()["studies"] if s["kind"] == "sensitivity"]
    assert [s["task"] for s in st] == [t1, t2] and [s["n"] for s in st] == [1, 2]
    assert client.delete(f"/api/tasks/{t1}").status_code == 200
    st = [s for s in client.get(f"/api/projects/{pid}/studies").json()["studies"] if s["kind"] == "sensitivity"]
    assert [s["task"] for s in st] == [t2]
    assert client.get(f"/api/tasks/{t1}").status_code == 404
    assert client.delete(f"/api/tasks/{t1}").status_code == 404


def test_full_run_reads_every_main_result_at_every_stage(client):
    """The summary table of the control parameters: each main result at each stage of the Original File, the factor
    of safety only in the safety phases, and the items the model does not have flagged; images of the run."""
    r = client.post("/api/projects", data={"objective": "sensitivity", "title": "Stages", "source": "upload",
                                           "discipline": "structural"})
    pid = r.json()["project"]["id"]
    assert r.json()["project"]["setup"]["info"]["discipline"] == "structural"
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    b = client.post(f"/api/projects/{pid}/basic").json()  # the example: simulated
    b = client.get(f"/api/projects/{pid}/basic").json()
    assert b["status"] == "succeeded", b
    st = b["stages"]
    names = [p["name"] for p in st["phases"]]
    assert names == ["Phase_1", "Phase_2", "Phase_3", "Phase_4", "Phase_5"] and st["phases"][-1]["safety"]
    rows = {r["key"]: r for r in st["rows"]}
    assert set(rows["fos"]["values"]) == {"Phase_5"} and "Phase_5" not in rows["utot"]["values"]
    u = [rows["utot"]["values"][n] for n in names[:-1]]
    assert all(isinstance(x, float) for x in u) and u == sorted(u)  # grows stage by stage
    assert not rows["pile_n"]["absent"] is None and rows["pile_n"]["absent"]  # no embedded beam in this model
    assert not rows["plate_m"]["absent"]
    assert len(b["results"]) == 12  # the main results only (not the stage table)
    assert {p["key"] for p in b["plots"]} == {"utot", "ux", "uy", "plate_m"}
    png = client.get(b["plots"][0]["url"])
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"


def test_word_report_has_the_main_inputs_comparison_and_model_output(client):
    import io as _io
    from docx import Document
    pid, _ = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}], runs=3)
    tid, out = _run(client, pid)
    pl = client.get(f"/api/tasks/{tid}/plots").json()["plots"]
    assert {p["key"] for p in pl} == {"utot", "ux"} and len(pl) == 2 * 3  # chosen in the study: every run
    d = Document(_io.BytesIO(client.get(f"/api/tasks/{tid}/report.docx").content))
    text = "\n".join(p.text for p in d.paragraphs)
    for h in ("OPTIMUS®", "Geotechnical Engineering report", "2. Main inputs", "2.1 Soil materials", "2.3 Loads",
              "Comparison with the Original File", "Model geometry", "Model output (PLAXIS Output)", "Table 1.", "Figure 1."):
        assert h in text, h
    assert len(d.inline_shapes) >= 1 + 1 + 6  # logo, geometry, the run images (and the charts)
    assert client.get(f"/api/projects/{pid}/geometry.png").content[:4] == b"\x89PNG"
    # without Model output: no PLAXIS image is saved
    s = client.get(f"/api/projects/{pid}/study").json()["study"]
    s["report"] = {"vis": ["tables"], "plots": []}
    client.put(f"/api/projects/{pid}/study", json=s)
    tid2, _ = _run(client, pid)
    assert client.get(f"/api/tasks/{tid2}/plots").json()["plots"] == []
    text = "\n".join(p.text for p in Document(_io.BytesIO(client.get(f"/api/tasks/{tid2}/report.docx").content)).paragraphs)
    assert "Model output" not in text and "Comparison with the Original File" in text
