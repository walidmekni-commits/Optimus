"""Geometry selected in the view: shifts and spacings, similar copies, whole piles."""
import math
from app import geosel, plaxis_model, study


def _raft():
    """Two vertical piles (top at z = 0, toe at z = -20) 3 m apart, a third pile 3 m further."""
    pts = []
    for i, x in enumerate((0.0, 3.0, 6.0)):
        pts += [{"name": f"Point_{2 * i + 1}", "x": x, "y": 0.0, "z": 0.0},
                {"name": f"Point_{2 * i + 2}", "x": x, "y": 0.0, "z": -20.0}]
    st = [{"kind": "embedded_beam", "name": f"EmbeddedBeam_{i + 1}", "material": "Pile",
           "points": [[x, 0.0, 0.0], [x, 0.0, -20.0]]} for i, x in enumerate((0.0, 3.0, 6.0))]
    return {"version": "3d", "axes": ["x", "y", "z"], "points": pts, "structures": st, "lines": [], "loads": []}


def test_spacing_moves_the_whole_second_pile():
    m = _raft()
    c = geosel.candidate(m, {"id": "a", "label": "Pt 1 – Pt 3", "members": [["Point_1", "Point_3"]], "whole": True}, "s")
    assert c["current"] == 3.0 and c["key"] == "grp:a"
    assert c["plaxis_ref"] == "Point_3.x+*1~3; Point_4.x+*1~3"  # top and toe of pile 2


def test_similar_copies_move_whole_piles():
    m = _raft()
    e = {"id": "b", "label": "Pt 1 – Pt 3", "members": [["Point_1", "Point_3"], ["Point_3", "Point_5"]], "whole": False}
    c = geosel.candidate(m, e, "s")
    assert c["label"].startswith("Pt 1 – Pt 3 and similar (2 items) spacing")
    # whole piles always (the old "points only" is ignored); the third pile moves twice: spacings stay equal
    assert c["plaxis_ref"] == "Point_3.x+*1~3; Point_4.x+*1~3; Point_5.x+*2~3; Point_6.x+*2~3"
    sh = geosel.candidate(m, e, "z")
    assert sh["plaxis_ref"].startswith("Point_1.z+; Point_2.z+; Point_3.z+") and sh["current"] == 0.0


def test_study_builds_runs_from_selected_geometry():
    m = plaxis_model.demo("2d")
    e = {"id": "g1", "label": "Pt 7 – Pt 8", "members": [["Point_7", "Point_8"]], "whole": True, "mode": "s"}
    b = study.build(m, {"mode": "combinations", "geo": [e], "params": [{"item": "grp:g1", "min": 10, "max": 14}],
                        "outputs": ["fos"], "runs": 4})
    assert b["errors"] == [] and len(b["runs"]) == 4
    assert b["variables"][0]["plaxis_ref"].startswith("Point_8.x+*")


def _anchors():
    """Two similar anchors from a wall (x = 0) down to x = 8, the first with a grout body (geogrid) at its end."""
    pts = [{"name": "W1", "x": 0.0, "y": 0.0, "z": 0.0}, {"name": "W2", "x": 0.0, "y": 0.0, "z": -12.0},
           {"name": "A1", "x": 0.0, "y": 0.0, "z": -2.0}, {"name": "B1", "x": 8.0, "y": 0.0, "z": -8.0},
           {"name": "G1", "x": 12.0, "y": 0.0, "z": -11.0},
           {"name": "A2", "x": 0.0, "y": 5.0, "z": -2.0}, {"name": "B2", "x": 8.0, "y": 5.0, "z": -8.0}]
    xyz = {p["name"]: [p["x"], p["y"], p["z"]] for p in pts}
    st = [{"kind": "plate", "name": "Plate_1", "points": [xyz["W1"], xyz["W2"]]},
          {"kind": "anchor", "name": "Anchor_1", "points": [xyz["A1"], xyz["B1"]]},
          {"kind": "geogrid", "name": "Geogrid_1", "points": [xyz["B1"], xyz["G1"]]},
          {"kind": "anchor", "name": "Anchor_2", "points": [xyz["A2"], xyz["B2"]]}]
    return {"version": "3d", "axes": ["x", "y", "z"], "points": pts, "structures": st, "lines": [], "loads": []}


def test_length_of_similar_anchors_in_one_run_and_the_grout_follows():
    m = _anchors()
    e = {"id": "an", "label": "Anchor_1", "members": [["A1", "B1"], ["A2", "B2"]], "whole": True}
    c = geosel.candidate(m, e, "s")
    assert c["label"] == "Anchor_1 and similar (2 items) length" and c["current"] == 10.0
    var = {"name": "p1", "plaxis_ref": c["plaxis_ref"]}
    mv = geosel.displace(m, [var], {"p1": 15.0})  # one run: both anchors 15 m, the wall end stays
    assert set(mv) == {"B1", "G1", "B2"}
    assert mv["B1"] == [12.0, 0.0, -11.0] and mv["B2"] == [12.0, 5.0, -11.0]
    assert mv["G1"] == [16.0, 0.0, -14.0]  # the grout body moves with the anchor end and keeps its length


