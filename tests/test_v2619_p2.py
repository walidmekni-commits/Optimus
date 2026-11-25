"""V26.1.9 batch 2: the P2 features of the QA/QC "Report for Missing Features" (other common projects: embankments,
piles and anchors, cables, staged fill / excavation, local mesh refinement, prescribed displacements, phase settings,
load factor and rainfall per stage, more soil models, earthquake records, profiles, temperature, uplift). The PLAXIS
commands are checked with a fake PLAXIS; every module runs them with the simulated solver and reports them."""
import io
import math
import re
import zipfile

import pytest

from app import exports, features, geosel, plaxis_model, study
from app.solvers import PlaxisSolver, SolverError, hoek_brown_mc
from tests.test_studies import _run, _setup
from tests.test_v2619_p1 import G, O, Staged, V, solver
from tests.test_workflow import client, new_project  # noqa: F401  (fixture)


def _cands(v):
    return {c["key"]: c for c in plaxis_model.candidates(plaxis_model.demo(v))}


# --------------------------------------------------------------------------------------------------------------
# what the study page offers
@pytest.mark.parametrize("v", ["2d", "3d"])
def test_p2_inputs_are_offered(v):
    c = _cands(v)
    up = "z" if v == "3d" else "y"
    ln = c["len:NodeToNodeAnchor_1"]
    assert "Point_8.x+*" in ln["plaxis_ref"] and "Point_9.x+*" in ln["plaxis_ref"]  # the grout body moves with the free end
    assert ln["current"] == pytest.approx(math.hypot(12, 6), rel=1e-4)
    assert c["len:Geogrid_1"]["plaxis_ref"].startswith("Point_9.")
    assert not any(k.startswith("slope:Line_") and "Point_7|Point_8" in x["plaxis_ref"] for k, x in c.items())  # not the anchor
    assert c["cf:Plate_1"]["plaxis_ref"] == "Plate_1.CoarsenessFactor" and c["cf:Plate_1"]["section"] == "mesh"
    assert c["init:calc"]["values"][0] == "K0 procedure: 0" and ("Field stress: 2" in c["init:calc"]["values"]) == (v == "3d")
    assert c["calc:Phase_2"]["values"][1] == "Consolidation: 1"
    assert c["lfactor:@Phase_2"]["plaxis_ref"] == "Loads.factor@Phase_2"
    assert c["rain:@Phase_2"]["plaxis_ref"] == "Precipitation.q@Phase_2" and c["rain:@Phase_2"]["section"] == "flow"
    assert c["thermal:Phase_2"]["values"][-1] == "Steady state thermal flow: 3"
    sm = next(x for k, x in c.items() if k.startswith("smodel:"))
    assert "Soft Soil: 5" in sm["values"] and "MCC: 8" in sm["values"]
    if v == "2d":
        assert c["place:Soil_1_1"]["values"][0] == "as in the model: 0" and c["dig:Soil_1_1"]["plaxis_ref"] == "Soil_1_1.ExcavatedIn"
    assert up in "yz"


def test_p2_domains_in_section_2():
    m = plaxis_model.demo("2d")
    dom = {p["key"]: p["domain"] for p in study.catalog(m, {})["params"]}
    assert dom["len:NodeToNodeAnchor_1"] == "geometry" and dom["cf:Plate_1"] == "mesh"
    assert dom["calc:Phase_2"] == dom["place:Soil_1_1"] == "stages" and dom["rain:@Phase_2"] == "water"
    assert dom["thermal:Phase_2"] == "thermal"
    outs = {o["key"]: o["domain"] for o in study.catalog(m, {})["outputs"]}
    assert outs["settle_profile"] == outs["wall_profile"] == "displacements" and outs["temp"] == "thermal"


def test_rock_material_switches_to_its_mohr_coulomb_equivalent():
    m = plaxis_model.demo("2d")
    m["materials"].append({"name": "Rock", "id": "Granite", "kind": "soil", "model": "Hoek-Brown",
                           "props": {"SigmaCi": 50000.0, "GSI": 50.0, "mi": 10.0, "gammaUnsat": 26.0}})
    sm = next(c for c in plaxis_model.candidates(m) if c["key"] == "smodel:Rock")
    assert sm["values"] == ["Hoek-Brown: 10", "MC (equivalent to Hoek-Brown): 2"]


