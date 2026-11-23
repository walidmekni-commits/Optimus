"""Run optimization workflow: PLAXIS model -> items -> run plan (logical / customized) -> compiled scenarios ->
outputs -> runs -> compiled results."""
import io
import json

from app import plaxis_model, sequence
from app.schema import coerce_variable
from tests.test_workflow import client, new_project  # noqa: F401  (fixture)


def _model(client, pid):
    r = client.post(f"/api/projects/{pid}/plaxis-model/read?demo=1")
    assert r.status_code == 200, r.text
    return r.json()


def test_plaxis_version_choice_and_demo_model(client):
    r = client.post("/api/projects", data={"objective": "optimize", "title": "T", "source": "upload",
                                           "plaxis_version": "3d"})
    pid = r.json()["project"]["id"]
    assert r.json()["project"]["setup"]["plaxis"] == "3d"
    m = _model(client, pid)
    assert m["version"] == "3d" and m["model"]["vertical"] == "z" and "<svg" in m["svg"]
    assert any(c["label"] == "Global mesh (element distribution)" for c in m["candidates"])
    client.put(f"/api/projects/{pid}/plaxis-version", json={"version": "2d"})
    m = _model(client, pid)
    assert m["version"] == "2d" and m["model"]["counts"]["soil_polygons"] == 4
    # .p2dx file -> 2D when not chosen
    pid2 = new_project(client, "optimize")
    assert client.get(f"/api/projects/{pid2}").json()["project"]["setup"]["plaxis"] == "3d"  # wall.p3d


def test_model_json_from_the_script_uploads(client):
    pid = new_project(client, "optimize")
    m = plaxis_model.demo("2d")
    m["source"] = "plaxis"
    r = client.post(f"/api/projects/{pid}/plaxis-model/upload",
                    files={"file": ("model.json", io.BytesIO(json.dumps(m).encode()), "application/json")})
    assert r.status_code == 200 and r.json()["model"]["source"] == "file"
    bad = client.post(f"/api/projects/{pid}/plaxis-model/upload", files={"file": ("x.json", b"{}", "application/json")})
    assert bad.status_code == 400


def test_logical_sequencing_20_percent_per_run():
    v = coerce_variable({"name": "p1", "label": "Load", "unit": "kPa", "kind": "range", "min": 80, "max": 150,
                         "current": "100", "section": "loads"})
    assert sequence.logical_steps(v, 5, {"from": 100, "to": 150}) == [110, 120, 130, 140, 150]
    mesh = coerce_variable({"name": "p2", "label": "Mesh", "kind": "values", "section": "mesh",
                            "plaxis_ref": "Mesh.size", "values": plaxis_model.mesh_values("2d")})
    assert [s[0] for s in sequence.logical_steps(mesh, 3, {})] == ["Very coarse", "Medium", "Very fine"]


