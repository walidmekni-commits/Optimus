"""V26.1.9: every new PLAXIS feature reaches every relevant module: variable inputs (section 2), control parameters /
targets / measured results / reported results (section 1), the reports (Word, Excel) and the views (model geometry).
Walid, 24 Nov 2026: "make sure to involve [the missing features] in control parameters, variable input, reporting,
visualization etc. for all the relevant modules"."""
import io
import zipfile

import pytest

from app import exports, features, plaxis_model, study
from tests.test_studies import _run, _setup
from tests.test_v2619_p1 import _features
from tests.test_workflow import client, new_project  # noqa: F401  (fixture)

NEW_INPUTS = ["add:N1", "prestress:Anchor@Phase_3", "design:approach", "drainage:Clay", "iface:PositiveInterface_1.Rinter",
              "wlevel:@Phase_4", "pore:Phase_4", "dyn:frequency"]
NEW_RESULTS = ["fos", "probe0:uv", "trough", "vl", "skin", "q", "head", "iface_rel", "geogrid_n", "dyn_ax"]


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read("word/document.xml").decode("utf-8")


def _xlsx_sheets(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return z.read("xl/workbook.xml").decode("utf-8")


@pytest.mark.parametrize("v", ["2d", "3d"])
def test_catalog_offers_every_new_input_and_result_in_every_mode(v):
    m = plaxis_model.demo(v)
    s = _features(v)
    for mode in study.MODES:
        cat = study.catalog(m, {**s, "mode": mode})
        keys = {p["key"] for p in cat["params"]}
        outs = {o["key"]: o for o in cat["outputs"]}
        assert {"add:N1", "design:approach", "drainage:Clay", "wlevel:@Phase_4", "dyn:frequency"} <= keys, mode
        assert {"probe0:uv", "vl", "dyn_ax", "skin", "q"} <= set(outs), mode
        assert all(o["domain"] in cat["out_domains"] for o in outs.values())
        assert all(p["domain"] in cat["domains"] for p in cat["params"])


@pytest.mark.parametrize("mode,extra", [
    ("sensitivity", {"controls": [{"output": "fos", "how": "fixed", "value": 1.3}, {"output": "probe0:uv"},
                                  {"output": "dyn_ax"}], "runs": 4}),
    ("optimize", {"targets": [{"output": "fos", "operator": ">=", "value": 1.0},
                              {"output": "probe0:utot", "operator": "<=", "value": 1.0}], "runs": 4}),
    ("combinations", {"outputs": ["fos", "vl", "skin", "dyn_ax"], "runs": 3}),
    ("calibration", {"measured": [{"output": "probe0:uv", "value": -0.01}, {"output": "q", "value": 0.2}], "runs": 3}),
    ("design_change", {"outputs": ["fos", "trough", "iface_rel"], "runs": 2}),
    ("reporting", {"outputs": NEW_RESULTS, "runs": 1}),
])
def test_every_module_runs_the_features_and_reports_them(client, mode, extra):
    if mode == "design_change":
        params = [{"item": k, "new": n} for k, n in (("add:N1", "added"), ("design:approach", "EC7 DA1 Combination 2"),
                                                       ("wlevel:@Phase_4", -10.5), ("dyn:frequency", 4))]
    elif mode == "reporting":
        params = []
    else:
        params = [{"item": "add:N1"}, {"item": "prestress:Anchor@Phase_3", "min": 150, "max": 300},
                  {"item": "wlevel:@Phase_4", "min": -11, "max": -9}]
    pid, prev = _setup(client, mode, params=params, **extra, **_features("2d"))
    assert prev["errors"] == [], prev["errors"]
    tid, out = _run(client, pid)
    # the factor of safety after each chosen stage is a main result in every module (not hidden as a stage value)
    fos = [t for t in out.get("targets") or [] if t.get("safety_after")]
    if any(x.get("output") == "fos" for x in extra.get("controls", []) + extra.get("targets", [])) or "fos" in extra.get("outputs", []):
        assert {t["safety_after"] for t in fos} == {"Phase_2", "Phase_4"}
        assert all(not t.get("stage") for t in fos)
        assert all("after" in r["result"] for r in out["rows"] if r["status"] == "succeeded"), out["rows"]
    # what each run set in PLAXIS is kept with the run and shown with it
    ran = [r for r in out["rows"] if r["status"] == "succeeded"]
    assert ran and any((r.get("outputs") or {}).get("_stages_applied") for r in ran)
    # the reports: the study's features, the other PLAXIS objects and what each run set
    d = client.get(f"/api/tasks/{tid}/report.docx")
    assert d.status_code == 200
    txt = _docx_text(d.content)
    assert "PLAXIS features of the study" in txt and "New structure New_N1" in txt and "Result point Corner" in txt
    assert "Factor of safety per stage" in txt and "Dynamic phase" in txt
    assert "What each run set in PLAXIS" in txt and "Interfaces, tunnels, wells, drains" in txt
    assert "@safety:" not in txt  # the added phases are named for the reader
    x = client.get(f"/api/tasks/{tid}/results.xlsx?swap=0")
    assert x.status_code == 200 and "PLAXIS features" in _xlsx_sheets(x.content)


def test_model_geometry_draws_new_structures_and_result_points():
    m = plaxis_model.demo("2d")
    s = _features("2d")
    fp = features.points(m, s)
    assert fp["added"][0]["name"] == "New_N1" and fp["probes"][0]["name"] == "Corner"
    assert exports.geometry_png(m, s) and exports.geometry_png(m)
    rows = features.describe(m, s)
    assert [r[0] for r in rows][:2] == ["Factor of safety per stage", "Result point Corner"]


def test_report_names_the_added_phases():
    m = plaxis_model.demo("2d")
    ph = m["phases"][2]
    assert exports._phase_text({"phase": "@safety:" + ph["name"]}, m) == f"safety phase after {ph.get('id') or ph['name']} (added)"
    assert exports._phase_text({"phase": "@dynamic"}, m) == "dynamic phase (added)"
    assert exports._phase_text({"phase": ""}, m) == "last phase"