def test_hoek_brown_equivalent_mohr_coulomb():
    c, phi, info = hoek_brown_mc(50000, 50, 10, 0)
    assert 2000 < c < 3000 and 28 < phi < 33 and "mb 1.677" in info  # mb = mi exp((GSI-100)/28)
    c2, phi2, _ = hoek_brown_mc(50000, 70, 10, 0)
    assert c2 > c and phi2 > phi  # a better rock mass is stronger
    c3, phi3, _ = hoek_brown_mc(50000, 50, 10, 1.0)
    assert c3 < c and phi3 < phi  # a blast-damaged one is weaker


def test_slope_angle_moves_the_top_in_the_run_view():
    m = plaxis_model.demo("2d")
    m["points"] += [{"name": "T1", "x": 50.0, "y": 5.0}, {"name": "T0", "x": 40.0, "y": 0.0}]
    v = {"name": "p1", "plaxis_ref": "T1|T0.slopeangle"}
    mv = geosel.displace(m, [v], {"p1": 45})
    assert mv["T1"][0] == pytest.approx(45.0) and mv["T1"][1] == pytest.approx(5.0)


# --------------------------------------------------------------------------------------------------------------
# PLAXIS commands, with a fake PLAXIS
def test_p2_values_are_collected_and_applied():
    g = G()
    g.Point_7 = O("Point_7", x=V("30"), y=V("5"))
    g.Point_6 = O("Point_6", x=V("20"), y=V("0"))
    g.Plate_1 = O("Plate_1")
    sv = solver(g)
    st = {"slopes": [], "clusters": [], "coarse": {}, "displ": [], "init": None, "calc": [], "thermal": [], "lfactor": {}, "rain": {}}
    assert sv._p2_take(st, "Point_7|Point_6", "slopeangle", "", 30.0)
    assert sv._p2_take(st, "Plate_1", "CoarsenessFactor", "", 0.25)
    assert not sv._p2_take(st, "Clay", "phi", "", 30.0)
    moved = {}
    done, remesh = sv._p2_geometry(g, st, False, moved)
    assert remesh and float(g.Point_7.x) == pytest.approx(20 + 5 / math.tan(math.radians(30)))
    assert moved[("Point_7", "x")][0] == 30.0 and g.Plate_1.CoarsenessFactor == 0.25
    with pytest.raises(SolverError, match="between 1° and 89°"):
        sv._p2_geometry(g, {**st, "slopes": [("Point_7|Point_6", 95.0)], "coarse": {}}, False, {})


def test_staged_fill_excavation_and_calculation_types():
    g = G()  # InitialPhase, Phase_1 (Wall), Phase_2 (Dig)
    g.Phases.append(O(V("Phase_3"), Identification=V("Dig 2")))
    soil = O("Soil_3_1", Active=Staged())
    g.Soils = [soil]
    calls = []
    g.activate = lambda o, ph: calls.append(("on", o.Name, str(ph.Name)))
    g.deactivate = lambda o, ph: calls.append(("off", o.Name, str(ph.Name)))
    sv = solver(g)
    st = {"slopes": [], "clusters": [("Soil_3_1", "excavatedin", 2)], "coarse": {}, "displ": [], "init": 1,
          "calc": [("Phase_2", 1)], "thermal": [], "lfactor": {}, "rain": {}}
    done = sv._p2_stages(g, st)
    assert ("on", "Soil_3_1", "Phase_1") in calls and ("off", "Soil_3_1", "Phase_2") in calls and ("off", "Soil_3_1", "Phase_3") in calls
    assert g.Phases[0].DeformCalcType == "Gravity loading" and g.Phases[2].DeformCalcType == "Consolidation"
    assert any("excavated in Phase_2" in x for x in done)
    calls.clear()
    sv._p2_stages(g, {**st, "clusters": [("Soil_3_1", "placedin", 3)], "init": None, "calc": []})
    assert ("off", "Soil_3_1", "InitialPhase") in calls and ("off", "Soil_3_1", "Phase_2") in calls and ("on", "Soil_3_1", "Phase_3") in calls
    with pytest.raises(SolverError, match="stage 9 does not exist"):
        sv._p2_stages(g, {**st, "clusters": [("Soil_3_1", "placedin", 9)], "init": None, "calc": []})


