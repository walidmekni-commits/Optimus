"""V26.1.9: the P1 features of the QA/QC "Report for Missing Features" (excavation and wall design like UA-22):
reading interfaces / wells / drains / tunnels, new structures, anchor prestress per row and stage, interface objects,
structure results, water level per stage, flow calculation, wells and drains, drainage type, a safety phase after
chosen stages, design approaches, results at chosen points and dynamic loads. The PLAXIS commands are checked with a
fake PLAXIS; the studies run end to end with the simulated solver."""
import math

import pytest

from app import features, plaxis_model, study
from app.solvers import PlaxisSolver, SolverError
from tests.test_studies import _check_files, _run, _setup
from tests.test_workflow import client, new_project  # noqa: F401  (fixture)


# --------------------------------------------------------------------------------------------------------------
# what the study page offers
@pytest.mark.parametrize("v", ["2d", "3d"])
def test_p1_inputs_are_offered(v):
    m = plaxis_model.demo(v)
    keys = {c["key"]: c for c in plaxis_model.candidates(m)}
    assert keys["drainage:Clay"]["current"] == "Undrained (A): 1" and keys["drainage:Clay"]["kind"] == "values"
    pre = keys["prestress:Anchor@Phase_3"]  # the anchor row, from its first active stage
    assert pre["plaxis_ref"] == "NodeToNodeAnchor_1.PrestressForce@Phase_3" and pre["current"] == 300.0
    assert "prestress:Anchor@Phase_2" not in keys  # the anchor is not active yet
    itf = keys["iface:PositiveInterface_1.Rinter"]
    assert itf["plaxis_ref"] == "PositiveInterface_1|Clay.ifaceRinter" and "Plate_1" in itf["label"]  # adjacent soil, host
    assert keys["iface:NegativeInterface_1.DeltaInter"]["current"] == 0.1
    assert keys["wlevel:@Phase_4"]["plaxis_ref"] == "WaterLevel.level@Phase_4" and keys["wlevel:@Phase_4"]["current"] == -4.0
    assert "wlevel:@Phase_5" not in keys  # not for the safety phase
    assert keys["pore:Phase_2"]["values"][1].startswith("Steady-state groundwater flow")
    assert keys["drain:Drain_1@Phase_4"]["plaxis_ref"] == "Drain_1.h@Phase_4"
    assert keys["design:approach"]["values"][2] == "EC7 DA1 Combination 2: 2"
    cat = study.catalog(m)
    dom = {p["key"]: p["domain"] for p in cat["params"]}
    assert dom["wlevel:@Phase_4"] == dom["pore:Phase_2"] == dom["drain:Drain_1@Phase_4"] == "water"
    assert dom["prestress:Anchor@Phase_3"] == "features" and dom["iface:PositiveInterface_1.Rinter"] == "features"  # QA D14
    outs = {o["key"]: o for o in cat["outputs"]}
    assert {"geogrid_n", "skin", "iface_sn", "iface_tau", "q", "head", "psteady", "pactive", "trough"} <= set(outs)
    assert ("beam_n" in outs) == (v == "3d")  # beams (capping beam) exist in PLAXIS 3D only
    assert outs["q"]["domain"] == "water" and outs["skin"]["domain"] == "forces"


def test_cluster_water_conditions_in_2d():
    m = plaxis_model.demo("2d")
    keys = {c["key"]: c for c in plaxis_model.candidates(m)}
    c = keys["wcond:Soil_2_1@Phase_4"]
    assert c["plaxis_ref"] == "Soil_2_1.WaterCondition@Phase_4" and c["values"] == plaxis_model.CLUSTER_CONDITIONS


def _features(v):
    return {"safety": {"after": ["Phase_2", "Phase_4"]}, "probes": [{"name": "Corner", "x": 30, "y": 0, "z": 0}],
            "added": [{"id": "N1", "kind": "anchor", "a": {"x": 20, "y": -5, "z": -5}, "b": {"x": 32, "y": -11, "z": -11},
                       "material": "Anchor", "phase": "Phase_3", "prestress": 200}],
            "dynamic": {"on": True, "after": "Phase_4", "source": "base", "amplitude": 0.02, "frequency": 3, "duration": 4},
            "tunnel": {"d": 8}}


