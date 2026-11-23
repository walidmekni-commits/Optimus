"""The four studies: sensitivity, optimization (+ new runs), modification and scenarios, with Excel and Word."""
import io
import zipfile

import pytest

from tests.test_workflow import client, new_project  # noqa: F401  (fixture)

PHI = {"item": "mat:Clay.phi", "min": 22, "max": 34, "custom": [], "new": ""}


def _setup(client, mode, **study):
    pid = new_project(client, mode)
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    r = client.put(f"/api/projects/{pid}/study", json={"mode": mode, **study})
    assert r.status_code == 200, r.text
    return pid, r.json()


def _run(client, pid):
    r = client.post(f"/api/projects/{pid}/study/run")
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["done"] == out["total"] and out["status"] == "succeeded", out
    return tid, out


def _check_files(client, tid):
    x = client.get(f"/api/tasks/{tid}/results.xlsx?swap=0")
    assert x.status_code == 200 and zipfile.is_zipfile(io.BytesIO(x.content))
    d = client.get(f"/api/tasks/{tid}/report.docx")
    assert d.status_code == 200 and b"word/document.xml" in d.content


def test_catalog_has_domains(client):
    pid, _ = _setup(client, "sensitivity")
    r = client.get(f"/api/projects/{pid}/study").json()
    doms = {p["domain"] for p in r["catalog"]["params"]}
    assert {"geometry", "soil", "structures", "loads", "mesh"} <= doms
    assert any(o["key"] == "fos" for o in r["catalog"]["outputs"])


def test_sensitivity(client):
    pid, prev = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}], runs=6)
    assert prev["errors"] == [] and prev["total"] == 6 and prev["rows"][0]["values"] == "Existing design"  # total
    tid, out = _run(client, pid)
    assert len(out["rows"]) == 6 and len(out["figures"]) == 2  # the figure + the sensitivity summary
    assert out["figures"][0]["src"].startswith("data:image/png;base64,")
    _check_files(client, tid)


def test_study_needs_a_control(client):
    pid, prev = _setup(client, "sensitivity", params=[PHI])
    assert prev["errors"]
    assert client.post(f"/api/projects/{pid}/study/run").status_code == 400


def test_optimization_and_new_runs(client):
    pid, prev = _setup(client, "optimize", params=[PHI], targets=[{"output": "fos", "operator": ">=", "value": 1.5}], runs=4)
    tid, out = _run(client, pid)
    assert "optimization" in out and out["optimization"]["percent"] > 0
    r = client.post(f"/api/tasks/{tid}/more", json={"n": 3})
    assert r.status_code == 200, r.text
    out2 = client.get(f"/api/tasks/{r.json()['id']}/study").json()
    assert len(out2["rows"]) == len(out["rows"]) + 3
    assert sum(1 for x in out2["rows"] if x["new"]) == 3
    old = {x["values"] for x in out["rows"]}
    assert not any(x["values"] in old for x in out2["rows"] if x["new"])  # earlier runs are not repeated
    _check_files(client, r.json()["id"])


def test_modification(client):
    pid, prev = _setup(client, "design_change", params=[{**PHI, "new": 30}], outputs=["fos", "utot"])
    assert prev["total"] == 2
    tid, out = _run(client, pid)
    assert out["comparison"] and out["comparison"][0]["old"] != out["comparison"][0]["new"]
    _check_files(client, tid)


def test_scenarios_custom_values(client):
    pid, prev = _setup(client, "combinations", params=[{**PHI, "custom": [24, 28, 32]}], outputs=["fos"],
                       runs=4, distribution="custom")
    assert [r["values"] for r in prev["rows"][1:]] == ["Clay Friction angle φ' = 24 °", "Clay Friction angle φ' = 28 °",
                                                         "Clay Friction angle φ' = 32 °"]
    tid, out = _run(client, pid)
    assert len(out["figures"]) == 1
    _check_files(client, tid)


@pytest.mark.parametrize("key", ["move:Plate_1.x", "pt:Point_5.x", "mesh"])
def test_geometry_and_mesh_parameters(client, key):
    p = {"item": key, "min": -1, "max": 1} if key != "mesh" else {"item": key}
    pid, prev = _setup(client, "combinations", params=[p], outputs=["fos"], runs=3)
    assert prev["errors"] == [] and prev["total"] == 3


