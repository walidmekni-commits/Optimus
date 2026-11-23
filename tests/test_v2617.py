"""OPTIMUS V26.1.7: the surcharge area follows its corners. A model point on a corner of a load moves that corner too
(in PLAXIS and in the run visualization), and the area is geometry (Point, Line, Area, Volume)."""
from app import plaxis_model as pm, study
from tests.test_workflow import client  # noqa: F401  (fixture)


def test_a_point_on_a_load_corner_moves_the_load():
    m = pm.demo("3d")
    cat = {c["key"]: c for c in study.catalog(m)["params"]}
    p12 = cat["pt:Point_12.x"]
    assert p12["ref"] == "Point_12.x; SurfaceLoad_1_1.corner1:x" and p12["loads"] == [["SurfaceLoad_1_1", 1]]
    assert p12["gk"] == "area" and p12["domain"] == "geometry" and "corner C1 of SurfaceLoad_1_1" in p12["label"]
    assert cat["pt:Point_6.z"]["ref"] == "Point_6.z" and cat["pt:Point_6.z"]["gk"] == "point"
    assert cat["corner:SurfaceLoad_1_1.3.y"]["domain"] == "geometry" and cat["area:SurfaceLoad_1_1.size"]["domain"] == "geometry"
    assert cat["load:SurfaceLoad_1_1.sigz"]["domain"] == "loads"
    # the new corners, as PLAXIS gets them: C1 moves with Point_12, C3 by its own value
    new = pm.reshape(m["loads"][0]["points"], [pm.area_edit("corner1:x", 20.0), pm.area_edit("corner3:y", 15.0)])
    assert new[0] == [20.0, 0.0, 0.0] and new[2] == [32.0, 15.0, 0.0] and pm.area_of(new) == 195.0
    m2 = pm.demo("2d")
    c2 = {c["key"]: c for c in study.catalog(m2)["params"]}
    assert c2["pt:Point_13.x"]["ref"] == "Point_13.x; LineLoad_1_1.corner2:x" and c2["pt:Point_13.x"]["gk"] == "line"


def test_the_run_moves_the_load_corner_with_the_point(client):  # noqa: F811
    from tests.test_workflow import new_project
    pid = new_project(client, "sensitivity")
    assert client.put(f"/api/projects/{pid}/plaxis-version", json={"version": "3d"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    r = client.put(f"/api/projects/{pid}/study", json={"mode": "sensitivity", "runs": 2,
                   "params": [{"item": "pt:Point_12.x", "how": "list", "list": "20, 24", "custom": [], "new": ""}],
                   "controls": [{"output": "utot", "how": "max", "value": 0.06}]})
    assert r.status_code == 200 and not r.json()["errors"], r.text
    rows = [x for x in r.json()["rows"] if x["inputs"]]
    assert [x["moves"]["Point_12"] for x in rows] == [[20.0, 0.0, 0.0], [24.0, 0.0, 0.0]]


class _Pt:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = _V(x), _V(y), _V(z)

    def setproperties(self, ax, v):
        getattr(self, ax).value = v


class _V:
    def __init__(self, v):
        self.value = v


def test_reshape_after_the_point_moved_the_corner_already():
    """2D: moving the point moves the line load with it; the corner edit then finds it in place and changes nothing."""
    from app.solvers import PlaxisSolver
    pts = [_Pt(20.0, 0.0, 0.0), _Pt(32.0, 0.0, 0.0)]  # Point_12 already moved from 22 to 20

    class Load:
        Points = pts
        Name = _V("LineLoad_1_1")
    load = Load()
    load.Parent = load
    s = PlaxisSolver.__new__(PlaxisSolver)
    s._find = lambda g_i, name: load
    txt = s._reshape_load(type("G", (), {"gotostructures": lambda self: None})(), "LineLoad_1_1", [("corner", 1, "x", 20.0)], False)
    assert "corners moved" in txt and pts[0].x.value == 20.0 and pts[1].x.value == 32.0