@pytest.mark.parametrize("v", ["2d", "3d"])
def test_feature_inputs_and_results(v):
    m = plaxis_model.demo(v)
    s = _features(v)
    cands = {c["key"]: c for c in features.candidates(m, s)}
    assert cands["add:N1"]["values"] == ["not added: 0", "added: 1"] and cands["add:N1"]["plaxis_ref"] == "New_N1.add"
    assert cands["addshift:N1"]["plaxis_ref"] == ("New_N1.shiftz" if v == "3d" else "New_N1.shifty")
    assert cands["dyn:frequency"]["current"] == 3.0 and cands["dyn:amplitude"]["unit"] == "m"
    outs = {o["key"]: o for o in features.outputs(m, s)}
    at = "30;0;0" if v == "3d" else "30;0"
    assert outs["probe0:utot"]["result"] == f"Soil.Utot|at {at}"
    assert outs["vl"]["result"].endswith("|trough:8") and outs["vl"]["unit"] == "%"
    assert outs["dyn_ax"]["phase_hint"] == "dynamic"
    opt = features.solver_options(m, s)
    assert opt["safety_after"] == ["Phase_2", "Phase_4"] and opt["dynamic"]["after"] == "Phase_4"
    sp = opt["additions"]["New_N1"]
    assert sp["kind"] == "anchor" and sp["phase"] == "Phase_3" and sp["prestress"] == 200
    assert sp["a"] == ([20.0, -5.0, -5.0] if v == "3d" else [20.0, -5.0, -5.0])


def test_safety_phase_after_chosen_stages_gives_fos_per_stage():
    m = plaxis_model.demo("2d")
    m["phases"] = m["phases"][:-1]  # no safety phase in the model
    s = {"mode": "optimize", "params": [{"item": "mat:Clay.phi", "min": 22, "max": 30}],
         "targets": [{"output": "fos", "operator": ">=", "value": 1.3}], "runs": 3}
    b = study.build(m, s)
    assert any("needs a safety phase" in e for e in b["errors"])  # V26.1.8 rule kept without the feature
    b = study.build(m, {**s, "safety": {"after": ["Phase_2", "Phase_4"]}})
    assert b["errors"] == []
    fos = [t for t in b["targets"] if t["key"] == "fos"]
    assert [t["phase"] for t in fos] == ["@safety:Phase_2", "@safety:Phase_4"]
    assert all(t["operator"] == ">=" and t["value"] == 1.3 for t in fos) and "after Excavation to -10 m" in fos[1]["label"]
    assert [t["name"] for t in b["targets"]] == [f"T{i}" for i in range(1, len(b["targets"]) + 1)]


def test_values_variable_spreads_over_more_runs():
    """A two-value variable (not added / added) in four runs: every run gets a value (was an IndexError)."""
    m = plaxis_model.demo("2d")
    s = {"mode": "optimize", "params": [{"item": "add:N1"}, {"item": "mat:Clay.phi", "min": 22, "max": 30}],
         "targets": [{"output": "utot", "operator": "<=", "value": 0.05}], "runs": 5, **_features("2d")}
    b = study.build(m, s)
    assert b["errors"] == [] and len(b["runs"]) == 5
    added = [r["inputs"]["p1"][1] for r in b["runs"][1:]]
    assert added[0] == 0.0 and added[-1] == 1.0 and len(added) == 4