def test_runs_customized_and_controls_min_avg():
    m = {**_anchors(), "materials": [], "boreholes": [], "phases": [], "water": {}, "soil_polygons": [], "mesh": {}}
    s = {"mode": "sensitivity", "runs": 3, "baseline": True,
         "geo": [{"id": "an", "label": "Anchor_1", "members": [["A1", "B1"], ["A2", "B2"]], "whole": True, "mode": "s"}],
         "params": [{"item": "grp:an", "min": 8, "max": 12}],
         "controls": [{"output": "utot", "how": "fixed", "stat": "avg"}], "over": {"2": {"grp:an": 14}},
         "confirmed": [0, 1]}
    b = study.build(m, s)
    assert not b["errors"], b["errors"]
    assert [r["values"] for r in b["rows"]] == ["Existing design", "Anchor_1 and similar (2 items) length = 8 m",
                                                 "Anchor_1 and similar (2 items) length = 14 m"]
    assert b["rows"][2]["custom"] and b["rows"][1]["confirmed"] and not b["rows"][2]["confirmed"]
    assert b["rows"][2]["moves"]["B2"] == [11.2, 5.0, -10.4] and b["rows"][0]["moves"] == {}
    t = b["targets"][0]
    assert t["result"].endswith("#avg") and t["label"] == "Total displacement |u| (average)"


def test_each_item_with_its_own_range_still_one_run():
    m = _anchors()
    e = {"id": "an", "label": "Anchor_1", "members": [["A1", "B1"], ["A2", "B2"]], "whole": True, "mode": "s",
         "ranges": {"1": [9.0, 11.0]}}
    c = geosel.candidate(m, e, "s", (8.0, 12.0))
    var = {"name": "p1", "plaxis_ref": c["plaxis_ref"]}
    for v, own in ((8.0, 9.0), (12.0, 11.0), (10.0, 10.0)):  # anchor 2 follows its own range, in the same run
        mv = geosel.displace(m, [var], {"p1": v})
        a2, b2 = (0.0, 5.0, -2.0), mv.get("B2", [8.0, 5.0, -8.0])
        assert abs(math.dist(a2, b2) - own) < 1e-6
        assert abs(math.dist((0.0, 0.0, -2.0), mv.get("B1", [8.0, 0.0, -8.0])) - v) < 1e-6


def test_deleted_run_is_left_out():
    m = {**_anchors(), "materials": [], "boreholes": [], "phases": [], "water": {}, "soil_polygons": [], "mesh": {}}
    s = {"mode": "sensitivity", "runs": 3, "baseline": True,
         "geo": [{"id": "an", "label": "Anchor_1", "members": [["A1", "B1"]], "whole": True, "mode": "s"}],
         "params": [{"item": "grp:an", "min": 8, "max": 12}], "controls": [{"output": "fos", "how": "fixed"}]}
    sig = study.build(m, s)["rows"][1]["sig"]
    b = study.build(m, {**s, "skip": [sig]})
    assert b["deleted"] == 1 and [r["values"] for r in b["rows"]] == ["Existing design", "Anchor_1 length = 12 m"]
    assert b["rows"][1]["run"] == 1 and b["rows"][1]["key"] == 2


def test_remove_candidates():
    m = {**_anchors(), "materials": [], "boreholes": [], "phases": [{"name": "InitialPhase"}, {"name": "Phase_1", "id": "Dig"}],
         "water": {}, "soil_polygons": [], "mesh": {}}
    keys = {c["key"]: c for c in study.catalog(m)["params"]}
    assert keys["del:Anchor_1"]["values"] == ["keep: 0", "remove: 1"] and keys["del:Anchor_1"]["domain"] == "structures"
    assert keys["del:Phase_1"]["domain"] == "stages" and keys["del:A1"]["domain"] == "geometry"


def test_value_list_sets_runs():
    from app import study
    p = {"item": "x", "how": "list", "list": "8, 10;12  14"}
    assert study.value_list(p) == [8.0, 10.0, 12.0, 14.0]
    var = {"kind": "range", "min": 0, "max": 1}
    assert study.param_values(p, var, 5, "even") == [8.0, 10.0, 12.0, 14.0, None]
    assert study.value_list({"how": "range", "list": "1,2"}) is None


def test_runs_from_value_list():
    m = {**_anchors(), "materials": [], "boreholes": [], "phases": [], "water": {}, "soil_polygons": [], "mesh": {}}
    s = {"mode": "sensitivity", "runs": 4, "baseline": True,
         "geo": [{"id": "an", "label": "Anchor_1", "members": [["A1", "B1"], ["A2", "B2"]], "whole": True, "mode": "s"}],
         "params": [{"item": "grp:an", "min": 8, "max": 12, "how": "list", "list": "9, 11, 13"}],
         "controls": [{"output": "utot", "how": "fixed"}]}
    b = study.build(m, s)
    assert not b["errors"], b["errors"]
    assert [r["values"] for r in b["rows"]][1:] == [f"Anchor_1 and similar (2 items) length = {v} m" for v in (9, 11, 13)]


