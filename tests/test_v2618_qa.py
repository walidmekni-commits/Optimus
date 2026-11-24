"""OPTIMUS V26.1.8 (QA/QC review of the five study modules): reading PLAXIS 3D results, Run 0 from the full run, the
factor of safety without a safety phase, no retry of a deterministic PLAXIS error, and the new analysis of
Optimization (interpolated limit, refinement), Design modification (verdict), Design scenarios (all combinations,
governing scenario), Calibration (calibrated value) and Advanced reporting (every stage, missing results)."""
import copy

from app import plaxis_model as pm, solvers, study
from tests.test_workflow import client, new_project  # noqa: F401  (fixture)

PHI = {"item": "mat:Clay.phi", "min": 22, "max": 34, "custom": [], "new": ""}


def _plan(mode, targets, variables=None):
    v = variables or [{"name": "p1", "item": "x", "label": "Load", "unit": "kN/m²", "kind": "range", "min": 10.0,
                       "max": 30.0, "current": 20.0}]
    return {"variables": v, "targets": targets, "study": {"mode": mode}, "first": 0}


def _run(no, x, outs, inputs=True):
    return {"no": no, "status": "succeeded", "inputs": {"p1": x} if inputs else {}, "outputs": outs}


def test_3d_result_names_have_alternatives():
    class G:
        N11 = "n11"
        N = "anchor"
    assert solvers._rtype(G, "N1") == "n11" and solvers._rtype(G, "AnchorForce3D") == "anchor"
    outs = {o["key"]: o["result"] for o in pm.outputs("3d")}
    assert outs["plate_n"] == "Plate.N11" and outs["anchor"] == "NodeToNodeAnchor.N"


def test_a_run_just_over_the_limit_does_not_pass():
    t = {"name": "T1", "label": "ux", "unit": "m", "operator": "<=", "value": 0.0072, "key": "ux", "result": "Ux"}
    runs = [_run(0, None, {"T1": 0.007201}, False), _run(1, 10.0, {"T1": 0.0053})]
    o = study.results({"plan": _plan("optimize", [t]), "objective": "optimize"}, runs)["optimization"]
    assert o["passing"] == 1 and o["best_run"] == 1


def test_deterministic_errors_are_not_retried():
    assert solvers.deterministic('PlxScriptingError: Invalid material "L-2 Med. Dense sand"')
    assert not solvers.deterministic("ConnectionError: PLAXIS did not answer")


def test_fos_needs_a_safety_phase():
    m = copy.deepcopy(pm.demo("2d"))
    m["phases"] = [ph for ph in m["phases"] if "safety" not in f"{ph.get('type', '')} {ph.get('id', '')}".lower()]
    b = study.build(m, {"mode": "reporting", "outputs": ["fos", "utot"]})
    assert not b["errors"] and [t["key"] for t in b["targets"]] == ["utot"] and b["warnings"]
    b = study.build(m, {"mode": "optimize", "params": [PHI], "runs": 3, "targets": [{"output": "fos", "operator": ">=", "value": 1.5}]})
    assert any("safety phase" in e for e in b["errors"])


def test_optimization_interpolates_the_limit_and_refines_there():
    t = {"name": "T1", "label": "Max wall bending moment", "unit": "kN·m/m", "operator": "<=", "value": 205.0, "key": "plate_m", "result": "Plate.M11"}
    plan = _plan("optimize", [t])
    plan["variables"][0]["current"] = 15.0
    runs = [_run(0, None, {"T1": 199.0}, False), _run(1, 10.0, {"T1": 195.0}), _run(2, 20.0, {"T1": 203.0}), _run(3, 30.0, {"T1": 211.0})]
    out = study.results({"plan": plan, "objective": "optimize"}, runs)
    o = out["optimization"]
    assert o["achieved"] and o["best_run"] == 2  # the least over-designed passing run
    g = o["governing"]
    assert g and abs(g["x"] - 22.5) < 1e-9 and g["between"] == [2, 3] and "Interpolated limit" in out["text"]
    new = study.more_runs(plan, runs, 2)
    xs = sorted(r["inputs"]["p1"] for r in new)
    assert 22.5 in xs and all(20.0 <= x <= 30.0 for x in xs)  # at the limit and inside its interval