# --------------------------------------------------------------------------------------------------------------
# end to end with the simulated solver
def test_study_with_every_p1_feature_runs(client):
    s = {"params": [{"item": "add:N1", "new": "added"}, {"item": "prestress:Anchor@Phase_3", "new": 250},
                    {"item": "design:approach", "new": "EC7 DA1 Combination 2"}, {"item": "drainage:Clay", "new": "Drained"},
                    {"item": "iface:PositiveInterface_1.Rinter", "new": 0.5}, {"item": "wlevel:@Phase_4", "new": -10.5},
                    {"item": "pore:Phase_4", "new": "Steady-state groundwater flow"}, {"item": "dyn:frequency", "new": 4}],
         "outputs": ["fos", "probe0:uv", "trough", "vl", "skin", "q", "dyn_ax"], **_features("2d")}
    pid, prev = _setup(client, "design_change", **s)
    assert prev["errors"] == [] and prev["total"] == 2, prev
    cat = client.get(f"/api/projects/{pid}/study").json()["catalog"]
    assert any(p["key"] == "add:N1" for p in cat["params"]) and any(o["key"] == "probe0:uv" for o in cat["outputs"])
    assert cat["kinds"]["anchor"] and {p["name"] for p in cat["all_phases"]} >= {"Phase_2", "Phase_4"}
    tid, out = _run(client, pid)
    assert out["status"] == "succeeded"
    _check_files(client, tid)


def test_put_study_returns_the_catalog_with_the_features(client):
    pid, prev = _setup(client, "sensitivity", **_features("2d"))
    assert any(o["key"] == "dyn_vx" for o in prev["catalog"]["outputs"])
    assert any(p["key"] == "dyn:duration" for p in prev["catalog"]["params"])


# --------------------------------------------------------------------------------------------------------------
# PLAXIS commands, with a fake PLAXIS
class Staged(dict):
    pass


class SV(Staged):
    """A staged property that also has its own value (as read in Structures mode)."""

    def __init__(self, d, value):
        super().__init__(d)
        self.value = value


class V(str):
    """A PLAXIS text value: str(...) and .value."""

    @property
    def value(self):
        return str(self)


class O:
    def __init__(self, name, **kw):
        self.Name = name
        self.props = {}
        for k, v in kw.items():
            setattr(self, k, v)

    def setproperties(self, *args):
        for k, v in zip(args[::2], args[1::2]):
            self.props[k] = v
            setattr(self, k, v)


class G:
    def __init__(self):
        self.mode = "stages"
        self.Phases = [O(V("InitialPhase"), Identification=V("Initial phase")), O(V("Phase_1"), Identification=V("Wall")),
                       O(V("Phase_2"), Identification=V("Dig"))]
        for i, p in enumerate(self.Phases):
            p.PreviousPhase = self.Phases[i - 1] if i else None
        self.calls = []
        self.Materials = []
        self.LineLoads = self.SurfaceLoads = self.PointLoads = []

    def __getattr__(self, n):
        if n.startswith("goto"):
            return lambda: setattr(self, "mode", n[4:])
        raise AttributeError(n)


def solver(g):
    s = PlaxisSolver.__new__(PlaxisSolver)
    s.LONGTERM_ID = PlaxisSolver.LONGTERM_ID

    def find(g_i, name):
        try:
            return getattr(g_i, name)
        except AttributeError:
            for m in g_i.Materials:
                if name in (m.Name, getattr(m, "Identification", "")):
                    return m
        raise SolverError(f"Object '{name}' not found in the PLAXIS model")
    s._find = find
    return s


def test_prestress_switches_adjust_prestress_on_in_the_stage():
    g = G()
    p2 = g.Phases[2]
    g.NodeToNodeAnchors = [O("NodeToNodeAnchor_1_1", AdjustPrestress=Staged(), PrestressForce=Staged()),
                           O("NodeToNodeAnchor_2_1", AdjustPrestress=Staged(), PrestressForce=Staged())]
    txt = solver(g)._set_staged(g, "NodeToNodeAnchor_1", "PrestressForce", "Phase_2", 250.0)
    a1, a2 = g.NodeToNodeAnchors
    assert a1.AdjustPrestress == {p2: True} and a1.PrestressForce == {p2: 250.0} and a2.PrestressForce == {}
    assert "Phase_2 = 250" in txt
    with pytest.raises(SolverError, match="Could not set Nothing.PrestressForce"):
        solver(g)._set_staged(g, "Nothing", "PrestressForce", "Phase_2", 1.0)