def test_puzzle_runs_and_order():
    m = {**_anchors(), "materials": [], "boreholes": [], "phases": [], "water": {}, "soil_polygons": [], "mesh": {}}
    s = {"mode": "sensitivity", "runs": 9, "baseline": True,
         "geo": [{"id": "an", "label": "Anchor_1", "members": [["A1", "B1"], ["A2", "B2"]], "whole": True, "mode": "s"}],
         "params": [{"item": "grp:an", "min": 8, "max": 12, "how": "list", "list": "9, 11"}],
         "controls": [{"output": "utot", "how": "fixed"}],
         "puzzle": {"on": True, "runs": [[{"item": "grp:an", "v": 11}], [], [{"item": "grp:an", "v": 9}]]}}
    b = study.build(m, s)
    assert not b["errors"], b["errors"]
    assert [r["values"] for r in b["rows"]] == ["Existing design", "Anchor_1 and similar (2 items) length = 11 m",
                                                 "Anchor_1 and similar (2 items) length = 9 m"]
    b = study.build(m, {**s, "order": [0, 2, 1]})  # Run 2 moved before Run 1
    assert [r["values"].split("= ")[-1] for r in b["rows"]] == ["Existing design", "9 m", "11 m"] and [r["key"] for r in b["rows"]] == [0, 2, 1]
    b = study.build(m, {**s, "puzzle": {"on": True, "runs": []}})
    assert any("puzzle" in e for e in b["errors"])


def _anchor_rows():
    """Like a PLAXIS 3D anchored wall: each anchor is a free length (A–B) and a grout body (B2–C) that starts at a
    second point at the same place as B; geometry lines on both. Three rows along y."""
    pts, lines, sts = [], [], []
    for i, y in enumerate((0.0, 5.0, 10.0)):
        k = i + 1
        pts += [{"name": f"A{k}", "x": 0.0, "y": y, "z": -2.0}, {"name": f"B{k}", "x": -8.0, "y": y, "z": -6.0},
                {"name": f"D{k}", "x": -8.0, "y": y, "z": -6.0}, {"name": f"C{k}", "x": -14.0, "y": y, "z": -9.0}]
        lines += [{"name": f"Line_{2 * k - 1}", "a": f"A{k}", "b": f"B{k}"}, {"name": f"Line_{2 * k}", "a": f"D{k}", "b": f"C{k}"}]
        sts += [{"name": f"Free{k}", "kind": "anchor", "points": [[0.0, y, -2.0], [-8.0, y, -6.0]]},
                {"name": f"Grout{k}", "kind": "embedded_beam", "points": [[-8.0, y, -6.0], [-14.0, y, -9.0]]}]
    return {"version": "3d", "axes": ["x", "y", "z"], "points": pts, "lines": lines, "structures": sts, "loads": [],
            "materials": [], "boreholes": [], "phases": [], "water": {}, "soil_polygons": [], "mesh": {}}


def test_grout_at_a_second_point_follows_and_runs_are_checked():
    from app import geocheck
    m = _anchor_rows()
    e = {"id": "fr", "label": "Free1", "members": [["A1", "B1"], ["A2", "B2"], ["A3", "B3"]], "whole": False, "mode": "s"}
    c = geosel.candidate(m, e, "s")
    mv = geosel.displace(m, [{"name": "p1", "plaxis_ref": c["plaxis_ref"]}], {"p1": 5.0})
    assert {"B1", "D1", "C1", "B3", "D3", "C3"} <= set(mv)  # the second point and the whole grout body move too
    L = lambda a, b: math.dist(mv.get(a), mv.get(b))
    assert abs(math.dist(mv["A1"] if "A1" in mv else (0, 0, -2), mv["B1"]) - 5.0) < 1e-4 and abs(L("D1", "C1") - math.dist((-8, 0, -6), (-14, 0, -9))) < 1e-4
    assert mv["B1"] == mv["D1"]
    own = [((0.0, y, -2.0), (-8.0, y, -6.0)) for y in (0.0, 5.0, 10.0)]
    assert geocheck.check(m, mv, own) == []
    # only B1 moved (what "points only" did): the free length runs into its grout body
    d = [-8.0 * 1.25, 0.0, -6.0 - 4.0 * 0.25]
    bad = geocheck.check(m, {"B1": d}, [((0.0, 0.0, -2.0), (-8.0, 0.0, -6.0))])
    assert any(x["level"] == "error" and "overlap" in x["text"] and "Line_1" in x["text"] for x in bad), bad
    # a grout body stretched through a shared point is reported
    warn = geocheck.check(m, {"D1": [-6.0, 0.0, -5.0]}, [])
    assert any(x["level"] == "warning" and "Grout1" in x["text"] for x in warn), warn
    # the study refuses a run whose geometry is wrong
    s = {"mode": "sensitivity", "runs": 2, "baseline": True, "geo": [{**e, "id": "fr"}],
         "params": [{"item": "grp:fr", "min": -1, "max": -1}], "controls": [{"output": "utot", "how": "fixed"}]}
    b = study.build(m, s)
    assert any("No length left" in x or "Turned over" in x for x in b["errors"]), b["errors"]
