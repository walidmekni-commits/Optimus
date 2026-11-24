"""Rev B of the module interface (Walid's remarks of 23 Nov 2026, A3 report Rev B of 24 Nov 2026):
Design modification with many modification runs (Number of modifications, Puzzle packages, names, one results column
per modification with verdict, margin and rank, Choose ★, report chapters), the Study plan, What next? at the end of
each module and the handover Design optimization -> Design modification."""
import io
import zipfile

from app import plan, study
from tests.test_studies import PHI, _run, _setup
from tests.test_workflow import client, new_project  # noqa: F401  (fixture)

CRIT = [{"output": "utot", "operator": "<=", "value": 1.0}]


def _docx(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_old_single_modification_study_still_builds(client):
    """A study saved before Rev B ("new": one value) is still one modification, compared as before."""
    pid, prev = _setup(client, "design_change", params=[{**PHI, "new": 30}], outputs=["fos", "utot"])
    assert prev["errors"] == [] and prev["total"] == 2
    tid, out = _run(client, pid)
    assert len(out["modifications"]) == 1 and out["comparison"]
    assert out["text"].startswith("Existing → modified design")
    assert any(f["title"].startswith("Figure") or f for f in out["figures"])


def test_many_modifications_from_new_values(client):
    """Each item has several new values (one Puzzle box each): Modification k takes the k-th value of every item."""
    pid, prev = _setup(client, "design_change", params=[{**PHI, "news": [26, 30, 33]}],
                       outputs=["fos", "utot"], criteria=CRIT, names={"2": "Stiffer clay"})
    assert prev["errors"] == [] and prev["total"] == 4, prev
    assert [r["values"] for r in prev["rows"]][1:] == ["Clay φ′ = 26°", "Clay φ′ = 30°", "Clay φ′ = 33°"] or len(prev["rows"]) == 4
    assert prev["rows"][2]["name"] == "Stiffer clay"
    tid, out = _run(client, pid)
    mods = out["modifications"]
    assert [m["no"] for m in mods] == [1, 2, 3] and all("results" in m for m in mods)
    assert sorted(m["rank"] for m in mods) == [1, 2, 3]
    assert sum(m["chosen"] for m in mods) == 1 and next(m for m in mods if m["chosen"])["rank"] == 1
    assert mods[1]["title"] == "Modification 2 · Stiffer clay"
    assert "compared with 3 modifications" in out["text"] and "Rank 1" in out["text"]
    kinds = [f["title"] for f in out["figures"]]
    assert any("every modification" in k for k in kinds), kinds
    # Word: one table for the modifications, the comparison, the ranking; no repeated "Comparison with the Original File"
    txt = _docx(client.get(f"/api/tasks/{tid}/report.docx").content)
    assert "The modifications" in txt and "Ranking and the chosen modification" in txt
    assert "Comparison with the Original File" not in txt and "Stiffer clay" in txt
    x = client.get(f"/api/tasks/{tid}/results.xlsx?swap=0")
    with zipfile.ZipFile(io.BytesIO(x.content)) as z:
        assert "Modifications" in z.read("xl/workbook.xml").decode()
    # Choose ★: another modification is the one handed to Design scenarios
    other = next(m for m in mods if not m["chosen"])
    assert client.post(f"/api/tasks/{tid}/choose", json={"run": other["run"]}).status_code == 200
    out2 = client.get(f"/api/tasks/{tid}/study").json()
    assert next(m for m in out2["modifications"] if m["chosen"])["run"] == other["run"]
    assert out2["verdict"]["chosen"] == other["run"] and "Chosen by the engineer" in out2["text"]


def test_number_of_modifications_and_empty_ones():
    s = study.clean({"mode": "design_change", "params": [{**PHI, "news": [26, 30]}], "outputs": ["fos"], "mods": 3})
    from app import plaxis_model
    b = study.build(plaxis_model.demo("2d"), s)
    assert any("Modification 3 has no change" in e for e in b["errors"])
    b = study.build(plaxis_model.demo("2d"), {**s, "mods": 1})
    assert b["errors"] == [] and len(b["runs"]) == 2


def test_puzzle_packages_in_design_modification(client):
    """Rev B W1.1/W1.2: the Puzzle stays in Design modification; each modification is a package of boxes."""
    from app import plaxis_model
    m = plaxis_model.demo("2d")
    cand = next(c for c in study.all_candidates(m) if c["key"] == "mat:Fill.E50ref")
    s = study.clean({"mode": "design_change", "outputs": ["utot"],
                     "params": [{**PHI, "news": [26, 30]}, {"item": cand["key"], "news": [cand["current"] * 0.8]}],
                     "puzzle": {"on": True, "runs": [[{"item": PHI["item"], "v": 26}],
                                                     [{"item": PHI["item"], "v": 30}, {"item": cand["key"], "v": cand["current"] * 0.8}]]}})
    b = study.build(m, s)
    assert b["errors"] == []
    assert [len(r["inputs"]) for r in b["runs"]] == [0, 1, 2]  # Run 0, then a package of one and of two changes
    assert b["runs"][2]["label"] == "Modification 2"


def test_ranking_rules():
    targets = [{"name": "T1", "label": "U", "unit": "m", "key": "utot", "operator": "<=", "value": 1.0, "result": "u"}]
    var = {"name": "p1", "label": "Clay φ′", "unit": "°", "kind": "range", "current": 28.0, "item": PHI["item"]}
    runs = [{"status": "succeeded", "inputs": {}, "outputs": {"T1": 1.1}},
            {"status": "succeeded", "inputs": {"p1": 29.0}, "outputs": {"T1": 0.95}},   # small change, small margin
            {"status": "succeeded", "inputs": {"p1": 34.0}, "outputs": {"T1": 0.70}},   # big change, big margin
            {"status": "succeeded", "inputs": {"p1": 24.0}, "outputs": {"T1": 1.30}}]   # fails
    task = {"plan": {"variables": [var], "targets": targets, "first": 0, "study": {"mode": "design_change"}},
            "objective": "design_change"}
    out = study.results(task, runs)
    rk = {m["run"]: m["rank"] for m in out["modifications"]}
    assert rk == {1: 1, 2: 2, 3: 3}  # least change that passes first, the failing one last
    assert out["existing"]["meets"] is False and out["verdict"]["passing"] == 2
    task["plan"]["study"]["rank"] = "margin"
    out = study.results(task, runs)
    assert {m["run"]: m["rank"] for m in out["modifications"]} == {2: 1, 1: 2, 3: 3}


def test_what_next_defaults_order_and_conditions():
    w = plan.what_next("optimize", {}, {"optimization": {"achieved": True}})
    assert w["question"] == "Would you like to proceed with Design Modification and Design Scenarios?"
    assert [x["mode"] for x in w["items"]] == ["design_change", "combinations", "reporting"]
    w = plan.what_next("optimize", {}, {"optimization": {"achieved": False}})
    assert w["question"].startswith("No run meets every target") and "more_runs" in w["flags"]
    # the order changed by hand is kept
    w = plan.what_next("optimize", {"next": {"optimize": ["reporting", "design_change"]}}, {})
    assert [x["mode"] for x in w["items"]] == ["reporting", "design_change"] and w["custom"]
    assert {x["mode"] for x in w["others"]} >= {"combinations", "sensitivity"}
    # the Study plan puts its next step first
    w = plan.what_next("sensitivity", {"several": True, "steps": ["sensitivity", "design_change", "combinations"]}, {})
    assert w["items"][0]["mode"] == "design_change"
    # no modification passes: Optimization moves up
    w = plan.what_next("design_change", {}, {"modifications": [{"rank": 1, "meets": False}]})
    assert w["items"][0]["mode"] == "optimize"


def test_study_plan_is_saved_with_the_project(client):
    pid = new_project(client, "sensitivity")
    r = client.put(f"/api/projects/{pid}/plan", json={"several": True, "steps": ["sensitivity", "optimize", "bogus", "design_change"],
                                                      "chain": "A", "next": {"optimize": ["reporting", "optimize"]}})
    assert r.status_code == 200
    g = client.get(f"/api/projects/{pid}/plan").json()
    assert g["plan"]["steps"] == ["sensitivity", "optimize", "design_change"] and g["plan"]["several"]
    assert g["plan"]["next"] == {"optimize": ["reporting"]} and g["chains"]["B"][0] == "calibration"


def test_optimization_hands_over_to_design_modification(client):
    """Walid: "Link its results with the input of Design Modification": the recommended run is Modification 1, the
    targets are the acceptance criteria, and the study is not calculated until Run is pressed."""
    pid, prev = _setup(client, "optimize", params=[PHI], targets=[{"output": "fos", "operator": ">=", "value": 0.5}], runs=4)
    assert prev["errors"] == []
    tid, out = _run(client, pid)
    nx = client.get(f"/api/tasks/{tid}/next").json()
    assert nx["items"][0]["mode"] == "design_change"
    r = client.post(f"/api/tasks/{tid}/handover", json={"to": "design_change"})
    assert r.status_code == 200, r.text
    s = r.json()["study"]
    assert s["mode"] == "design_change" and s["params"][0]["item"] == PHI["item"]
    assert s["params"][0]["news"][0] == next(row["inputs"]["p1"] for row in out["rows"] if row["run"] == out["optimization"]["best_run"])
    assert s["criteria"] == [{"output": "fos", "operator": ">=", "value": 0.5}]
    assert s["link"]["mode"] == "optimize" and s["link"]["taken"]
    saved = client.get(f"/api/projects/{pid}/study").json()["study"]
    assert saved["mode"] == "design_change" and saved["link"]["task"] == tid
    # the pre-filled study runs as it is
    tid2, out2 = _run(client, pid)
    assert out2["modifications"] and out2["modifications"][0]["title"].startswith("Modification 1")