def test_drainage_type_and_flow_calculation_names():
    g = G()

    class Mat(O):
        def setproperties(self, prop, v):
            if prop == "DrainageType" and v != "Undrained (A)":
                raise RuntimeError("Invalid value")
            super().setproperties(prop, v)
    g.Clay = Mat("Clay")
    assert solver(g)._drainage(g, "Clay", 1) == "Clay: drainage type Undrained (A)" and g.Clay.DrainageType == "Undrained (A)"
    assert "Steady state groundwater flow" in solver(g)._pore_type(g, "Phase_2", 1)
    assert g.Phases[2].PorePresCalcType == "Steady state groundwater flow"


def test_design_approach_factors_strength_and_variable_loads():
    g = G()
    p1, p2 = g.Phases[1:]

    class Soil(O):
        _plx_type = "SoilMat"
    g.Materials = [Soil("Clay", phi=30.0, cref=10.0, psi=5.0, SuRef=60.0), O("Wall", _plx_type="PlateMat", EI=1.0)]
    g.LineLoads = [O("LineLoad_1_1", qy_start=Staged({p1: -10.0, p2: -10.0}))]
    done, eff = solver(g)._design_approach(g, 2)  # DA1-C2: M2 and γQ = 1.3
    clay = g.Materials[0]
    assert clay.phi == pytest.approx(math.degrees(math.atan(math.tan(math.radians(30)) / 1.25)))
    assert clay.cref == pytest.approx(8.0) and clay.SuRef == pytest.approx(60 / 1.4)
    assert clay.psi == pytest.approx(max(0.0, 5.0 - (30 - clay.phi)))
    assert g.LineLoads[0].qy_start == {p1: pytest.approx(-13.0), p2: pytest.approx(-13.0)} and eff == 1.0
    g2 = G()
    g2.Materials = [Soil("Sand", phi=34.0)]
    done, eff = solver(g2)._design_approach(g2, 3)  # DA2*: effects × 1.35, strength unfactored
    assert eff == 1.35 and g2.Materials[0].phi == 34.0
    assert PlaxisSolver.is_force("Plate.M2D") and not PlaxisSolver.is_force("Soil.Utot")


def test_safety_phase_added_and_found_by_its_stage():
    g = G()
    made = []

    def phase(base):
        p = O(V(f"Phase_{len(g.Phases)}"), Identification=V(""), PreviousPhase=base, Deform=O("Deform"))
        g.Phases.append(p)
        made.append(p)
        return p
    g.phase = phase
    sv = solver(g)
    assert sv._add_safety(g, ["Phase_1"]) == ["Safety phase Phase_3 after Phase_1"]
    p = made[0]
    assert p.DeformCalcType == "Safety" and p.Identification == "OPTIMUS safety after Phase_1"
    p.Identification = V(p.Identification)
    assert sv._phase(g, "@safety:Phase_1") is p
    assert sv._phase(g, "").Name.value == "Phase_2"  # the last stage of the works, not the added safety phase
    with pytest.raises(SolverError, match="no phase 'OPTIMUS dynamic'"):
        sv._phase(g, "@dynamic")


def test_water_level_per_stage_is_drawn_and_set():
    g = G()
    g.Points = [O("P1", x=0.0, y=-30.0), O("P2", x=60.0, y=0.0)]
    drawn, setg = [], []

    def waterlevel(*pts):
        drawn.append(pts)
        return [O("Point_9"), O(f"UserWaterLevel_{len(drawn)}")]
    g.waterlevel = waterlevel
    g.setglobalwaterlevel = lambda wl, ph: setg.append((wl.Name, ph.Name))
    done = solver(g)._water_levels(g, {"Phase_1": -6.0, "Phase_2": -10.5}, False)
    assert drawn == [((0.0, -6.0), (60.0, -6.0)), ((0.0, -10.5), (60.0, -10.5))]
    assert setg == [("UserWaterLevel_1", "Phase_1"), ("UserWaterLevel_2", "Phase_2")] and len(done) == 2


