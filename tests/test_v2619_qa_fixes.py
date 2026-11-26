"""V26.1.9: corrective actions of Ashraf's QA/QC report after editing (24 Nov 2026), defects D1-D9 and D14 (D10-D13 are
the module interface's, in tests/test_revb_interface.py)."""
import pytest

from app import features, insight, plaxis_model, study
from app.solvers import PlaxisSolver, SolverError, _is_safety
from tests.test_studies import _run, _setup
from tests.test_v2619_p1 import G, O, V, _features, solver
from tests.test_workflow import client, new_project  # noqa: F401  (fixture)


class N:
    """A PLAXIS enumerated value given as a number (PLAXIS 3D 2024.3)."""

    def __init__(self, v):
        self.value = v

    def __str__(self):
        return str(self.value)


class Strict(O):
    """A phase that takes only the listed values of one property (as PLAXIS: 'Incorrect new value for: ...')."""

    def __init__(self, name, prop, ok=(), **kw):
        object.__setattr__(self, "_prop", prop)
        object.__setattr__(self, "_ok", tuple(ok))
        object.__setattr__(self, "tried", [])
        super().__init__(name, **kw)

    def __setattr__(self, k, v):
        if k == self._prop:
            self.tried.append(v)
            if v not in self._ok:
                raise RuntimeError(f"Incorrect new value for: {k}")
        object.__setattr__(self, k, v)

    def setproperties(self, *args):
        for k, v in zip(args[::2], args[1::2]):
            setattr(self, k, v)


# D1: the factor of safety of a safety phase whose calculation type PLAXIS gives as a number (7)
def test_d1_safety_phase_given_as_a_number():
    assert _is_safety(O("Phase_6", DeformCalcType=N(7)))
    assert not _is_safety(O("Phase_5", DeformCalcType=N(4)))
    assert _is_safety(O("Phase_7", DeformCalcType=V("Safety")))
    assert plaxis_model.calc_type_name(N(1)) == "K0 procedure" and plaxis_model.calc_type_name("7") == "Safety"
    assert plaxis_model.is_safety_phase({"type": "7", "id": "FoS"}) and not plaxis_model.is_safety_phase({"type": "4", "id": "Dig"})


def test_d1_a_model_with_a_numeric_safety_phase_offers_the_fos():
    m = plaxis_model.demo("3d")
    for ph in m["phases"]:
        ph["type"] = "7" if "safety" in (ph.get("type") or "").lower() else "4"
        ph["id"] = ph["id"].replace("Safety", "FoS").replace("safety", "FoS")
    b = study.build(m, {"mode": "optimize", "params": [{"item": "mat:Clay.phi", "min": 22, "max": 30}],
                        "targets": [{"output": "fos", "operator": ">=", "value": 1.3}], "runs": 3})
    assert not any("needs a safety phase" in e for e in b["errors"]), b["errors"]


def test_d1_the_safety_phase_set_by_its_name_or_number():
    g = G()
    made = []

    def phase(base):
        p = O(V(f"Phase_{len(g.Phases)}"), Identification=V(""), PreviousPhase=base, Deform=O("Deform"))
        g.Phases.append(p)
        made.append(p)
        return p
    g.phase = phase
    solver(g)._add_safety(g, ["Phase_1"])
    assert made[0].DeformCalcType == "Safety"  # the name first, never OPTIMUS' own code 0


# D2: a stage value without its phase label does not stop the results page
def test_d2_stage_values_without_a_phase_label():
    task = {"plan": {"study": {"mode": "reporting"}, "variables": [], "first": 0,
                     "targets": [{"name": "T1", "label": "FoS after Dig", "result": "FoS", "phase": "@safety:Phase_2",
                                  "operator": "report", "value": None, "unit": "", "key": "fos", "stage": True}]},
            "objective": "reporting"}
    runs = [{"status": "succeeded", "inputs": {}, "outputs": {"T1": 2.8}}]
    out = study.results(task, runs)
    assert out["stages"]["phases"] == ["@safety:Phase_2"]


# D3: a study that adds phases calculates Run 0; the dynamic results are read without ticking them
def test_d3_run0_calculated_when_the_study_adds_phases(client):
    pid, prev = _setup(client, "reporting", params=[], outputs=["utot"], runs=1,
                       dynamic={"on": True, "after": "Phase_4", "source": "base"})
    assert prev["errors"] == [], prev["errors"]
    assert any("dynamic" in w.lower() for w in prev.get("warnings") or []), prev.get("warnings")
    tid, out = _run(client, pid)
    keys = {t["key"] for t in out["targets"]}
    assert {"dyn_ax", "dyn_ux"} <= keys
    task = client.get(f"/api/tasks/{tid}").json()
    assert not (task.get("plan") or {}).get("baseline")  # not taken over from the full run