def test_load_factor_and_rainfall_per_stage():
    g = G()
    ph = g.Phases[2]
    load = O("LineLoad_1_1", Active=Staged({ph: True}), qy_start=Staged({ph: -10.0}), qy_end=Staged({ph: -10.0}))
    g.LineLoads = [load]
    g.Precipitation = O("Precipitation", Active=Staged(), q=Staged())
    g.activate = lambda o, p: o.Active.__setitem__(p, True)
    g.deactivate = lambda o, p: o.Active.__setitem__(p, False)
    sv = solver(g)
    st = {"slopes": [], "clusters": [], "coarse": {}, "displ": [], "init": None, "calc": [], "thermal": [],
          "lfactor": {"Phase_2": 1.5}, "rain": {"Phase_2": 0.02}}
    done = sv._p2_stages(g, st)
    assert load.qy_start[ph] == pytest.approx(-15.0) and load.qy_end[ph] == pytest.approx(-15.0)
    assert g.Precipitation.Active[ph] is True and g.Precipitation.q[ph] == pytest.approx(0.02)
    assert any("× 1.5" in x for x in done) and any("rainfall 0.02" in x for x in done)


class Mat(O):
    pass


def _mat(**props):
    m = Mat("Clay")
    for k, v in props.items():
        setattr(m, k, V(str(v)) if isinstance(v, str) else v)
    return m


@pytest.mark.parametrize("code", [5, 8])
def test_soft_soil_and_modified_cam_clay_parameters(code):
    g = G()
    g.Clay = _mat(pRef=100.0, EOedRef=5000.0, EURRef=15000.0, nuUR=0.2, eInit=0.8, phi=25.0)
    out = solver(g)._soil_model(g, "Clay", code)
    lam = 100 / 5000
    kap = min(3 * 0.6 * 100 / 15000, 0.2 * lam)
    if code == 5:
        assert g.Clay.lambdaModified == pytest.approx(lam) and g.Clay.kappaModified == pytest.approx(kap)
    else:
        sp = math.sin(math.radians(25))
        assert g.Clay.props["lambda"] == pytest.approx(lam * 1.8) and g.Clay.props["kappa"] == pytest.approx(kap * 1.8)
        assert g.Clay.props["M"] == pytest.approx(6 * sp / (3 - sp))
    assert f"SoilModel={code}" in out


def test_hoek_brown_rock_switched_to_mohr_coulomb():
    g = G()
    g.Clay = _mat(SigmaCi=50000.0, GSI=50.0, mi=10.0, D=0.0, ERef=2.0e6)
    out = solver(g)._soil_model(g, "Clay", 2)
    c, phi, _ = hoek_brown_mc(50000, 50, 10, 0)
    assert g.Clay.props["cRef"] == pytest.approx(c) and g.Clay.props["phi"] == pytest.approx(phi)
    assert "Hoek-Brown σci 50000" in out


def test_earthquake_record_in_a_table_multiplier():
    class Table(list):
        def add(self):
            r = O(f"row{len(self)}")
            self.append(r)
            return r
    g = G()
    mult = O("DisplMultiplier_1", Table=Table())
    rec = features.record("t a\n0 0\n0.01 0.12\n0.02 -0.3")
    solver(g)._fill_table(g, mult, rec)
    assert [(r.Time, r.Multiplier) for r in mult.Table] == [(0.0, 0.0), (0.01, 0.12), (0.02, -0.3)]
    s = {"dynamic": {"on": True, "after": "Phase_2", "signal": "record", "record": rec, "units": "g", "name": "Test"}}
    m = plaxis_model.demo("2d")
    cands = {c["key"]: c for c in features.candidates(m, s)}
    assert cands["dyn:scale"]["plaxis_ref"] == "Dynamic.scale" and "dyn:frequency" not in cands
    assert features.clean(s)["dynamic"]["duration"] == pytest.approx(0.02)
    assert "earthquake record Test (3 points, peak 0.3 g" in features.describe(m, s)[0][1]