def test_modification_verdict_and_criteria(client):  # noqa: F811
    pid = new_project(client, "design_change")
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    r = client.put(f"/api/projects/{pid}/study", json={"mode": "design_change", "params": [{**PHI, "new": 30}],
                   "outputs": ["utot", "plate_m"], "criteria": [{"output": "utot", "operator": "<=", "value": 10}]})
    assert r.status_code == 200 and not r.json()["errors"]
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    v = out["verdict"]
    assert v["criteria"] == 1 and v["improves"] + v["worsens"] >= 1 and all(c.get("effect") for c in out["comparison"])
    assert "acceptance criteria" in out["text"] or "does NOT meet" in out["text"]


def test_modification_without_run_0_is_not_compared_with_itself():
    t = {"name": "T1", "label": "u", "unit": "m", "operator": "report", "value": None, "key": "utot", "result": "Utot"}
    runs = [{"no": 0, "status": "failed", "inputs": {}, "outputs": None}, _run(1, 5.0, {"T1": 0.01})]
    out = study.results({"plan": _plan("design_change", [t]), "objective": "design_change"}, runs)
    assert "comparison" not in out and "not calculated" in out["text"]


def test_scenarios_all_combinations_and_governing(client):  # noqa: F811
    pid = new_project(client, "combinations")
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    two = [{**PHI, "how": "list", "list": "24, 30"}, {"item": "mat:Clay.cref", "how": "list", "list": "5, 10, 15"}]
    r = client.put(f"/api/projects/{pid}/study", json={"mode": "combinations", "params": two, "factorial": True,
                                                         "outputs": ["utot", "fos"], "runs": 2})
    assert r.status_code == 200 and not r.json()["errors"], r.text
    assert r.json()["total"] == 1 + 2 * 3  # Run 0 + every combination
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    g = {x["label"]: x for x in out["governing"]}
    assert len(g) == 2 and "Governing scenarios" in out["text"]


def test_calibration_gives_the_calibrated_value():
    t = {"name": "T1", "label": "ux", "unit": "m", "operator": "report", "value": None, "key": "ux", "result": "Ux", "measured": 0.0065}
    runs = [_run(1, 10.0, {"T1": 0.0050}), _run(2, 20.0, {"T1": 0.0068}), _run(3, 30.0, {"T1": 0.0086})]
    out = study.results({"plan": _plan("calibration", [t]), "objective": "calibration"}, runs)
    e = out["calibration"]["estimate"]
    assert e["interpolated"] and 15.0 < e["x"] < 25.0 and "Calibrated value" in out["text"]
    assert out["calibration"]["residuals"][0]["diff"] is not None


def test_time_in_every_module(client):  # noqa: F811
    pid = new_project(client, "design_change")
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    body ={"mode": "design_change", "params": [{**PHI, "new": 30}], "outputs": ["utot"],
            "time": {"on": True, "longterm": 365}}
    r = client.put(f"/api/projects/{pid}/study", json=body)
    assert r.status_code == 200 and not r.json()["errors"], r.text
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    ts = out["time"][0]
    assert ts["key"] == "utot" and len(ts["runs"]) == 2
    days = [d for d, _ in ts["runs"][0]["points"]]
    assert days == sorted(days) and days[-1] - days[-2] == 365  # the long-term phase, one year after the last stage
    assert any("over time" in f["title"] for f in out["figures"])
    assert len(out["comparison"]) == 1  # the stage values are not compared one by one