def test_d3_dynamics_refused_by_the_licence_is_said():
    g = G()
    g.displmultiplier = lambda: O("DisplMultiplier_1")
    g.Points = [O("P1", x=V("0"), y=V("-30")), O("P2", x=V("60"), y=V("0"))]
    g.linedispl = lambda *a: [O("LineDisplacement_1"), O("LineDisplacement_1_1")]

    def phase(base):
        p = Strict(V("Phase_3"), "DeformCalcType", (), Identification=V(""), PreviousPhase=base)
        g.Phases.append(p)
        return p
    g.phase = phase
    g.set = lambda *a: (_ for _ in ()).throw(RuntimeError("Incorrect new value"))
    sv = solver(g)
    sv._extent = lambda g_i, is3: (0.0, 60.0, 0.0, 0.0)
    with pytest.raises(SolverError, match="licence does not include Dynamics"):
        sv._add_dynamic(g, {"after": "Phase_2", "source": "base", "amplitude": 0.01, "frequency": 2, "duration": 4}, False)


# D4: the interface 'from adjacent soil' copies the soil next to it
def test_d4_interface_from_adjacent_soil_gets_its_soil():
    for v in ("2d", "3d"):
        m = plaxis_model.demo(v)
        itf = next(x for x in m["interfaces"] if x["side"] == "positive")
        base = plaxis_model.adjacent_soil(m, itf)
        assert base in {x["name"] for x in m["materials"] if x["kind"] == "soil"}, (v, base)
        c = next(c for c in plaxis_model.candidates(m) if c["key"] == f"iface:{itf['name']}.Rinter")
        soils = plaxis_model.adjacent_soils(m, itf)
        assert base in soils and c["plaxis_ref"] == f"{itf['name']}|{','.join(soils)}.ifaceRinter"
    m = plaxis_model.demo("3d")
    itf = dict(m["interfaces"][0], points=[])  # no points read: the plate's
    assert plaxis_model.adjacent_soil(m, itf) in ("", "Clay", "Fill", "Sand")


# D5: the pore pressure calculation type in PLAXIS' own words, and a refusal said plainly
def test_d5_pore_pressure_type_tokens():
    g = G()
    g.Phases[2] = Strict(V("Phase_2"), "PorePresCalcType", ("Steady state groundwater flow",), Identification=V("Dig"))
    g.set = lambda *a: (_ for _ in ()).throw(RuntimeError("Incorrect new value"))
    assert "Steady state groundwater flow" in solver(g)._pore_type(g, "Phase_2", 1)
    assert g.Phases[2].tried[0] == "Steady state groundwater flow"
    g.Phases[2] = Strict(V("Phase_2"), "PorePresCalcType", (), Identification=V("Dig"))
    with pytest.raises(SolverError, match="licence"):
        solver(g)._pore_type(g, "Phase_2", 1)


# D6: |q| from qx, qy, qz when PLAXIS has no Qtot
def test_d6_specific_discharge_from_its_components():
    class R:
        Qx, Qy, Qz = "qx", "qy", "qz"

    class Out:
        def getresults(self, ph, rt, where):
            return {"qx": [3.0, 0.0], "qy": [4.0, 1.0], "qz": [0.0, 2.0]}[rt]
    assert PlaxisSolver.q_total(Out(), None, R, True) == pytest.approx([5.0, 5 ** 0.5])
    assert PlaxisSolver.q_total(Out(), None, R, False) == pytest.approx([5.0, 1.0])


# D7: result points checked before the runs; a point on the surface is tried just inside
def test_d7_result_points_checked_in_the_preview():
    m = plaxis_model.demo("3d")
    s = {"probes": [{"name": "Out", "x": 80, "y": 0, "z": 0}, {"name": "Up", "x": 10, "y": 5, "z": 3},
                    {"name": "Good", "x": 30, "y": 5, "z": -2}]}
    w = features.probe_checks(m, s)
    assert any("Out" in x and "outside the model" in x for x in w) and any("Up" in x for x in w)
    assert not any("Good" in x for x in w)
    b = study.build(m, {"mode": "reporting", "outputs": ["utot"], **s})
    assert any("outside the model" in x for x in b["warnings"])