def test_profiles_along_the_surface_and_the_wall():
    class R:
        X, Y, Z = "x", "y", "z"

    class Out:
        data = {"x": [0, 10, 20, 20, 20], "y": [0, 0, 0, -5, -10], "uy": [-0.01, -0.03, -0.02, 0, 0],
                "ux": [0, 0, 0.004, 0.012, 0.006]}

        def getresults(self, ph, rt, where):
            return self.data[rt]
    s = PlaxisSolver._profile(Out(), None, R, "uy", "profile:surface", False)
    assert s == [(0.0, -0.01), (10.0, -0.03), (20.0, -0.02)]
    w = PlaxisSolver._profile(Out(), None, R, "ux", "profile:wall", False)
    assert w[0] == (0.0, 0.004) and w[-1] == (-10.0, 0.006) and max(abs(b) for _, b in w) == 0.012


def test_profile_figure():
    spec = {"kind": "profile", "title": "Wall", "target": {"label": "Wall deflection profile ux", "unit": "m"}, "along": "wall",
            "series": [{"label": "Run 0", "points": [[0, 0.004], [-5, 0.012]], "existing": True},
                       {"label": "Run 1", "points": [[0, 0.003], [-5, 0.010]], "best": True}]}
    assert exports.figure(spec)[:4] == b"\x89PNG"


# --------------------------------------------------------------------------------------------------------------
# end to end: every module with the P2 inputs and results (simulated PLAXIS)
P2_RESULTS = ["settle_profile", "wall_profile", "temp", "utot"]


@pytest.mark.parametrize("mode,extra", [
    ("sensitivity", {"controls": [{"output": "wall_profile"}, {"output": "settle_profile"}], "runs": 4}),
    ("optimize", {"targets": [{"output": "wall_profile", "operator": "<=", "value": 0.1}], "runs": 3}),
    ("combinations", {"outputs": P2_RESULTS, "runs": 3}),
    ("calibration", {"measured": [{"output": "wall_profile", "value": 0.02}], "runs": 3}),
    ("design_change", {"outputs": P2_RESULTS, "runs": 2}),
    ("reporting", {"outputs": P2_RESULTS, "runs": 1}),
])
def test_every_module_runs_the_p2_features(client, mode, extra):
    if mode == "design_change":
        params = [{"item": "len:NodeToNodeAnchor_1", "new": 15}, {"item": "calc:Phase_2", "new": "Consolidation"},
                  {"item": "lfactor:@Phase_2", "new": 1.2}]
    elif mode == "reporting":
        params = []
    else:
        params = [{"item": "len:NodeToNodeAnchor_1", "min": 12, "max": 16}, {"item": "lfactor:@Phase_2", "min": 1.0, "max": 1.5}]
    pid, prev = _setup(client, mode, params=params, **extra)
    assert prev["errors"] == [], prev["errors"]
    tid, out = _run(client, pid)
    assert any(re.sub(r"^Figure \d+\. ", "", f["title"]).startswith(("Wall deflection profile", "Surface settlement profile"))
               for f in out["figures"]), \
        [f["title"] for f in out["figures"]]
    ok = [r for r in out["rows"] if r["status"] == "succeeded"]
    assert ok and all(((r.get("outputs") or {}).get("_profiles") or {}) for r in ok)
    d = client.get(f"/api/tasks/{tid}/report.docx")
    assert d.status_code == 200
    with zipfile.ZipFile(io.BytesIO(d.content)) as z:
        txt = z.read("word/document.xml").decode("utf-8")
    assert "profile" in txt.lower()
    assert client.get(f"/api/tasks/{tid}/results.xlsx?swap=0").status_code == 200
