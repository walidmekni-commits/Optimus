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
        assert c["plaxis_ref"] == f"{itf['name']}|{base}.ifaceRinter"
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