def test_d7_point_on_the_surface_tried_just_below():
    class Out:
        def getsingleresult(self, ph, rt, pt, *a):
            return 0.004 if pt[1] < 0 else "Not found"
    assert PlaxisSolver._at_point(Out(), None, "Uy", "10;0", False) == pytest.approx(0.004)

    class Never:
        def getsingleresult(self, *a):
            return "Not found"
    with pytest.raises(SolverError, match="not active \\(excavated\\)"):
        PlaxisSolver._at_point(Never(), None, "Uy", "10;-3", False)


# D8: a failed phase said in words; what the run set is kept
def test_d8_loginfo_in_words():
    assert "collapse" in insight.LOGINFO["101"]
    import app.solvers as S
    src = open(S.__file__, encoding="utf-8").read()
    assert "self.last_applied" in src and 'LOGINFO.get(info.strip()' in src
    assert S.deterministic("SolverError: Phase Phase_3 failed: the soil body seems to collapse (PLAXIS LogInfo 101)")


# D9: a result point gives |u| without ticking it
def test_d9_result_point_read_without_ticking():
    m = plaxis_model.demo("2d")
    b = study.build(m, {"mode": "reporting", "outputs": ["utot"], "probes": [{"name": "Corner", "x": 30, "y": -1}]})
    assert any(t["key"] == "probe0:utot" for t in b["targets"])
    b = study.build(m, {"mode": "reporting", "outputs": ["probe0:ux"], "probes": [{"name": "Corner", "x": 30, "y": -1}]})
    assert [t["key"] for t in b["targets"] if t["key"].startswith("probe")] == ["probe0:ux"]


# D14: the features grouped in section 2
def test_d14_features_grouped_in_section_2():
    m = plaxis_model.demo("2d")
    cat = study.catalog(m, _features("2d"))
    dom = {p["key"]: p["domain"] for p in cat["params"]}
    assert dom["add:N1"] == dom["prestress:Anchor@Phase_3"] == dom["design:approach"] == "features"
    assert cat["domains"]["features"] == "PLAXIS features"


# re-QA D5: every enumerated / staged PLAXIS property of the study page reaches its own handler in a run (the
# steady-state flow was set by the generic path because of a misspelt property name)
class _Stop(Exception):
    pass


@pytest.mark.parametrize("v", ["2d", "3d"])
def test_every_special_input_reaches_its_handler(v, tmp_path, monkeypatch):
    import types
    from app.schema import Variable
    m = plaxis_model.demo(v)
    cands = {c["key"]: c for c in plaxis_model.candidates(m)}
    want = {"pore:": "_pore_type", "drainage:": "_drainage", "smodel:": "_soil_model"}
    picked = [(k, c, h) for pre, h in want.items() for k, c in cands.items() if k.startswith(pre)][:12]
    assert {h for *_, h in picked} == set(want.values())
    calls = []
    sv = PlaxisSolver.__new__(PlaxisSolver)
    sv.additions, sv.dynamic, sv.safety_after = {}, None, []
    monkeypatch.setattr(PlaxisSolver, "_base_model", lambda self, f, d: tmp_path / ("m.p3d" if v == "3d" else "m.p2dx"))
    s_i = types.SimpleNamespace(open=lambda p: None)
    monkeypatch.setattr(PlaxisSolver, "_connect", lambda self, ver="3d": (s_i, object()))
    for h in want.values():
        monkeypatch.setattr(PlaxisSolver, h, lambda self, g, name, code, *a, _h=h, **k: calls.append((_h, name)) or _h)

    def generic(self, g, name):
        raise AssertionError(f"{name} went to the generic PLAXIS set")
    monkeypatch.setattr(PlaxisSolver, "_find", generic)
    monkeypatch.setattr(PlaxisSolver, "_p2_geometry", lambda self, *a: (_ for _ in ()).throw(_Stop()))
    import sys
    easy = types.ModuleType("plxscripting.easy")
    easy.new_server = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "plxscripting", types.ModuleType("plxscripting"))
    monkeypatch.setitem(sys.modules, "plxscripting.easy", easy)
    vs = [Variable(name=f"x{i}", label=c["label"], unit="", plaxis_ref=c["plaxis_ref"], kind="values", min=None, max=None,
                   step=None, values=c["values"], current=None, section=c["section"]) for i, (k, c, h) in enumerate(picked)]
    ins = {f"x{i}": float(c["values"][-1].rsplit(": ", 1)[1]) for i, (k, c, h) in enumerate(picked)}
    with pytest.raises(_Stop):
        sv.run([], ins, vs, [], tmp_path)
    assert {h for h, _ in calls} == set(want.values()), calls