def test_full_optimization_workflow(client):
    pid = new_project(client, "optimize")
    client.put(f"/api/projects/{pid}/plaxis-version", json={"version": "2d"})
    m = _model(client, pid)
    keys = {c["label"]: c["key"] for c in m["candidates"]}
    pick = [keys["Global mesh (element distribution)"], keys["Clay Friction angle φ'"], keys["LineLoad_1_1 qy"]]
    r = client.put(f"/api/projects/{pid}/plaxis-model/selection", json={"items": pick}).json()
    vs = r["setup"]["variables"]
    assert [v["section"] for v in vs] == ["mesh", "soil", "structures"] and vs[1]["plaxis_ref"] == "Clay.phi"
    assert set(r["selected"]) == set(pick)
    # run plan: mesh 4 runs logical, materials 5 logical, loads 2 customized -> 4 x 5 x 2 = 40 (+ existing model)
    cfg = client.get(f"/api/projects/{pid}/sequence").json()["config"]
    cfg["criteria"]["mesh"]["runs"] = 4
    cfg["criteria"]["soil"]["runs"] = 5
    cfg["ends"]["p2"] = {"from": 25, "to": 30}
    cfg["criteria"]["structures"] = {"runs": 2, "mode": "custom", "custom": {"p3": ["-100", "-120"]}}
    seq = client.put(f"/api/projects/{pid}/sequence", json=cfg).json()
    assert seq["total"] == 41 and not seq["errors"]
    titles = [x["title"] for x in seq["rows"]]
    assert titles[0] == "Run 0: Existing model"
    assert titles[1] == "Run 1: Very coarse mesh / Clay Friction angle φ' 26 ° / LineLoad_1_1 qy -100 kN/m/m"
    assert "Clay Friction angle φ' 30 °" in titles[-1] and "-120" in titles[-1]
    cfg["combine"] = "oat"
    assert client.put(f"/api/projects/{pid}/sequence", json=cfg).json()["total"] == 1 + 4 + 5 + 2
    cfg["combine"] = "together"
    assert client.put(f"/api/projects/{pid}/sequence", json=cfg).json()["total"] == 1 + 5
    # outputs to extract at every run
    cat = {o["key"]: o for o in client.get("/api/outputs?version=2d").json()}
    targets = [{"name": "T1", "label": cat["fos"]["label"], "result": "FoS", "phase": "Phase_5", "operator": ">=", "value": 1.3},
               {"name": "T2", "label": cat["plate_m"]["label"], "result": cat["plate_m"]["result"], "operator": "report",
                "unit": "kN·m/m"},
               {"name": "T3", "label": cat["settle"]["label"], "result": cat["settle"]["result"], "operator": "report", "unit": "m"}]
    setup = client.get(f"/api/projects/{pid}").json()["project"]["setup"]
    client.put(f"/api/projects/{pid}/setup", json={"variables": setup["variables"], "targets": targets, "goal": None})
    plan = client.post(f"/api/projects/{pid}/plan").json()
    assert plan["type"] == "task_summary", plan
    tid = plan["task"]["id"]
    client.post(f"/api/tasks/{tid}/approve")
    t = client.get(f"/api/tasks/{tid}").json()
    assert t["status"] == "succeeded" and len(t["runs"]) == 6
    assert t["runs"][2]["title"].startswith("Run 2: ") and "mesh" in t["runs"][2]["title"]
    assert "Best design: Run" in t["result"]["text"]
    c = client.get(f"/api/tasks/{tid}/compiled").json()
    assert len(c["rows"]) == 6 and c["stats"] and c["figures"]
    assert any("against" in f["title"] for f in c["figures"]) and "<svg" in c["figures"][0]["svg"]
    rep = client.get(f"/api/tasks/{tid}/report.html").text
    assert "Compiled results" in rep and "Run 2:" in rep
    csv = client.get(f"/api/tasks/{tid}/results.csv").text.splitlines()
    assert csv[6].startswith("0,Existing model")


class _V:
    def __init__(self, v):
        self.value = v


class _Obj:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v if isinstance(v, (_Obj, list)) else _V(v))


def test_extract_reads_a_plaxis_session():
    """extract() against a stand-in for plxscripting objects (Name.value, x.value, First / Second ...)."""
    p1, p2 = _Obj(Name="Point_1", x=0.0, y=0.0), _Obj(Name="Point_2", x=0.0, y=-12.0)
    line = _Obj(Name="Line_1", First=p1, Second=p2)
    clay = _Obj(Name="Clay", Identification="Soft clay", phi=24.0, cref=8.0, Eref=9000.0)
    clay._plx_type = "SoilMat"
    wall = _Obj(Name="Wall", Identification="Sheet pile", EA=4e6, EI=8e4)
    wall._plx_type = "PlateMat"
    plate = _Obj(Name="Plate_1", Parent=line, Material=wall)
    zone = _Obj(Top=0.0, Bottom=-12.0)
    layer = _Obj(Name="Soillayer_1", Zones=[zone], Soil=_Obj(Material=clay))
    bh = _Obj(Name="Borehole_1", x=0.0, Head=-2.0)
    load = _Obj(Name="LineLoad_1_1", qy_start=-15.0, Parent=line)
    g = _Obj(Points=[p1, p2], Lines=[line], Materials=[clay, wall], Plates=[plate], Boreholes=[bh],
             Soillayers=[layer], LineLoads=[load], Phases=[_Obj(Name="Phase_1", Identification="Dig", DeformCalcType="Plastic")])
    s = _Obj()
    s.is_3d = False
    m = plaxis_model.extract(s, g)
    assert m["version"] == "2d" and m["counts"]["points"] == 2 and m["structures"][0]["points"] == [[0.0, 0.0, 0.0], [0.0, -12.0, 0.0]]
    assert m["boreholes"][0]["layers"][0] == {"name": "Soillayer_1", "material": "Clay", "top": 0.0, "bottom": -12.0}
    keys = {c["plaxis_ref"] for c in plaxis_model.candidates(m)}
    assert {"Clay.phi", "Wall.EI", "LineLoad_1_1.qy_start", "Borehole_1.Head", "Point_2.y", "Mesh.size"} <= keys
    assert "<svg" in plaxis_model.svg(m)


