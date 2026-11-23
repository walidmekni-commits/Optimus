"""OPTIMUS V26.1.5: failure reason and proposed solution of a failed run, the effect of each variable input on the
control parameters, proposed combinations after the first runs, and the richer report."""
import io
import json

from app import db, insight
from tests.test_studies import PHI, _run, _setup
from tests.test_workflow import client  # noqa: F401  (fixture)

SAND = {"item": "mat:Sand.phi", "min": 28, "max": 38, "custom": [], "new": ""}


def test_failure_reason_and_proposed_solution():
    v = [{"name": "V1", "label": "Clay φ'", "unit": "°", "kind": "range", "min": 22, "max": 34, "current": 28}]
    d = insight.diagnose({"error": "SolverError: Phase Phase_3 failed (LogInfo 101)", "inputs": {"V1": 22}}, v)
    assert "did not converge" in d["reason"] and "collapse" in d["reason"]
    assert "Max steps" in d["solution"] and "lowest Clay φ'" in d["solution"]
    d = insight.diagnose({"error": "SolverError: Could not reach the PLAXIS remote scripting server at localhost:10000."})
    assert "connection" in d["reason"] and "Restart" in d["solution"]
    d = insight.diagnose({"error": ""})
    assert "no error message" in d["reason"]


def test_effects_and_proposals_after_the_first_runs(client):
    pid, _ = _setup(client, "sensitivity", params=[PHI, SAND],
                    controls=[{"output": "fos", "how": "fixed", "value": 1.5}, {"output": "utot", "how": "max", "value": 0.06}],
                    runs=7, puzzle={"on": True, "runs": [[{"item": "mat:Clay.phi", "v": 22}], [{"item": "mat:Clay.phi", "v": 34}],
                                                          [{"item": "mat:Sand.phi", "v": 28}], [{"item": "mat:Sand.phi", "v": 38}],
                                                          [{"item": "mat:Clay.phi", "v": 30}, {"item": "mat:Sand.phi", "v": 34}]]})
    tid, out = _run(client, pid)
    eff = out["effects"]
    assert eff and {e["label"] for e in eff[0]["items"]} >= {"Clay φ'", "Sand φ'"} or len(eff[0]["items"]) >= 1
    it = eff[0]["items"][0]
    assert it["rank"] == 1 and it["direction"] in ("increase", "decrease") and "share" in it
    pr = out["proposals"]
    assert pr and pr[0]["inputs"] and pr[0]["predicted"] and pr[0]["why"]
    # the Word report has the chapters
    from docx import Document
    doc = Document(io.BytesIO(client.get(f"/api/tasks/{tid}/report.docx").content))
    heads = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading 1")]
    assert any("Effect of each variable" in h for h in heads) and any("Proposed combinations" in h for h in heads)
    assert any("Calculation quality" in h for h in heads)


def test_a_failed_run_shows_why(client):
    pid, _ = _setup(client, "sensitivity", params=[PHI], controls=[{"output": "fos", "how": "fixed", "value": 1.5}], runs=3)
    tid, _ = _run(client, pid)
    r = db.get_runs(tid)[1]
    db.update_run(r["id"], status="failed", error="SolverError: Simulated calculation did not converge")
    out = client.get(f"/api/tasks/{tid}/study").json()
    bad = [x for x in out["rows"] if x["status"] == "failed"]
    assert bad and bad[0]["reason"].startswith("The PLAXIS calculation did not converge") and bad[0]["solution"]
    from docx import Document
    doc = Document(io.BytesIO(client.get(f"/api/tasks/{tid}/report.docx").content))
    cells = {c.text for t in doc.tables for row in t.rows for c in row.cells}
    assert "Failure reason" in cells and "Proposed solution" in cells