# re-QA N1: a PLAXIS 3D surface's points are local to it; the wall is read at x = 20, below the ground
def test_n1_wall_read_in_model_coordinates():
    import types
    P = lambda x, y, z: types.SimpleNamespace(x=x, y=y, z=z)  # noqa: E731
    poly = types.SimpleNamespace(Points=[P(0, 0, 0), P(0, 12, 0), P(0, 12, 13.5), P(0, 0, 13.5)],
                                 LocalOrigin=P(20, 0, 2), LocalAxis1=P(0, -1, 0), LocalAxis2=P(0, 0, -1))
    plate = types.SimpleNamespace(Parent=poly)
    alts = plaxis_model._surface_points(plate, poly, True)
    assert len(alts) >= 2
    m = {"version": "3d", "points": [], "notes": [], "interfaces": [], "loads": [], "contractions": [], "displacements": [],
         "boreholes": [{"name": "BH_1", "x": 0, "y": 0, "layers": [{"name": "L1", "material": "Fill", "top": 2, "bottom": -3},
                                                                  {"name": "L2", "material": "Sand", "top": -3, "bottom": -30}]}],
         "structures": [{"kind": "plate", "name": "Plate_1", "points": alts[0], "_alts": alts},
                        {"kind": "anchor", "name": "Anchor_1", "points": [[20, -6, -1], [32, -6, -7]]},
                        {"kind": "beam", "name": "Beam_1", "points": [[20, 0, 1], [20, -12, 1]]}]}
    m["interfaces"].append({"name": "PositiveInterface_1", "side": "positive", "points": alts[0], "_alts": list(alts)})
    plaxis_model._resolve_globals(m)
    wall = m["structures"][0]["points"]
    assert all(abs(q[0] - 20) < 1e-6 for q in wall)
    assert min(q[1] for q in wall) == pytest.approx(-12) and max(q[2] for q in wall) == pytest.approx(2)
    assert "_alts" not in m["structures"][0] and not m["notes"]
    assert plaxis_model.adjacent_soil(m, m["interfaces"][0]) == "Sand"  # D4: mid level -4.75 m, in the ground
    # a surface with no local frame is kept as read, and said when it lies above the ground
    m2 = dict(m, notes=[], structures=[{"kind": "plate", "name": "Plate_2", "points": alts[-1], "_alts": [alts[-1]]}],
              interfaces=[])
    plaxis_model._resolve_globals(m2)
    assert any("Plate_2" in n for n in m2["notes"])


# re-QA D7: the soil contour, not the points and structures, bounds the model
def test_d7_soil_contour_bounds_the_result_points():
    m = plaxis_model.demo("3d")
    m["contour"] = {"xmin": 0, "xmax": 100, "ymin": 0, "ymax": 20}
    w = features.probe_checks(m, {"probes": [{"name": "Far", "x": 80, "y": 5, "z": -2}]})
    assert not any("Far" in x for x in w), w
    m.pop("contour")
    w = features.probe_checks(m, {"probes": [{"name": "Far", "x": 80, "y": 5, "z": -2}]})
    assert any("beyond the points and structures" in x for x in w) and not any("outside the model" in x for x in w)


# re-QA N2: only a lost link to PLAXIS is retried; a collapse, a refused value or a missing material is not
def test_n2_errors_that_repeat_are_not_retried():
    import app.solvers as S
    for e in ("SolverError: PositiveInterface_1: no material to copy for its own interface properties",
              "PlxScriptingError: Incorrect new value for: PorePresCalcType",
              "SolverError: Phase Phase_3 failed: the soil body seems to collapse (PLAXIS LogInfo 101)",
              "PlxScriptingError: Phase_3: Soil body seems to collapse", "KeyError: 'Name'"):
        assert S.deterministic(e), e
    for e in ("ConnectionError: PLAXIS did not answer", "requests.exceptions.ReadTimeout: timed out",
              "PermissionError: [WinError 32] The process cannot access the file because it is being used by another process"):
        assert not S.deterministic(e), e