def test_cluster_condition_dry_in_a_stage():
    g = G()
    wc = O("WaterConditions_2_1", Conditions=Staged())
    g.Soils = [O("Soil_2_1", WaterConditions=wc)]
    solver(g)._cluster_conditions(g, [("Soil_2_1", "Phase_2", 1)])
    assert wc.Conditions == {g.Phases[2]: "Dry"}


def test_new_anchor_drawn_with_material_activated_and_prestressed():
    g = G()
    g.Materials = [O("Anchor", Identification="Anchor rod")]
    drawn = []

    def n2nanchor(a, b):
        drawn.append((a, b))
        return [O("Point_20"), O("Point_21"), O("Line_9"), O("NodeToNodeAnchor_7")]
    g.n2nanchor = n2nanchor
    sv = solver(g)
    spec = {"name": "New_N1", "kind": "anchor", "a": [20.0, -5.0, 0.0], "b": [32.0, -11.0, 0.0], "material": "Anchor",
            "phase": "Phase_1", "prestress": 200.0, "shift": -1.0}
    made = sv._add_structures(g, [spec], False)
    assert drawn == [((20.0, -6.0), (32.0, -12.0))] and made[0][1] == "NodeToNodeAnchor_7"
    piece = O("NodeToNodeAnchor_7_1", Active=Staged(), AdjustPrestress=Staged(), PrestressForce=Staged())
    g.NodeToNodeAnchors = [piece]
    act = []
    g.activate = lambda o, ph: act.append((o.Name, ph.Name))
    done = sv._activate_added(g, made)
    assert act == [("NodeToNodeAnchor_7_1", "Phase_1"), ("NodeToNodeAnchor_7_1", "Phase_2")]
    assert piece.PrestressForce == {g.Phases[1]: 200.0} and "prestress 200" in done[0]
    with pytest.raises(SolverError, match="PLAXIS 3D only"):
        sv._add_structures(g, [{**spec, "kind": "beam"}], False)


def test_interface_gets_its_own_material():
    g = G()

    class Soil(O):
        _plx_type = "SoilMat"
    clay = Soil("Clay", SoilModel=3, Rinter=0.67, phi=25.0, DrainageType="Drained")
    g.Materials = [clay]
    g.Clay = clay
    g.PositiveInterface_1 = O("PositiveInterface_1", MaterialMode="From adjacent soil")
    g.PositiveInterfaces = [O("PositiveInterface_1_1", Material=Staged(), MaterialMode=Staged())]
    new = []

    def soilmat():
        m = Soil(f"SoilMat_{len(new) + 9}")
        new.append(m)
        return m
    g.soilmat = soilmat
    done = solver(g)._interfaces(g, {"PositiveInterface_1|Clay": {"Rinter": 0.5}})
    m = new[0]
    assert m.Rinter == 0.5 and m.phi == 25.0 and m.SoilModel == 3 and m.props.get("InterfaceStrengthDetermination") == "Manual"
    assert g.PositiveInterface_1.MaterialMode == "Custom" and g.PositiveInterface_1.Material is m
    assert clay.Rinter == 0.67  # the soil itself is not changed (other interfaces keep it)
    assert all(v is m for v in g.PositiveInterfaces[0].Material.values()) and "Rinter = 0.5" in done[0]


class Out:
    """A fake PLAXIS Output: four surface nodes and a deeper one."""

    def __init__(self):
        self.X, self.Y, self.Uy, self.Utot = "X", "Y", "Uy", "Utot"
        self.data = {"X": [0.0, 10.0, 20.0, 30.0, 10.0], "Y": [0.0, 0.0, 0.0, 0.0, -5.0],
                     "Uy": [0.0, -0.02, -0.02, 0.0, -0.05]}

    def getresults(self, phase, rtype, where):
        return self.data[rtype]

    def getsingleresult(self, phase, rtype, pt):
        return -0.012 if pt == (10.0, 0.0) else "not found"