def test_the_surcharge_area_can_be_reduced_or_extended(client):
    from tests.test_workflow import new_project
    pid = new_project(client, "sensitivity")
    assert client.put(f"/api/projects/{pid}/plaxis-version", json={"version": "3d"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    from app import study
    m = client.get(f"/api/projects/{pid}/plaxis-model").json()["model"]
    keys = {c["key"]: c for c in study.catalog(m)["params"]}
    ed = next(k for k in keys if k.startswith("area:") and k.endswith(".xmax"))
    assert keys[ed]["domain"] == "geometry" and any(k.startswith("corner:") for k in keys)
    r = client.put(f"/api/projects/{pid}/study", json={"mode": "sensitivity", "runs": 3,
                   "params": [{"item": ed, "how": "list", "list": "28, 36", "custom": [], "new": ""}],
                   "controls": [{"output": "utot", "how": "max", "value": 0.06}]})
    assert r.status_code == 200 and not r.json()["errors"], r.text
    row = next(x for x in r.json()["rows"] if x["inputs"])
    assert any("corner" in n or n.startswith("Point") for n in row["moves"])
    from app.plaxis_model import reshape
    assert reshape([[0, 0], [10, 0], [10, 5], [0, 5]], [("x", "max", 6)]) == [[0, 0, 0], [6, 0, 0], [6, 5, 0], [0, 5, 0]]


class _V:
    def __init__(self, v):
        self.value = v


class _Pt:
    def __init__(self, x, y, z, fixed=False):
        self.x, self.y, self.z, self.fixed = _V(x), _V(y), _V(z), fixed

    def setproperties(self, k, v):
        if self.fixed:
            raise RuntimeError("polygon points cannot be edited")
        getattr(self, k).value = v


class _Poly:
    def __init__(self, pts):
        self.Points = pts


class _Load:
    def __init__(self, parent, name="SurfaceLoad_1_1"):
        self.Parent, self.Name, self.sigz, self.Active = parent, _V(name), _V(-10.0), {}

    def setproperties(self, k, v):
        setattr(self, k, _V(v))


def test_the_new_area_is_applied_in_plaxis():
    from app.solvers import PlaxisSolver
    sv = PlaxisSolver.__new__(PlaxisSolver)
    pts = [_Pt(22, 0, 0), _Pt(32, 0, 0), _Pt(32, 20, 0), _Pt(22, 20, 0)]
    ld = _Load(_Poly(pts))
    sv._find = lambda g, n: ld

    class G:
        Phases = []

        def gotostructures(self):
            pass
    txt = sv._reshape_load(G(), "SurfaceLoad_1_1", [("x", "max", 28.0), ("y", "max", 15.0)], True)
    assert "corners moved" in txt and pts[1].x.value == 28.0 and pts[2].y.value == 15.0 and pts[0].x.value == 22

    # a PLAXIS that does not let the polygon points move: the load is drawn again with its value and stages
    fixed = _Load(_Poly([_Pt(22, 0, 0, True), _Pt(32, 0, 0, True), _Pt(32, 20, 0, True), _Pt(22, 20, 0, True)]))
    ph = _V("Phase_1"); ph.Name = _V("Phase_1")
    fixed.Active = {ph: True}
    made, deleted = [], []

    class G2:
        Phases = [ph]

        def gotostructures(self):
            pass

        def gotostages(self):
            pass

        def surfload(self, *coords):
            new = _Load(_Poly([_Pt(*c) for c in coords]), "SurfaceLoad_2_1")
            made.append((coords, new))
            return [new.Parent, new]

        def delete(self, o):
            deleted.append(o)

        def activate(self, o, p):
            o.Active[p] = True
    sv._find = lambda g, n: fixed
    txt = sv._reshape_load(G2(), "SurfaceLoad_1_1", [("x", "min", 24.0)], True)
    coords, new = made[0]
    assert "drawn again" in txt and coords[0] == (24.0, 0.0, 0.0) and deleted == [fixed.Parent]
    assert new.sigz.value == -10.0 and new.Active[ph] is True


def test_the_whole_area_is_one_value_and_geometry_is_point_line_area_volume():
    from app import plaxis_model as pm, study
    m = pm.demo("3d")
    cat = {c["key"]: c for c in study.catalog(m)["params"]}
    size = cat["area:SurfaceLoad_1_1.size"]
    assert size["current"] == 200 and size["unit"] == "m²" and size["ref"] == "SurfaceLoad_1_1.areasize1"  # C1 is by the wall
    assert size["gk"] == "area" and cat["pt:Point_6.z"]["gk"] == "point" and any(c.get("gk") == "volume" for c in cat.values())
    assert any(c.get("gk") == "line" for c in cat.values())
    mv = pm.load_corner_moves(m, [{"name": "V", "plaxis_ref": size["ref"]}], {"V": 50})
    assert mv["Point_13"] == [27.0, 0.0, 0.0] and mv["SurfaceLoad_1_1 corner 4"] == [22.0, 10.0, 0.0]  # half each way, C1 stays
    assert pm.area_edit("areasize2", 80.0) == ("size", 2, 80.0) and pm.area_edit("corner3:y", 1.0) == ("corner", 3, "y", 1.0)
    m2 = pm.demo("2d")
    ln = next(c for c in study.catalog(m2)["params"] if c["key"] == "area:LineLoad_1_1.size")
    assert ln["unit"] == "m" and ln["gk"] == "line"