# re-test N1 (UA-22, PLAXIS 3D 2024.3): the Polygon's own x, y, z is where its local points start; AxisVector 0, 0, 0
def test_n1_polygon_origin_added_to_its_local_points():
    import types
    P = lambda x, y, z: types.SimpleNamespace(x=x, y=y, z=z)  # noqa: E731

    class Poly:  # a PLAXIS Polygon: iterable over its local points, not a list
        def __init__(self, pts):
            self._pts = pts

        def __iter__(self):
            return iter(self._pts)

    def poly(org, pts, cog):
        p = Poly([P(*q) for q in pts])
        p.x, p.y, p.z = org
        p.PlaneDefinition, p.AxisVectorX, p.AxisVectorY, p.AxisVectorZ = 0, 0, 0, 0
        p.CenterOfGravity = P(*cog)
        return p
    p1 = poly((20, -12, -11.5), [(0, 0, 0), (0, 12, 0), (0, 12, 13.5), (0, 0, 13.5)], (20, -6, -4.75))
    p2 = poly((21, -12, 2), [(0, 0, 0), (14, 0, 0), (14, 12, 0), (0, 12, 0)], (28, -6, 2))
    plate, load = types.SimpleNamespace(Parent=p1), types.SimpleNamespace(Parent=p2)
    m = {"version": "3d", "points": [], "notes": [], "contractions": [], "displacements": [],
         "boreholes": [{"name": "BH_1", "x": 0, "y": 0, "layers": [{"name": "L1", "material": "Fill", "top": 2, "bottom": -3},
                                                                  {"name": "L2", "material": "Sand", "top": -3, "bottom": -30}]}]}
    a = plaxis_model._surface_points(plate, p1, True)
    b = plaxis_model._surface_points(load, p2, True)
    m["structures"] = [{"kind": "plate", "name": "Plate_1", "points": a[0], "_alts": a, "_cog": plaxis_model._cog(plate, p1)}]
    m["interfaces"] = [{"name": "PositiveInterface_1", "side": "positive", "points": a[0], "_alts": list(a),
                        "_cog": plaxis_model._cog(plate, p1)}]
    m["loads"] = [{"kind": "surface", "name": "SurfaceLoad_1", "props": {}, "points": b[0], "_alts": b,
                   "_cog": plaxis_model._cog(load, p2)}]
    plaxis_model._resolve_globals(m)
    wall, sl = m["structures"][0]["points"], m["loads"][0]["points"]
    assert {q[0] for q in wall} == {20} and sorted({q[1] for q in wall}) == [-12, 0] and sorted({q[2] for q in wall}) == [-11.5, 2]
    assert sorted({q[0] for q in sl}) == [21, 35] and sorted({q[1] for q in sl}) == [-12, 0] and {q[2] for q in sl} == {2}
    assert not m["notes"] and plaxis_model.adjacent_soil(m, m["interfaces"][0]) == "Sand"
    plaxis_model.finish(m)
    assert m["extents"]["zmax"] == pytest.approx(2)


# re-test N5: the quality chapter of a 3D model with soil clusters (their names) and a failed run that kept only
# _soil_models; and a run that is not retried is calculated once (N2: the missing break)
def test_n5_quality_with_clusters_and_a_failed_run(client, monkeypatch):
    from app import db, exports, solvers
    m = plaxis_model.demo("3d")
    m["clusters"] = ["Soil_1_1", "Soil_2_1"]
    assert "Soil_1_1" in exports.model_objects(m)[-1][2]
    pid, prev = _setup(client, "combinations", params=[{"item": "mat:Clay.phi", "min": 22, "max": 30}], outputs=["utot"], runs=2)
    p = db.get_project(pid)
    db.update_project(pid, setup={**p["setup"], "plaxis_model": {**p["setup"]["plaxis_model"], "clusters": ["Soil_1_1"]}})
    real, n = solvers.SimulatedSolver.run, {"k": 0}

    def run(self, *a, **k):
        n["k"] += 1
        if n["k"] == 2:
            self.last_applied = {"_soil_models": ["L2MedDenseSand: drainage type Undrained (A)"]}
            raise solvers.SolverError("Phase Phase_3 failed: the soil body seems to collapse (PLAXIS LogInfo 101)")
        self.last_applied = None
        return real(self, *a, **k)
    monkeypatch.setattr(solvers.SimulatedSolver, "run", run)
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert n["k"] == 2 and [r["status"] for r in out["rows"]] == ["succeeded", "failed"]
    from tests.test_workflow import events
    assert out.get("quality") and "quality" not in events(client, pid), "the quality chapter failed"


# E1: the interface 'from adjacent soil' along several layers keeps one material per layer
def test_e1_interface_soils_per_layer_offered():
    m = plaxis_model.demo("3d")
    itf = next(x for x in m["interfaces"] if x["side"] == "positive")
    soils = plaxis_model.adjacent_soils(m, itf)
    assert len(soils) >= 2 and soils == [l_["material"] for l_ in sorted(m["boreholes"][0]["layers"], key=lambda x: -x["top"])
                                         if l_["material"] in soils]
    c = next(c for c in plaxis_model.candidates(m) if c["key"] == f"iface:{itf['name']}.Rinter")
    assert "a copy of the layer at its mid level" in c["label"] and ",".join(soils) in c["plaxis_ref"]