def test_own_model_is_never_replaced_by_the_example(client, monkeypatch):
    """Reading the uploaded project goes to PLAXIS (auto-connect) even with the simulated solver; when PLAXIS can't
    be reached the user gets the reason, not the example model."""
    from app import plaxis_launcher
    pid = new_project(client, "optimize")  # wall.p3d uploaded without its .p3dat folder
    j = client.post(f"/api/projects/{pid}/plaxis-model/read").json()
    assert j["status"] == "done" and "p3dat" in j["errors"][0]
    assert client.get(f"/api/projects/{pid}/plaxis-model").json()["model"] is None

    calls = []

    def fake_connect(version, progress=lambda m: None, **kw):
        calls.append(version)
        raise plaxis_launcher.ConnectError("PLAXIS 3D Input was not found on this PC")
    monkeypatch.setattr(plaxis_launcher, "connect", fake_connect)
    zbuf = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("Wall/Wall.p3d", b"x")
        z.writestr("Wall/Wall.p3dat/data.plxdat", b"x")
    r = client.post("/api/projects", data={"objective": "optimize", "title": "Z", "source": "upload"},
                    files=[("files", ("Wall.zip", zbuf.getvalue(), "application/zip"))])
    pid = r.json()["project"]["id"]
    j = client.post(f"/api/projects/{pid}/plaxis-model/read").json()
    assert calls == ["3d"] and "not found" in j["errors"][0]