def test_stop_and_restart(client, monkeypatch):
    from app import db, engine
    pid, prev = _setup(client, "combinations", params=[PHI], outputs=["fos"], runs=5)
    real = engine._stop_asked
    monkeypatch.setattr(engine, "_stop_asked", lambda tid: real(tid) or sum(
        r["status"] == "succeeded" for r in db.get_runs(tid)) >= 2)  # Stop pressed during the second run
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["status"] == "stopped" and out["done"] == 2
    assert [r["status"] for r in out["rows"]].count("stopped") == 3
    assert out["figures"]  # figures of the runs made so far
    assert client.post(f"/api/tasks/{tid}/stop").status_code == 409  # nothing calculating now
    monkeypatch.setattr(engine, "_stop_asked", real)
    assert client.post(f"/api/tasks/{tid}/restart", json={}).status_code == 200
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["status"] == "succeeded" and out["done"] == out["total"] == 5
    # Re-run from Run 3: the runs before keep their results, Run 3 and after are calculated again
    from app import db as _db
    before = [r["outputs"] for r in _db.get_runs(tid)]
    seen = []
    monkeypatch.setattr(engine, "_stop_asked", lambda t: seen.append(sum(r["status"] == "pending" for r in _db.get_runs(t))) or False)
    assert client.post(f"/api/tasks/{tid}/restart", json={"from": 3}).status_code == 200
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["status"] == "succeeded" and out["done"] == 5 and seen and max(seen) == 2  # runs 3 and 4 only
    assert [r["outputs"] for r in _db.get_runs(tid)][:3] == before[:3]


def test_runs_are_the_total_shared_between_parameters(client):
    two = [PHI, {"item": "mat:Sand.phi", "min": 30, "max": 36}]
    pid, prev = _setup(client, "sensitivity", params=two, controls=[{"output": "fos"}], runs=5)
    assert prev["total"] == 5 and prev["errors"] == []  # Run 0 + 2 + 2
    pid, prev = _setup(client, "sensitivity", params=two, controls=[{"output": "fos"}], runs=2)
    assert prev["errors"]  # Run 0 + 1 run cannot cover 2 parameters
    pid, prev = _setup(client, "optimize", params=[PHI], targets=[{"output": "fos", "operator": ">=", "value": 1.5}],
                       runs=5, baseline=False)
    assert prev["total"] == 5


def test_calibration_back_analysis(client):
    pid, prev = _setup(client, "calibration", params=[PHI], measured=[{"output": "fos", "value": 1.4}], runs=5)
    assert prev["errors"] == [] and prev["total"] == 5
    tid, out = _run(client, pid)
    rows = [r for r in out["rows"] if r.get("misfit") is not None]
    assert len(rows) == 5 and out["calibration"]["best_run"] in [r["run"] for r in rows]
    assert min(r["misfit"] for r in rows) == out["calibration"]["misfit"]
    assert "Best fit" in out["text"] and out["stats"]
    assert any("Misfit" in f["title"] for f in out["figures"])
    _check_files(client, tid)


def test_calibration_needs_a_measured_value(client):
    pid, prev = _setup(client, "calibration", params=[PHI], measured=[{"output": "fos", "value": ""}])
    assert any("measured value" in e for e in prev["errors"])


def test_advanced_reporting_runs_the_existing_design(client):
    pid, prev = _setup(client, "reporting", outputs=["fos", "utot"])
    assert prev["errors"] == [] and prev["total"] == 1
    tid, out = _run(client, pid)
    assert len(out["rows"]) == 1 and out["rows"][0]["values"] == "Existing design"
    assert len(out["stats"]) == 2
    _check_files(client, tid)


def test_basic_model_run_needs_plaxis_for_an_uploaded_model(client):
    pid = new_project(client, "sensitivity")  # an uploaded .p3d: calculated in PLAXIS, never simulated
    r = client.post(f"/api/projects/{pid}/basic")
    assert r.status_code == 409 and "not connected" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/basic").json()["status"] is None


def test_basic_model_run_from_the_first_page(client):
    r = client.post("/api/projects", data={"objective": "reporting", "title": "Example", "source": "upload"})
    pid = r.json()["project"]["id"]  # the example model (no PLAXIS file): simulated
    assert client.get(f"/api/projects/{pid}/basic").json()["status"] is None
    r = client.post(f"/api/projects/{pid}/basic")
    assert r.status_code == 200, r.text
    assert r.json()["example"] is True
    b = client.get(f"/api/projects/{pid}/basic").json()
    assert b["status"] == "succeeded" and "fos" in b["values"] and "plate_m" in b["values"]
    assert client.get(f"/api/projects/{pid}").json()["latest_task"] is None  # not shown as the study's results