def _iface_pc(levels):
    """A fake PLAXIS 3D: a wall interface along three layers, cut into staged pieces at the given levels."""
    import types
    from tests.test_v2619_p1 import Staged
    g = G()

    class Soil(O):
        _plx_type = "SoilMat"
    P = lambda x, y, z: types.SimpleNamespace(x=x, y=y, z=z)  # noqa: E731
    mats = {n: Soil(n, SoilModel=2, Rinter=0.67, phi=p, cref=c, Identification=n)
            for n, p, c in (("L1Sand", 32.0, 0.0), ("L2Sand", 34.0, 0.0), ("L3Sandstone", 38.0, 130.0))}
    g.Materials = list(mats.values())
    for n, x in mats.items():
        setattr(g, n, x)

    class Poly:
        def __init__(self, pts, org, cog):
            self._p, (self.x, self.y, self.z), self.CenterOfGravity = [P(*q) for q in pts], org, P(*cog)

        def __iter__(self):
            return iter(self._p)
    wall = Poly([(0, 0, 0), (0, 12, 0), (0, 12, 13.5), (0, 0, 13.5)], (20, -12, -11.5), (20, -6, -4.75))
    g.PositiveInterface_1 = O("PositiveInterface_1", MaterialMode="From adjacent soil", Parent=wall)
    g.PositiveInterfaces = [O(f"PositiveInterface_1_{i + 1}", Material=Staged(), MaterialMode=Staged(),
                              Parent=types.SimpleNamespace(CenterOfGravity=P(20, -6, z)) if z is not None else None)
                            for i, z in enumerate(levels)]
    for p_ in g.PositiveInterfaces:
        if p_.Parent is None:
            object.__setattr__(p_, "Parent", p_)

    def boreholes(g_i, is3):
        return [{"name": "BH_1", "x": 0, "y": 0, "head": 0, "layers": [
            {"name": "Soillayer_1", "material": "L1Sand", "top": 2, "bottom": -1},
            {"name": "Soillayer_2", "material": "L2Sand", "top": -1, "bottom": -5},
            {"name": "Soillayer_3", "material": "L3Sandstone", "top": -5, "bottom": -20}]}]
    made = []

    def soilmat():
        x = Soil(f"SoilMat_{len(made) + 20}")
        made.append(x)
        return x
    g.soilmat = soilmat
    return g, mats, made, boreholes


def test_e1_several_layers_one_copy_of_the_mid_layer(monkeypatch):
    """Re-tests of d5016d6 and 9be0a91: one copy per layer, and the values set on the soils themselves, both made PLAXIS
    24.3 fail at gotostages. Along several layers: one copy of the layer at the interface's mid level (eb9ec48)."""
    g, mats, made, bh = _iface_pc([0.5, -3.0, -8.25])
    monkeypatch.setattr(plaxis_model, "read_boreholes", bh)
    done = solver(g)._interfaces(g, {"PositiveInterface_1|L1Sand,L2Sand,L3Sandstone": {"Rinter": 0.5}})
    assert len(made) == 1 and made[0].Rinter == 0.5 and made[0].phi == 34.0  # mid level -4.75 m: L2Sand
    assert all(x.Rinter == 0.67 for x in mats.values())  # the soils themselves are not changed
    assert "a copy of the adjacent soil L2Sand" in done[0] and "L1Sand, L2Sand, L3Sandstone" in done[0]


# Walid / Ashraf: the run that always fails at "Anchor Excavation" (L2 medium dense sand switched to Undrained (A))
def test_undrained_sand_said_before_and_after_the_run():
    from app import insight
    m = plaxis_model.demo("3d")
    m["materials"].append({"name": "L2MedDenseSand", "id": "L2MedDenseSand", "kind": "soil",
                           "props": {"phi": 34.0, "cref": 0.0, "gammaUnsat": 18.0}})
    b = study.build(m, {"mode": "combinations", "params": [{"item": "drainage:L2MedDenseSand", "how": "list",
                                                             "list": "Undrained (A): 1"}], "outputs": ["utot"], "runs": 1})
    assert any("L2MedDenseSand drains freely" in w and "Consolidation" in w for w in b["warnings"]), b["warnings"]
    run = {"error": "SolverError: Phase Phase_3 failed: the soil body seems to collapse (PLAXIS LogInfo 101). The PLAXIS "
                    "file of this run is kept to look at the mechanism: UA-22_run_003_failed.p3d.",
           "outputs": {"_soil_models": ["L2MedDenseSand: drainage type Undrained (A)"]}, "inputs": {}}
    d = insight.diagnose(run, [])
    assert "Phase_3" in d["reason"] and "L2MedDenseSand (Undrained (A))" in d["reason"] and "not an error of OPTIMUS" in d["reason"]
    assert "Drained" in d["solution"] and "_failed" in d["solution"]
    clay = study.build(m, {"mode": "combinations", "params": [{"item": "drainage:Clay", "how": "list", "list": "Drained: 0"}],
                           "outputs": ["utot"], "runs": 1})
    assert not any("drains freely" in w for w in clay["warnings"])