def test_launcher_starts_plaxis_when_nothing_answers(monkeypatch, tmp_path):
    from app import plaxis_launcher, settings
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ensure_plxscripting_importable", lambda cfg: "here")
    monkeypatch.setattr(plaxis_launcher, "find_input_exe", lambda v: r"C:\\PLAXIS\\Plaxis3DInput.exe")
    state = {"started": None}
    monkeypatch.setattr(plaxis_launcher, "_listening", lambda h, p: state["started"] is not None)
    monkeypatch.setattr(plaxis_launcher.subprocess, "Popen", lambda args, cwd=None: state.update(started=args))
    monkeypatch.setattr(plaxis_launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(plaxis_launcher, "_new_server", lambda h, p, pw: ("s", "g"))
    assert plaxis_launcher.connect("3d") == ("s", "g")
    cfg = settings.load()
    assert state["started"][1] == f"--AppServerPort={cfg['port']}" and state["started"][2] == f"--AppServerPassword={cfg['password']}"
    assert cfg["solver"] == "plaxis" and cfg["password"]


def test_missing_data_folder_can_be_added_on_page_2(client, monkeypatch):
    from app import plaxis_launcher
    seen = {}

    def fake_connect(version, progress=lambda m: None, **kw):
        raise plaxis_launcher.ConnectError("stop here")
    monkeypatch.setattr(plaxis_launcher, "connect", fake_connect)
    r = client.post("/api/projects", data={"objective": "optimize", "title": "Z", "source": "upload"},
                    files=[("files", ("Zayed_SS2.p3d", b"x", "application/octet-stream"))])
    pid = r.json()["project"]["id"]
    j = client.post(f"/api/projects/{pid}/plaxis-model/read").json()
    assert "Zayed_SS2.p3dat" in j["errors"][0]
    up = client.post(f"/api/projects/{pid}/model-files",
                     files=[("files", ("data.plxdat", b"1", "application/octet-stream")),
                            ("files", ("geo.dat", b"2", "application/octet-stream"))],
                     data={"paths": ["Zayed_SS2.p3dat/data.plxdat", "Zayed_SS2.p3dat/sub/geo.dat"]})
    assert up.status_code == 200 and up.json()["saved"] == 2
    j = client.post(f"/api/projects/{pid}/plaxis-model/read").json()
    assert j["errors"] == ["Could not read your PLAXIS model: stop here"]  # the folder is found now


def test_plaxis_modes_and_run_geometry(client):
    """Variables pages follow PLAXIS Input's modes, with the model's own parameters; each run's geometry is drawn."""
    from app.schema import SECTIONS
    assert list(SECTIONS) == ["soil", "structures", "mesh", "flow", "stages"]
    r = client.post("/api/projects", data={"objective": "optimize", "title": "T", "source": "upload",
                                           "plaxis_version": "2d"})
    pid = r.json()["project"]["id"]
    m = _model(client, pid)
    by = {}
    for c in m["candidates"]:
        by.setdefault(c["section"], []).append(c)
    assert set(by) == set(SECTIONS)
    assert any(c["plaxis_ref"] == "Soillayer_2.Zones[0].Bottom" for c in by["soil"])
    assert any(c["plaxis_ref"] == "Phase_3.Deform.MaxSteps" for c in by["stages"])
    assert any(c["plaxis_ref"] == "NodeToNodeAnchor_1.PrestressForce@Phase_3" and c["unit"] == "kN" for c in by["stages"])
    assert next(c for c in by["soil"] if c["plaxis_ref"] == "Clay.phi")["unit"] == "°"
    assert next(c for c in by["structures"] if c["plaxis_ref"] == "DWall.EI")["unit"] == "kN·m²/m"
    keys = ["geo:Plate_1.toe", "layer:Soillayer_2.0.Bottom"]
    vs = client.put(f"/api/projects/{pid}/plaxis-model/selection", json={"items": keys}).json()["setup"]["variables"]
    assert [v["section"] for v in vs] == ["structures", "soil"]
    seq = client.get(f"/api/projects/{pid}/sequence").json()
    run = seq["rows"][-1]
    g = client.post(f"/api/projects/{pid}/run-geometry", json={"inputs": run["inputs"]}).json()
    assert "<svg" in g["svg"] and g["moved"] == ["Point_6"]
    assert {c["label"] for c in g["changes"]} == {"Plate_1 toe level", "Soillayer_2 (Clay) bottom level"}
    assert client.post(f"/api/projects/{pid}/run-geometry", json={"inputs": {}}).json()["changes"] == []


def test_model_view_pick_and_move_whole_structure(client):
    """Model view: clickable points / objects, moving a whole structure shifts every point from where it is."""
    from app import plaxis_model
    r = client.post("/api/projects", data={"objective": "optimize", "title": "T3", "source": "upload",
                                           "plaxis_version": "3d"})
    pid = r.json()["project"]["id"]
    m = _model(client, pid)
    objs = {o["name"]: o for o in m["objects"]}
    plate = next(o for o in objs.values() if o["kind"] == "plate")
    assert len(plate["points"]) >= 2 and 'class="obj"' in m["svg"] and 'class="pt' in m["svg"]
    for view in ("front", "side", "top"):
        assert "<svg" in client.get(f"/api/projects/{pid}/plaxis-model?view={view}").json()["svg"]
    key = f"move:{plate['name']}.x"
    first = plate["points"][0]
    r = client.put(f"/api/projects/{pid}/plaxis-model/selection", json={"items": [key, f"pt:{first}.z"]}).json()
    v = next(x for x in r["setup"]["variables"] if x["item"] == key)
    assert v["plaxis_ref"].count("+") == len(plate["points"]) and v["section"] == "structures"
    assert 'fill="#2f7fd0"' in r["svg"]  # picked point drawn in blue (the selection colour)
    pts = {p["name"]: p for p in m["model"]["points"]}
    run, changes, moved = plaxis_model.apply_inputs(m["model"], [v], {v["name"]: 1.5})
    after = {p["name"]: p for p in run["points"]}
    for n in plate["points"]:
        assert abs(after[n]["x"] - (pts[n]["x"] + 1.5)) < 1e-9
    assert set(plate["points"]) <= set(moved) and len(changes) == 1