def test_control_target_maximum(client):
    pid, prev = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "plate_m", "how": "max", "value": 520}], runs=4)
    assert prev["errors"] == []
    tid, out = _run(client, pid)
    assert out["targets"][0]["how"] == "max" and out["targets"][0]["ref"] == 520
    _check_files(client, tid)


def test_sensitivity_detector_of_the_results(client):
    pid, prev = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}],
                       detector={"on": True, "fields": []}, runs=5)
    assert prev["errors"] == []
    tid, out = _run(client, pid)
    det = out["detector"]
    assert 3 <= len(det) <= 10 and det == sorted(det, key=lambda d: -abs(d["spread"]))
    assert all(d["driver"] for d in det)
    assert any("Sensitivity Detector" in f["title"] for f in out["figures"])
    _check_files(client, tid)


def test_sensitivity_detector_of_the_inputs(client):
    pid, prev = _setup(client, "sensitivity", controls=[{"output": "fos", "how": "fixed", "value": 1.5}],
                       screen={"on": True, "pct": 20, "items": ["mat:Clay.phi", "mat:Clay.cref"]})
    assert prev["errors"] == [], prev["errors"]
    assert prev["total"] == 3 and "+20 %" in prev["rows"][1]["values"] or "+20 %" in str(prev["rows"])
    tid, out = _run(client, pid)
    sc = out["screen"]
    assert [x["item"] for x in sc][0] in ("mat:Clay.phi", "mat:Clay.cref") and len(sc) == 2
    assert abs(sc[0]["main"]) >= abs(sc[1]["main"]) and "Sensitivity Detector" in out["text"]
    _check_files(client, tid)


def test_sensitivity_summary_tornado(client):
    soil = [{"item": "mat:Clay.phi", "min": 22, "max": 34}, {"item": "mat:Clay.cref", "min": 2, "max": 20}]
    pid, prev = _setup(client, "sensitivity", params=soil, runs=7,
                       controls=[{"output": "fos", "how": "fixed", "value": 1.5}, {"output": "plate_m", "how": "max", "value": 520}])
    assert prev["errors"] == []
    tid, out = _run(client, pid)
    assert len(out["summary"]) == 2 and all(len(sm["params"]) == 2 for sm in out["summary"])
    p = out["summary"][0]["params"]
    assert p[0]["span"] >= p[1]["span"] and p[0]["lo"] <= 0 <= p[0]["hi"]
    titles = " ".join(f["title"] for f in out["figures"])
    assert "Sensitivity summary: effect of each parameter" in titles and "spread of every control parameter" in titles
    _check_files(client, tid)


def test_basic_run_shows_progress_and_can_be_cancelled(client):
    r = client.post("/api/projects", data={"objective": "reporting", "title": "Example", "source": "upload"})
    pid = r.json()["project"]["id"]
    b = client.post(f"/api/projects/{pid}/basic").json()
    assert {"progress", "elapsed", "step_seconds"} <= set(b) and b["status"] == "succeeded"
    t = client.get(f"/api/projects/{pid}/basic").json()["task"]
    from app import db
    db.update_task(t, status="running")  # as if PLAXIS hangs
    c = client.post(f"/api/projects/{pid}/basic/cancel").json()
    assert c["status"] == "stopped"


def test_basic_model_run_is_calculated_once(client, monkeypatch):
    """The basic model run is never repeated automatically: a failure is shown once, Run PLAXIS repeats it."""
    monkeypatch.setenv("SIM_FAIL_RATE", "1")
    monkeypatch.setenv("MAX_RETRIES", "3")
    r = client.post("/api/projects", data={"objective": "reporting", "title": "Once", "source": "upload"})
    pid = r.json()["project"]["id"]
    assert client.post(f"/api/projects/{pid}/basic").status_code == 200
    b = client.get(f"/api/projects/{pid}/basic").json()
    assert b["status"] == "failed"
    from app import db
    rows = db._query("SELECT id FROM tasks WHERE project_id = ?", (pid,))
    assert len(rows) == 1 and db.get_runs(rows[0]["id"])[0]["attempts"] == 1