# Walid 25 Nov 2026 (QA on UA-22 run_002): a Plastic phase that collapses with an Undrained (A) soil is calculated
# again once as Consolidation (7 days by default), flagged in the run record and the report
def test_undrained_collapse_recalculated_as_consolidation():
    import types
    from app import exports, features
    from app.solvers import PlaxisSolver

    class V:
        def __init__(self, v):
            self.value = v

    class Ph:
        def __init__(self, name, kind):
            self.Name, self.DeformCalcType, self.LogInfo, self.ShouldCalculate = V(name), V(kind), V("0"), True
            self.TimeInterval = None

        def __setattr__(self, k, v):
            object.__setattr__(self, k, V(v) if k in ("DeformCalcType", "TimeInterval") and not isinstance(v, V) and v is not None else v)
    p1, p3 = Ph("Phase_1", "Plastic"), Ph("Phase_3", "Plastic")
    calls = []

    def calculate():
        calls.append(1)
        p3.LogInfo = V("101" if p3.DeformCalcType.value == "Plastic" else "0")
        if p3.LogInfo.value != "0":
            raise RuntimeError("Phase_3 failed")
    g = types.SimpleNamespace(Phases=[p1, p3], Materials=[types.SimpleNamespace(DrainageType=V("Undrained (A)"))],
                              calculate=calculate, gotostages=lambda: None)
    solver = PlaxisSolver.__new__(PlaxisSolver)
    try:
        calculate()
        err = None
    except RuntimeError as e:
        err = e
    done, err = solver._retry_collapse(g, 7.0, err)
    assert err is None and p3.DeformCalcType.value == "consolidation" and p3.TimeInterval.value == 7.0
    assert done == ["Phase_3 recalculated as Consolidation (7 days) because undrained plastic collapsed (LogInfo 101)"]
    assert len(calls) == 2 and p1.DeformCalcType.value == "Plastic"

    # no undrained soil: nothing is changed
    p3.DeformCalcType, p3.LogInfo = "Plastic", V("101")
    g.Materials = [types.SimpleNamespace(DrainageType=V("Drained"))]
    assert solver._retry_collapse(g, 7.0, None)[0] == [] and p3.DeformCalcType.value == "Plastic"

    # the study: on by default, its time interval a variable, described, and the runs flagged in the report
    m = {"version": "3d", "materials": [{"name": "L2", "drainage": "Undrained (A)"}], "phases": []}
    assert features.clean({})["collapse"] == {"on": True, "days": 7.0}
    assert features.solver_options(m, {})["collapse"] == {"on": True, "days": 7.0}
    c = [x for x in features.candidates(m, {}) if x["key"] == "collapse:days"]
    assert c and c[0]["plaxis_ref"] == "Collapse.days" and c[0]["current"] == 7.0
    assert not [x for x in features.candidates(m, {"collapse": {"on": False}}) if x["key"] == "collapse:days"]
    assert any("Consolidation over 7 days" in r[1] for r in features.describe(m, {}))
    runs = [{"no": 2, "outputs": {"_collapse_retry": done, "_stages_applied": ["x"] + done}}]
    assert exports.collapse_rows(runs) == [["Run 2", done[0]]]
    assert exports.stages_rows(runs)[1][1].startswith("⚠ Phase_3 recalculated")


def test_undrained_warning_says_what_happens_on_collapse():
    from app import study
    m = {"materials": [{"name": "L2MedDenseSand", "props": {"phi": 34, "cref": 0}}]}
    assert "calculates it again once as Consolidation over 7 days" in study.undrained_note(m, "L2MedDenseSand", {})
    assert "switched off" in study.undrained_note(m, "L2MedDenseSand", {"collapse": {"on": False}})