def test_settlement_trough_and_volume_loss():
    g_o = Out()
    vs = PlaxisSolver._trough(g_o, None, g_o, "Uy", "trough", False)
    assert vs == pytest.approx(0.1 + 0.2 + 0.1)  # trapezoids 0-10, 10-20, 20-30
    vl = PlaxisSolver._trough(g_o, None, g_o, "Uy", "trough:2", False)
    assert vl == pytest.approx(100 * 0.4 / math.pi)


def test_value_at_a_point():
    g_o = Out()
    assert PlaxisSolver._at_point(g_o, None, "Uy", "10;0", False) == pytest.approx(0.012)
    with pytest.raises(SolverError, match="outside the mesh"):
        PlaxisSolver._at_point(g_o, None, "Uy", "99;0", False)
    with pytest.raises(SolverError, match="not understood"):
        PlaxisSolver._at_point(g_o, None, "Uy", "10", False)


def test_extract_reads_interfaces_wells_drains_tunnels_and_active_stages():
    """plaxis_model.extract with a fake PLAXIS Input that has the V26.1.9 objects."""
    class P:
        def __init__(self, n, x, y):
            self.Name, self.x, self.y = n, x, y

    class Fake(G):
        pass
    g = Fake()
    p1, p2 = g.Phases[1:]
    a, b = P("Point_1", 20.0, 0.0), P("Point_2", 20.0, -18.0)
    g.Points = [a, b]
    g.Lines = []
    line = O("Line_1", First=a, Second=b)
    g.Plates = [O("Plate_1", Parent=line, Material="")]
    g.NodeToNodeAnchors = [O("NodeToNodeAnchor_1", Parent=O("Line_2", First=P("Point_3", 20, -2), Second=P("Point_4", 32, -8)),
                             Material="", Active=Staged({p2: True}), AdjustPrestress=Staged({p2: True}),
                             PrestressForce=Staged({p2: 150.0}))]
    g.PositiveInterfaces = [O("PositiveInterface_1", Parent=line, MaterialMode="From adjacent soil", Material="")]
    g.Drains = [O("Drain_1", Parent=O("Line_3", First=P("Point_5", 0, -10), Second=P("Point_6", 20, -10)), h=SV({p2: -10.5}, -10.5),
                  Active=Staged({p2: True}))]
    g.Wells = []
    g.Tunnels = [O("Tunnel_1")]
    g.Soils = [O("Soil_1_1"), O("Soil_2_1")]
    m = plaxis_model.extract(object(), g)
    assert m["interfaces"][0]["name"] == "PositiveInterface_1" and m["interfaces"][0]["mode"] == "From adjacent soil"
    assert m["drains"][0]["prop"] == "h" and m["drains"][0]["value"] == -10.5
    assert m["tunnels"] == [{"name": "Tunnel_1"}] and m["clusters"] == ["Soil_1_1", "Soil_2_1"]
    anchor = next(s for s in m["structures"] if s["kind"] == "anchor")
    assert anchor["active"] == ["Phase_2"]
    staged = {(s["object"], s["prop"], s["phase"]): s["value"] for s in m["staged"]}
    assert staged[("Drain_1", "h", "Phase_2")] == -10.5 and staged[("NodeToNodeAnchor_1", "PrestressForce", "Phase_2")] == 150.0
    keys = {c["key"] for c in plaxis_model.candidates(m)}
    assert {"prestress:NodeToNodeAnchor_1@Phase_2", "iface:PositiveInterface_1.Rinter", "staged:Drain_1.h@Phase_2"} <= keys
    assert "prestress:NodeToNodeAnchor_1@Phase_1" not in keys