def test_calibration_against_a_field_time_series(client):  # noqa: F811
    pid = new_project(client, "calibration")
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    series = [{"output": "utot", "points": [{"day": 30, "value": 0.02}, {"day": 60, "value": 0.03}, {"day": 425, "value": 0.035}]}]
    r = client.put(f"/api/projects/{pid}/study", json={"mode": "calibration", "params": [PHI], "runs": 4,
                                                         "series": series, "time": {"on": False, "longterm": 365}})
    assert r.status_code == 200 and not r.json()["errors"], r.json()["errors"]
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["calibration"]["best_run"] is not None and len(out["calibration"]["residuals"]) == 3
    assert out["time"][0]["measured"] and any("field readings" in f["title"] for f in out["figures"])


def test_soil_model_stiffness_set_and_curated_soil_parameters():
    m = copy.deepcopy(pm.demo("3d"))
    m["materials"].append({"name": "L2Sand", "kind": "soil", "model": "4", "props": {
        "E50Ref": 38000.0, "EOedRef": 38000.0, "EURRef": 114000.0, "pRef": 100.0, "ClayFraction": 10.0, "cK": 1e15, "phi": 34.0}})
    cat = {c["key"]: c for c in study.catalog(m)["params"]}
    sm = cat["smodel:L2Sand"]
    assert sm["values"] == pm.SOIL_MODELS_P2 and sm["domain"] == "soil"  # V26.1.9 (P2): Soft Soil and MCC added
    assert abs(cat["sscmu:L2Sand"]["current"] - 100 / 38000 / 25) < 1e-6 and "stiff:L2Sand" in cat
    assert "mat:L2Sand.ClayFraction" not in cat and "mat:L2Sand.cK" not in cat and cat["mat:L2Sand.E50Ref"]["unit"] == "kN/m²"
    v = pm.variable_from({**sm, "plaxis_ref": sm["ref"], "section": "soil"}, "p1", "3d")
    assert v["kind"] == "values" and v["current"] == "HSsmall: 4"
    b = study.build(m, {"mode": "combinations", "outputs": ["utot"], "runs": 3, "distribution": "custom",
                        "params": [{"item": "smodel:L2Sand", "custom": ["HS", "SSC"]}]})
    assert not b["errors"] and [r["inputs"]["p1"] for r in b["runs"][1:]] == [("HS", 3.0), ("SSC", 6.0)]


def test_soft_soil_creep_studies_use_gravity_loading():
    mu = {"name": "p1", "plaxis_ref": "L6.SSCmu", "values": []}
    sm = {"name": "p1", "plaxis_ref": "L6.SoilModel", "values": pm.SOIL_MODELS}
    assert study.needs_gravity_initial({"variables": [mu]})
    assert not study.needs_gravity_initial({"variables": [sm]}, [{"inputs": {"p1": ["HS", 3.0]}}])
    assert study.needs_gravity_initial({"variables": [sm]}, [{"inputs": {}}, {"inputs": {"p1": ["SSC", 6.0]}}])
    assert not study.needs_gravity_initial({"variables": [{"name": "p1", "plaxis_ref": "Load.sigz"}]})


def test_reporting_lists_missing_results_and_every_stage(client):  # noqa: F811
    pid = new_project(client, "reporting")
    assert client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1").status_code == 200
    assert client.put(f"/api/projects/{pid}/study", json={"mode": "reporting", "outputs": ["utot", "anchor"]}).status_code == 200
    tid = client.post(f"/api/projects/{pid}/study/run").json()["id"]
    out = client.get(f"/api/tasks/{tid}/study").json()
    assert out["stages"]["phases"] and {r["label"] for r in out["stages"]["rows"]} >= {"Max total displacement |u|"}
    assert len(out["stats"]) == 2  # the stage values are in their own table, not in the statistics
    missing = study.missing_results([{"name": "T1", "label": "Max anchor force", "result": "NodeToNodeAnchor.N"}],
                                    [{"status": "succeeded", "no": 0, "outputs": {"T1": None, "_missing": ["Max anchor force: not in this model"]}}])
    assert missing[0]["reason"] == "not in this model"