def test_collapse_retry_with_numeric_calc_type_and_drainage_variable():
    """Re-test of e56a560: PLAXIS 3D 2024.3 gives DeformCalcType as a number (4 = Plastic); the days variable is
    offered when a study variable makes a soil undrained (UA-22: L2 drained in the model)."""
    import types
    from app import features
    from app.solvers import PlaxisSolver

    class V:
        def __init__(self, v):
            self.value = v
    class Ph(types.SimpleNamespace):
        def __setattr__(self, k, v):
            if k == "DeformCalcType" and isinstance(v, str):
                raise ValueError("expects a number")  # this PLAXIS takes only the code
            super().__setattr__(k, v)
    ph = Ph(Name=V("Phase_3"), DeformCalcType=V(4), LogInfo=V("101"), ShouldCalculate=True, TimeInterval=V(0))
    sets = []

    def g_set(prop, val):
        sets.append(val)
        if val == 6:
            ph.DeformCalcType = V(6)

    def calculate():
        ph.LogInfo = V("0" if ph.DeformCalcType.value == 6 else "101")
    g = types.SimpleNamespace(Phases=[ph], Materials=[types.SimpleNamespace(DrainageType=V(1))], calculate=calculate,
                              gotostages=lambda: None, set=g_set)
    solver = PlaxisSolver.__new__(PlaxisSolver)
    done, err = solver._retry_collapse(g, 7.0, RuntimeError("Phase_3 failed"))
    assert err is None and ph.DeformCalcType.value == 6 and done and done[0].startswith("Phase_3 recalculated")

    m = {"version": "3d", "materials": [{"name": "L2", "drainage": "Drained"}], "phases": []}
    assert not [x for x in features.candidates(m, {}) if x["key"] == "collapse:days"]
    s = {"puzzle": {"on": True, "runs": [[{"item": "drainage:L2", "value": "Undrained (A): 1"}]]}}
    assert [x for x in features.candidates(m, s) if x["key"] == "collapse:days"]
    assert features.undrained_in({"materials": [{"drainage": "1"}]}, {})


def test_output_port_zero_falls_back(monkeypatch):
    """Re-test of e56a560: g_i.view() gave port 0 with an old Output window open: asked again, then the last Output
    port and the ports of the Output windows; the Input port itself is never used."""
    import sys
    import types
    from app import solvers, settings
    from app.solvers import PlaxisSolver
    tried = []

    def new_server(host, port, password=""):
        tried.append(port)
        if port != 10005:
            raise RuntimeError("secured")
        return None, types.SimpleNamespace(Phases=[1])
    monkeypatch.setitem(sys.modules, "plxscripting.easy", types.SimpleNamespace(new_server=new_server))
    monkeypatch.setitem(sys.modules, "plxscripting", types.ModuleType("plxscripting"))
    monkeypatch.setattr(solvers.time, "sleep", lambda s: None)
    monkeypatch.setattr(solvers, "_output_window_ports", lambda: [10000, 10005])
    saved = {}
    monkeypatch.setattr(settings, "load", lambda: {})
    monkeypatch.setattr(settings, "save", lambda d: saved.update(d))
    solver = PlaxisSolver.__new__(PlaxisSolver)
    solver.host, solver.port, solver.password = "localhost", 10000, "pw"
    g = types.SimpleNamespace(view=lambda ph: 0)
    g_o = solver._output(g, None)
    assert g_o.Phases == [1] and 10000 not in tried and tried[-1] == 10005 and saved == {"output_port": 10005}


def test_r6_consolidation_is_code_6_and_the_switch_is_accepted():
    """R6: PLAXIS 3D 2024.3 stores Consolidation as 6 after setattr(DeformCalcType, 'consolidation'): the retry
    takes it as switched (it was 4 = Plastic) and calculates again."""
    import types
    from app.plaxis_model import calc_type_name
    from app.solvers import PlaxisSolver
    assert calc_type_name(6) == "Consolidation" and calc_type_name(4) == "Plastic" and calc_type_name(5) == "5"

    class V:
        def __init__(self, v):
            self.value = v

    class Ph(types.SimpleNamespace):
        def __setattr__(self, k, v):
            if k == "DeformCalcType" and isinstance(v, str):
                v = V({"consolidation": 6, "plastic": 4}[v.lower()])  # PLAXIS keeps the code
            super().__setattr__(k, v)
    ph = Ph(Name=V("Phase_3"), DeformCalcType=V(4), LogInfo=V("101"), ShouldCalculate=True, TimeInterval=V(0))
    calls = []

    def calculate():
        calls.append(1)
        ph.LogInfo = V("0" if ph.DeformCalcType.value == 6 else "101")
    g = types.SimpleNamespace(Phases=[ph], Materials=[types.SimpleNamespace(DrainageType=V(1))], calculate=calculate,
                              gotostages=lambda: None, set=lambda p_, v: None)
    done, err = PlaxisSolver.__new__(PlaxisSolver)._retry_collapse(g, 7.0, RuntimeError("Phase_3 failed"))
    assert err is None and calls == [1] and ph.DeformCalcType.value == 6 and done
