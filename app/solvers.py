"""Solver backends. 'plaxis' drives PLAXIS 2D/3D through its remote scripting server; 'simulated' fakes the
results so the whole workflow can be tried and tested without PLAXIS."""
import hashlib
import math
import json
import random
import re
import zipfile
from pathlib import Path

from .config import env
from .planner import numeric
from .schema import Target, Variable

PLAXIS_EXT = (".p3d", ".p2dx", ".p3dxml")


def model_file(files: list[dict]) -> Path | None:
    """First PLAXIS project among the uploaded files (zips are unpacked next to themselves)."""
    for f in files:
        p = Path(f["path"])
        if p.suffix.lower() == ".zip":
            out = p.with_suffix("")
            if not out.exists():
                with zipfile.ZipFile(p) as z:
                    z.extractall(out)
            for q in sorted(out.rglob("*")):
                if q.suffix.lower() in PLAXIS_EXT:
                    return q
        elif p.suffix.lower() in PLAXIS_EXT:
            return p
    return None


def check_data_folder(path: Path) -> None:
    """A .p3d / .p2dx needs its data folder (project.p3dat / project.p2dxdat) next to it, or PLAXIS can't open it."""
    if path.suffix.lower() not in (".p3d", ".p2dx"):
        return
    sib = [q for q in path.parent.iterdir() if q.is_dir() and q.name.lower().startswith(path.stem.lower() + ".")
           and q.name.lower().endswith("dat")]
    if not sib:
        want = path.stem + (".p3dat" if path.suffix.lower() == ".p3d" else ".p2dxdat")
        raise SolverError(f"{path.name} came without its data folder {want}, so PLAXIS can't open it. Zip "
                          f"{path.name} together with the {want} folder and upload the zip, or choose "
                          "'File on this PC' / 'Project open in PLAXIS' on page 1.")


class SolverError(RuntimeError):
    pass


def hoek_brown_mc(sci: float, gsi: float, mi: float, d: float = 0.0, s3max: float | None = None) -> tuple[float, float, str]:
    """V26.1.9 (P2): the Mohr-Coulomb c' (kN/m²) and φ' (°) equivalent to a Hoek-Brown rock mass over 0 < σ3 < σ3max
    (Hoek, Carranza-Torres & Corkum 2002), σ3max = σci / 4 unless given."""
    mb = mi * math.exp((gsi - 100) / (28 - 14 * d))
    s_ = math.exp((gsi - 100) / (9 - 3 * d))
    a = 0.5 + (math.exp(-gsi / 15) - math.exp(-20 / 3)) / 6
    s3max = s3max if s3max is not None else sci / 4
    n = s3max / sci
    k = 6 * a * mb * (s_ + mb * n) ** (a - 1)
    phi = math.degrees(math.asin(k / (2 * (1 + a) * (2 + a) + k)))
    c = sci * ((1 + 2 * a) * s_ + (1 - a) * mb * n) * (s_ + mb * n) ** (a - 1) / \
        ((1 + a) * (2 + a) * math.sqrt(1 + k / ((1 + a) * (2 + a))))
    return c, phi, f"Hoek-Brown σci {sci:g}, GSI {gsi:g}, mi {mi:g}, D {d:g}, σ3max {s3max:g}: mb {mb:.4g}, s {s_:.4g}, a {a:.4g}"


# ---------------------------------------------------------------------------
class SimulatedSolver:
    """Smooth, deterministic fake results. Factor-of-safety-like results rise with each parameter's
    'strength' weight, displacement-like results fall. SIM_FAIL_RATE (0-1) injects random failures."""

    name = "simulated"

    def describe(self, files: list[dict]) -> str:
        names = ", ".join(f["name"] for f in files) or "none"
        return f"Simulated solver (PLAXIS not connected). Uploaded files: {names}."

    @staticmethod
    def _w(key: str) -> float:
        h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        return 0.2 + 0.8 * h

    def run(self, files, inputs: dict, variables: list[Variable], targets: list[Target], run_dir: Path) -> dict:
        rate = float(env("SIM_FAIL_RATE", "0"))
        say = getattr(self, "progress", None) or (lambda m, p=None: None)
        say("Calculating", 10)
        if float(env("SIM_RUN_SECONDS", "0")):  # to watch the progress, Stop and Restart without PLAXIS
            import time
            for k in range(10):
                time.sleep(float(env("SIM_RUN_SECONDS", "0")) / 10)
                say("Calculating", 10 + 8 * (k + 1))
        say("Reading the results", 95)
        if rate and random.random() < rate:
            raise SolverError("Simulated calculation did not converge")
        score, n = 0.0, 0.0
        for v in variables:
            x = numeric(inputs.get(v.name)) if v.name in inputs else numeric(_parse(v.current))
            if x is None:
                lo, hi = v.min or 0, v.max or 1
                x = (lo + hi) / 2
            lo, hi = (v.min, v.max) if v.min is not None and v.max is not None else _span(v)
            u = 0.5 if hi == lo else (x - lo) / (hi - lo)
            score += self._w(v.name) * u
            n += self._w(v.name)
        s = score / n if n else 0.5  # 0..1
        out = {}
        for t in targets:
            key = (t.result or t.name).split("|")[0].strip().lower()
            key, _, stat = key.partition("#")
            if key == "file":
                f = run_dir / "model.json"
                f.write_text(json.dumps({"inputs": {k: str(v) for k, v in inputs.items()}}, indent=1))
                out[t.name] = str(f)
            elif any(k in key for k in ("fos", "msf", "safety", "factor")):
                out[t.name] = round(1.05 + 0.9 * s, 3)
            elif "." in key:  # PLAXIS result type <Group>.<Type> (values in PLAXIS units: m, kN, kN.m)
                rtype = key.split(".", 1)[1]
                w = self._w(key)
                if rtype.startswith("u"):
                    out[t.name] = round(0.08 * w * (1 - 0.7 * s), 5)
                elif rtype.startswith("m"):
                    out[t.name] = round(600 * w * (1.2 - 0.6 * s), 2)
                elif "pexcess" in rtype:
                    out[t.name] = round(40 * w * (1.1 - 0.5 * s), 2)
                else:
                    out[t.name] = round(400 * w * (1.15 - 0.5 * s), 2)
            elif key.startswith("u") or any(k in key for k in ("disp", "settle", "defl")):
                out[t.name] = round(0.08 * (1 - 0.7 * s), 5)  # m, as PLAXIS gives it
            else:
                out[t.name] = round(100 * (0.5 + s), 2)
            if stat in ("min", "avg") and isinstance(out.get(t.name), float) and key != "file" and \
                    not any(k in key for k in ("fos", "msf", "safety", "factor")):
                out[t.name] = round(out[t.name] * (0.3 if stat == "min" else 0.55), 5)
            ph = re.search(r"(\d+)$", t.phase or "")
            if ph and (t.phase or "").startswith("@safety:") and isinstance(out.get(t.name), float):
                out[t.name] = round(out[t.name] * (1 + 0.6 / (int(ph.group(1)) + 1)), 3)  # V26.1.9: FoS falls stage by stage
            elif ph and isinstance(out.get(t.name), float) and not any(k in key for k in ("fos", "msf", "safety", "factor")):
                out[t.name] = round(out[t.name] * min(1.0, 0.25 + 0.19 * int(ph.group(1))), 5)  # grows stage by stage
        for t in targets:  # V26.1.9: the settlement trough (m³/m) and the volume loss (%) in their own ranges
            res = t.result or ""
            if "|trough" in res and isinstance(out.get(t.name), float):
                w = self._w(res)
                out[t.name] = round((0.4 + 1.2 * w) * (1.2 - 0.6 * s), 3) if res.split("|trough", 1)[1].startswith(":") \
                    else round((1 + 4 * w) * (1.2 - 0.6 * s), 3)
        for t in targets:  # V26.1.9 (batch 2): the settlement / wall deflection curve of the run
            res = t.result or ""
            if "|profile" in res and isinstance(out.get(t.name), float):
                peak = out[t.name]
                if "wall" in res:
                    pts = [[-z, round(peak * math.sin(math.pi * min(1.0, (z + 1) / 14)) * (1 - z / 40), 6)] for z in range(0, 19)]
                else:
                    pts = [[float(x), round(-peak * math.exp(-((x - 30) / (8 + 6 * s)) ** 2), 6)] for x in range(0, 61, 2)]
                out.setdefault("_profiles", {})[t.name] = pts
                out[t.name] = max(abs(b) for _, b in pts)
        applied = self._applied(inputs, variables)
        if applied:
            out["_stages_applied"] = applied
        plots = [k for k in getattr(self, "want_plots", None) or [] if k in SIM_KEYS]
        if plots:
            try:
                out["_plots"] = _sim_plots(plots, s, run_dir)
            except Exception:  # noqa: BLE001 - images are optional
                pass
        return out


def _sim_applied(self, inputs: dict, variables: list) -> list[str]:
    """V26.1.9: what the PLAXIS solver would set in the stages for this run (kept with the run as with PLAXIS)."""
    out = []
    for ph in getattr(self, "safety_after", None) or []:
        out.append(f"safety phase added after {ph} (simulated)")
    for name, sp in (getattr(self, "additions", None) or {}).items():
        v = next((x for x in variables if x.plaxis_ref == f"{name}.add"), None)
        on = v is None or (numeric(inputs.get(v.name)) if v.name in inputs else 0) == 1
        if on:
            out.append(f"{name}: {sp.get('kind')} added, active from {sp.get('phase')} (simulated)")
    d = getattr(self, "dynamic", None)
    if d:
        out.append(f"dynamic phase added after {d.get('after')} (simulated)")
    for v in variables:
        ref = v.plaxis_ref or ""
        if v.name in inputs and ("@" in ref or ref.startswith(("WaterLevel", "Dynamic.", "Design."))):
            val = inputs[v.name]
            out.append(f"{ref} = {val[0] if isinstance(val, (list, tuple)) else val} (simulated)")
    return out


SimulatedSolver._applied = _sim_applied
SIM_KEYS = ("utot", "ux", "uy", "pexcess", "plate_m", "plate_q", "plate_n")


def _sim_plots(keys: list[str], s: float, run_dir: Path) -> list[str]:
    """Simulated contour images (no PLAXIS): a displacement bowl behind a wall, so the reports can be tried."""
    from .exports import _plt
    import numpy as np
    plt = _plt()
    d = run_dir / "plots"
    d.mkdir(parents=True, exist_ok=True)
    x, y = np.meshgrid(np.linspace(0, 60, 240), np.linspace(-25, 0, 100))
    bowl = np.exp(-((x - 24) / 9) ** 2 - ((y + 3) / 7) ** 2) * (0.08 * (1 - 0.7 * s))
    fields = {"utot": ("Total displacements |u|", bowl), "ux": ("Horizontal displacements ux", -0.8 * bowl * (x > 20)),
              "uy": ("Vertical displacements", -0.6 * bowl),
              "pexcess": ("Excess pore pressures", -0.25 * bowl * (y < -6))}
    wall = {"plate_m": ("Bending moment of the wall", "M [kN·m/m]", 420), "plate_q": ("Shear force of the wall", "Q [kN/m]", 160),
            "plate_n": ("Axial force of the wall", "N [kN/m]", 90)}
    done = []
    for k in keys:
        if k in wall:  # a force diagram along the wall
            lab, ax_lab, amp = wall[k]
            z = np.linspace(0, -18, 120)
            f = amp * (1.2 - 0.6 * s) * (np.sin(np.pi * z / -18) * (1 if k != "plate_n" else 0.4) + (z / -18 if k == "plate_n" else 0) - (0.3 * np.sin(2 * np.pi * z / -18) if k == "plate_q" else 0))
            fig, ax = plt.subplots(figsize=(4.6, 5.2), dpi=110)
            ax.fill_betweenx(z, 0, f, color="#2f7fd0", alpha=.35); ax.plot(f, z, color="#1e4f8a", lw=1.6); ax.axvline(0, color="k", lw=2.5)
            ax.set_xlabel(ax_lab); ax.set_ylabel("z [m]"); ax.grid(alpha=.3)
            ax.set_title(f"{lab} (simulated) · extreme {abs(f).max():.1f}", fontsize=10)
            fig.tight_layout(); fig.savefig(d / f"{k}_sim.png"); plt.close(fig)
            done.append(str(d / f"{k}_sim.png"))
            continue
        if k not in fields:
            continue
        lab, f = fields[k]
        fig, ax = plt.subplots(figsize=(8, 3.6), dpi=110)
        cs = ax.contourf(x, y, f * 1000, levels=16, cmap="jet")
        ax.plot([20, 20], [0, -18], color="k", lw=2.5)
        ax.set_aspect("equal"); ax.set_xlabel("x [m]"); ax.set_ylabel("y [m]")
        cb = fig.colorbar(cs, ax=ax, shrink=.9); cb.set_label("[10⁻³ m]")
        ax.set_title(f"{lab} (simulated) · extreme {abs(f).max() * 1000:.2f}·10⁻³ m", fontsize=10)
        fig.tight_layout(); fig.savefig(d / f"{k}_sim.png"); plt.close(fig)
        done.append(str(d / f"{k}_sim.png"))
    return done


def _parse(v):
    from .planner import parse_value
    return parse_value(v) if v is not None else None


def _span(v: Variable):
    nums = [numeric(_parse(x)) for x in v.values]
    nums = [x for x in nums if x is not None] or [0, 1]
    return min(nums), max(nums)


# ---------------------------------------------------------------------------
from .plaxis_model import area_edit  # noqa: E402
SHIFT_REF = __import__("re").compile(r"^(.+)\.(\w+)\+(?:\*(-?[\d.]+(?:[eE][-+]?\d+)?))?(?:~(-?[\d.]+(?:[eE][-+]?\d+)?))?$")
LOAD_PROPS_ALL = {"sigx", "sigy", "sigz", "sigz_ref", "qx_start", "qy_start", "qz_start", "qx_end", "qy_end", "qz_end",
                  "fx", "fy", "fz"}


def _obj_name(o) -> str:
    try:
        n = o.Name
        return str(getattr(n, "value", n))
    except Exception:  # noqa: BLE001
        return str(o)


from .plaxis_p1 import P1Mixin  # noqa: E402  V26.1.9: the P1 features of the QA/QC missing-features report
from .plaxis_p2 import P2Mixin, new_p2  # noqa: E402  V26.1.9: batch 2


class PlaxisSolver(P1Mixin, P2Mixin):
    """Drives PLAXIS through its remote scripting server (Expert > Configure remote scripting server).
    Needs the `plxscripting` package that ships with PLAXIS, and this app running where it can reach PLAXIS
    (normally the same Windows PC). Each run opens the original model, applies the variables, calculates,
    reads the targets and saves the run as its own project file, so the original is never changed.

    Variable plaxis_ref formats:
        "Clay.cref"                 material or object property (materials found by name or Identification)
        "Phase_3.Deform.MaxSteps"   a property inside an object (dotted path)
        "Soillayer_2.Zones[0].Bottom"  soil layer level in borehole 1 (Soil mode, re-meshed)
        "Plate_1.d"                 any object in the model by name
        "LineLoad_1_1.qy_start@Phase_3"   a staged property in a given phase
        "Point_5.z+"                moves the point by the value (structures moved as a whole: "P1.x+; P2.x+")
        "Mesh.size"                 relative element size for meshing (e.g. 0.03 fine ... 0.1 coarse)
    Target result formats: FoS (SumMsf of the phase), Utot_max, Ux_max, Uy_max, Uz_max, or
        "<Group>.<Type>" e.g. "Plate.M11", "Soil.Utot" (max absolute value at nodes), or "file".
    """

    name = "plaxis"

    def __init__(self):
        from .settings import ensure_plxscripting_importable, load
        cfg = load()
        try:
            self.loaded_from = ensure_plxscripting_importable(cfg)
        except ImportError as e:
            raise SolverError(f"{e} Paste the folder that contains the 'plxscripting' folder into "
                              "'plxscripting folder' in PLAXIS connection.") from e
        self.host = cfg["host"]
        self.port = int(cfg["port"])
        self.password = cfg["password"]
        self._s = self._g = None
        self._base: dict[str, Path] = {}

    def test(self) -> str:
        """Connects and describes whatever project is open in PLAXIS Input right now."""
        try:
            s_i, g_i = self._connect()
        except Exception as e:  # noqa: BLE001
            raise SolverError(f"Could not reach the PLAXIS remote scripting server at {self.host}:{self.port}. "
                              f"Is it started (Expert > Configure remote scripting server) and is the password "
                              f"right? ({type(e).__name__}: {e})") from e
        return f"plxscripting loaded from: {self.loaded_from}\n" + self._describe_open(g_i)

    def mark_open_phases(self) -> int:
        """Marks every phase of the project open in PLAXIS Input for calculation (blue in the Phases explorer)."""
        s_i, g_i = self._connect()
        try:
            g_i.gotostages()
        except Exception:  # noqa: BLE001
            pass
        self._mark_phases(g_i)
        return len(list(g_i.Phases))

    def _describe_open(self, g_i) -> str:
        lines = []
        try:
            mats = [f"{m.Name.value} ({m.Identification.value})" for m in g_i.Materials]
            lines.append("Materials: " + ", ".join(mats))
        except Exception:  # noqa: BLE001
            pass
        for group in ("Plates", "EmbeddedBeams", "NodeToNodeAnchors", "Anchors", "Beams", "LineLoads",
                      "SurfaceLoads", "PointLoads"):
            try:
                objs = [o.Name.value for o in getattr(g_i, group)]
                if objs:
                    lines.append(f"{group}: " + ", ".join(objs))
            except Exception:  # noqa: BLE001
                pass
        try:
            pts = [f"{p.Name.value}({p.x.value:g},{p.y.value:g},{p.z.value:g})" for p in list(g_i.Points)[:80]]
            if pts:
                lines.append("Points (x,y,z): " + ", ".join(pts))
        except Exception:  # noqa: BLE001
            pass
        try:
            lines.append("Phases: " + ", ".join(f"{p.Name.value} ({p.Identification.value})" for p in g_i.Phases))
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(lines) or "Connected, but no project seems to be open in PLAXIS Input."

    def _base_model(self, files: list[dict], run_dir: Path) -> Path:
        """The model every run starts from. For 'currently open in PLAXIS', the open project is first saved
        once as a working copy next to the runs, so the user's original file is never touched."""
        src = files[0] if files else {}
        if src.get("source") != "open":
            path = model_file(files)
            if not path:
                raise SolverError("No PLAXIS project file (.p3d / .p2dx) among the input files")
            return path
        key = str(run_dir.parent)
        if key not in self._base:
            s_i, g_i = self._connect()
            ext = ".p3d" if getattr(s_i, "is_3d", True) else ".p2dx"
            base = (run_dir.parent / "working_copy" / ("base" + ext)).resolve()
            base.parent.mkdir(parents=True, exist_ok=True)
            g_i.save(str(base))
            if not base.exists():
                raise SolverError(f"PLAXIS could not save the working copy to {base}")
            self._base[key] = base
        return self._base[key]

    def read_model(self, files: list[dict], version: str = "3d", progress=lambda m: None, **connect_kw) -> dict:
        """The PLAXIS model's own geometry (points, lines, soils, structures, loads, materials, phases)."""
        from .plaxis_model import extract
        path = None
        if not files or files[0].get("source") != "open":
            path = model_file(files)
            if not path:
                raise SolverError("No PLAXIS project file (.p2dx / .p3d) among the input files")
            check_data_folder(path)
            version = "2d" if path.suffix.lower() in (".p2dx", ".p2d") else "3d"
        from .plaxis_launcher import connect
        self._s, self._g = connect(version, progress, **connect_kw)
        s_i, g_i = self._s, self._g
        if path:
            progress(f"Opening {path.name} in PLAXIS…")
            s_i.open(str(Path(path).resolve()))
        progress("Reading points, lines, soils, structures, loads, materials and phases…")
        return extract(s_i, g_i)

    def _connect(self, version: str = "3d"):
        """Connects to PLAXIS Input, starting it with its scripting server when it is not running."""
        if self._g is None:
            from .plaxis_launcher import connect
            self._s, self._g = connect(version)
            cfg = __import__("app.settings", fromlist=["load"]).load()
            self.host, self.port, self.password = cfg["host"], int(cfg["port"]), cfg["password"]
        return self._s, self._g

    def describe(self, files: list[dict]) -> str:
        s_i, g_i = self._connect()
        if files and files[0].get("source") == "open":
            return "PLAXIS model: project currently open in PLAXIS Input\n" + self._describe_open(g_i)
        path = model_file(files)
        if not path:
            return "No PLAXIS project file given."
        s_i.open(str(Path(path).resolve()))
        return f"PLAXIS model: {path.name}\n" + self._describe_open(g_i)

    def _find(self, g_i, name: str):
        """An object by name; dotted / indexed paths walk into it, e.g. "Phase_3.Deform" or "Soillayer_2.Zones[0]"."""
        import re as _re
        if "." in name or "[" in name:
            parts = _re.findall(r"[^.\[\]]+|\[\d+\]", name)
            obj = self._find(g_i, parts[0])
            for part in parts[1:]:
                try:
                    obj = list(obj)[int(part[1:-1])] if part.startswith("[") else getattr(obj, part)
                except Exception as e:  # noqa: BLE001
                    raise SolverError(f"'{name}' not found in the PLAXIS model ({part}: {e})") from e
            return obj
        try:
            return getattr(g_i, name)
        except Exception:  # noqa: BLE001
            for m in g_i.Materials:
                if name in (m.Name.value, m.Identification.value):
                    return m
        raise SolverError(f"Object '{name}' not found in the PLAXIS model")

    @staticmethod
    def _mark_phases(g_i) -> None:
        """PLAXIS calculates only the phases marked for calculation ("No phases have been marked for calculation"):
        a saved project has none marked once it was calculated, so every phase is marked again."""
        marked = 0
        for p in list(g_i.Phases):
            for how in (lambda: setattr(p, "ShouldCalculate", True), lambda: g_i.set(p.ShouldCalculate, True)):
                try:
                    how()
                    marked += 1
                    break
                except Exception:  # noqa: BLE001
                    continue
        if not marked:
            raise SolverError("Could not mark the phases for calculation (Phase.ShouldCalculate).")

    def _set_load(self, g_i, name: str, prop: str, value: float) -> None:
        """A load value (e.g. Surcharge.sigx). Load values are staged in PLAXIS: in Staged construction they belong
        to each phase, so "setproperties Surcharge sigx 22.5" fails there with "Unknown property". The value is
        set in Structures mode (the load's own value) and in every phase where the load (or its staged pieces,
        e.g. Surcharge_1) is active."""
        errors, done = [], 0
        _try(g_i, "gotostructures")
        try:
            self._find(g_i, name).setproperties(prop, value)
            done += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"Structures: {e}")
        _try(g_i, "gotostages")
        objs = []
        for group in ("SurfaceLoads", "LineLoads", "PointLoads"):
            try:
                objs += [o for o in getattr(g_i, group) if _obj_name(o) == name or _obj_name(o).startswith(name + "_")]
            except Exception:  # noqa: BLE001
                pass
        if not objs:
            try:
                objs = [self._find(g_i, name)]
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
        for o in objs:
            for ph in list(g_i.Phases)[1:]:
                try:
                    act = getattr(o, "Active")[ph]
                    if not bool(getattr(act, "value", act)):
                        continue
                except Exception:  # noqa: BLE001
                    pass
                try:
                    getattr(o, prop)[ph] = value
                    done += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{_obj_name(o)} in {_obj_name(ph)}: {e}")
        if not done:
            raise SolverError(f"Could not set {name}.{prop} = {value}: " + "; ".join(errors[:4]))

    def _reshape_load(self, g_i, name: str, edits: list, is3: bool) -> str:
        """A new area for a line / surface load (the surcharge): its corners are moved in PLAXIS Input, or, when this
        PLAXIS does not let them move, the load is drawn again on the new corners with its values and stages."""
        from .plaxis_model import _obj_points, reshape
        _try(g_i, "gotostructures")
        obj = self._find(g_i, name)
        parent = getattr(obj, "Parent", obj)
        old = _obj_points(parent, is3)
        if len(old) < 2:
            raise SolverError(f"The corners of {name} could not be read from PLAXIS, so its area cannot change.")
        new = reshape(old, edits)
        from .plaxis_model import area_of
        if area_of(new) < 1e-6:
            raise SolverError(f"The new area of {name} is zero (an edge was put on the opposite edge): change the values.")
        close = lambda a, b: all(abs(float(x) - float(y)) < 1e-4 for p, q in zip(a, b) for x, y in zip(p, q))
        errors = []
        # 1) the corners of the load are PLAXIS points (polygon points or the start / end points of a line)
        try:
            pts = list(getattr(parent, "Points", None) or [])
        except Exception:  # noqa: BLE001
            pts = []
        if not pts:
            pts = [q for q in (getattr(parent, "First", None), getattr(parent, "Second", None)) if q is not None]
        if len(pts) == len(old):
            try:
                for p, q in zip(pts, new):
                    for k, ax in enumerate("xyz"[:3 if is3 else 2]):
                        if abs(float(q[k]) - float(old[pts.index(p)][k])) > 1e-9:
                            p.setproperties(ax, float(q[k]))
                if close(_obj_points(parent, is3), new):
                    return f"{name}: corners moved to " + "; ".join(str([round(x, 3) for x in q[:3 if is3 else 2]]) for q in new)
                errors.append("the corners did not move")
            except Exception as e:  # noqa: BLE001
                errors.append(f"moving the corners: {e}")
        # 2) drawn again on the new corners, with the same values and the same stages
        try:
            kind = "surface" if is3 and len(new) > 2 else "line"
            props = {}
            for prop in LOAD_PROPS_ALL:
                try:
                    val = getattr(obj, prop)
                    props[prop] = float(getattr(val, "value", val))
                except Exception:  # noqa: BLE001
                    continue
            phases = list(g_i.Phases)
            active = {}
            _try(g_i, "gotostages")
            for ph in phases:
                try:
                    a = obj.Active[ph]
                    active[_obj_name(ph)] = bool(getattr(a, "value", a))
                except Exception:  # noqa: BLE001
                    pass
            _try(g_i, "gotostructures")
            coords = [tuple(float(x) for x in q[:3 if is3 else 2]) for q in new]
            made = g_i.surfload(*coords) if kind == "surface" else g_i.lineload(*coords[:2])
            made = made if isinstance(made, (list, tuple)) else [made]
            load = next((o for o in made if "load" in _obj_name(o).lower()), made[-1])
            for prop, val in props.items():
                try:
                    load.setproperties(prop, val)
                except Exception:  # noqa: BLE001
                    pass
            g_i.delete(parent)
            _try(g_i, "gotostages")
            for ph in phases:
                if active.get(_obj_name(ph)):
                    try:
                        g_i.activate(load, ph)
                    except Exception:  # noqa: BLE001
                        load.Active[ph] = True
                    for prop, val in props.items():
                        try:
                            getattr(load, prop)[ph] = val
                        except Exception:  # noqa: BLE001
                            pass
            _try(g_i, "gotostructures")
            return f"{name}: drawn again as {_obj_name(load)} on " + "; ".join(str([round(x, 3) for x in q]) for q in coords)
        except Exception as e:  # noqa: BLE001
            errors.append(f"drawing it again: {e}")
        raise SolverError(f"PLAXIS did not take the new area of {name} ({'; '.join(errors)}). Change the area in PLAXIS "
                          "Input, or shift the whole load instead (Move a whole structure, load or line).")

    def _remove(self, g_i, name: str) -> None:
        """A stage is deleted (when no later stage starts from it); a structure or load is deactivated in every
        stage, with its staged pieces (Anchor_1_1, ...)."""
        phases = list(g_i.Phases)
        ph = next((p for p in phases if _obj_name(p) == name), None)
        if ph is not None:
            later = [_obj_name(p) for p in phases if _obj_name(getattr(p, "PreviousPhase", None) or "") == name]
            if later:
                raise SolverError(f"Stage {name} cannot be removed: {', '.join(later)} start from it. Remove those first.")
            g_i.delete(ph)
            return
        done = 0
        for group in ("Plates", "NodeToNodeAnchors", "FixedEndAnchors", "Anchors", "EmbeddedBeams", "EmbeddedBeamRows",
                      "Geogrids", "Beams", "LineLoads", "SurfaceLoads", "PointLoads", "Interfaces", "PositiveInterfaces",
                      "NegativeInterfaces", "Wells", "Drains"):
            for o in list(getattr(g_i, group, []) or []):
                n = _obj_name(o)
                if n == name or n.startswith(name + "_"):
                    for p in phases[1:]:
                        try:
                            g_i.deactivate(o, p)
                        except Exception:  # noqa: BLE001
                            o.Active[p] = False
                    done += 1
        if not done:
            raise SolverError(f"{name} was not found in the PLAXIS model, so it could not be removed.")

    def _check_geometry(self, g_i, moved: dict) -> list[str]:
        """Reads every moved coordinate back from PLAXIS: the run stops if PLAXIS did not take the new value."""
        lines = []
        for (name, ax), (before, want) in moved.items():
            got = getattr(self._find(g_i, name), ax)
            got = float(getattr(got, "value", got))
            if abs(got - want) > 1e-4 * max(1.0, abs(want)):
                raise SolverError(f"PLAXIS did not take the new geometry: {name}.{ax} is {got:g}, expected {want:g} "
                                  f"(was {before:g}). Is the point used by a structure under another name?")
            lines.append(f"{name}.{ax}: {before:g} -> {got:g}")
        return lines

    def _export_plots(self, g_i, g_o, want: list[str], run_dir: Path, say) -> list[str]:
        """Saves the PLAXIS Output plots of the run as images (last calculation phase, not a safety phase):
        total / horizontal / vertical displacements, the mesh, the bending moment of the wall."""
        say("Saving the PLAXIS Output images", 93)
        d = run_dir / "plots"
        d.mkdir(parents=True, exist_ok=True)
        last = ""
        for ph in list(g_i.Phases):
            try:
                kind = str(getattr(ph, "DeformCalcType", "")).lower()
            except Exception:  # noqa: BLE001
                kind = ""
            try:
                ident = str(ph.Identification.value)
            except Exception:  # noqa: BLE001
                ident = ""
            if "safety" not in kind and "dynamic" not in kind and not ident.startswith("OPTIMUS"):
                last = _obj_name(ph)
        phase_o = self._phase(g_o, last)
        is3 = hasattr(g_o.ResultTypes.Soil, "Uz")
        rtypes = {"utot": ("Soil", "Utot"), "ux": ("Soil", "Ux"), "uy": ("Soil", "Uz" if is3 else "Uy"), "mesh": ("Soil", "Utot"),
                  "pexcess": ("Soil", "PExcess"), "plate_m": ("Plate", "M11" if is3 else "M2D"),
                  "plate_q": ("Plate", "Q13" if is3 else "Q2D"), "plate_n": ("Plate", "N1" if is3 else "Nx2D")}
        done = []
        plot = g_o.Plots[-1]
        for k in want:
            if k not in rtypes:
                continue
            group, rtype = rtypes[k]
            f = d / f"{k}.png"
            try:
                plot.Phase = phase_o
                plot.ResultType = _rtype(getattr(g_o.ResultTypes, group), rtype)
                try:
                    plot.DrawMesh = (k == "mesh")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    plot.export(str(f), 1600, 900)
                except Exception:  # noqa: BLE001
                    g_o.export(plot, str(f), 1600, 900)
                if f.is_file():
                    done.append(str(f))
            except Exception:  # noqa: BLE001 - e.g. no plate in the model for the moment
                continue
        return done

    def _output(self, g_i, phase_i):
        """PLAXIS Output for the run (QA V26.1.8). g_i.view() opens Output with its own scripting server, which is
        secured or not depending on PLAXIS' settings, whatever the Input server uses. A secured request sent to an
        unsecured Output server fails on every result with "Syntax of request could not be understood", so both
        ways are tried and the one that answers is kept."""
        from plxscripting.easy import new_server
        port = g_i.view(phase_i)
        errors = []
        for pw in dict.fromkeys([self.password or "", ""]):
            try:
                _, g_o = new_server(self.host, port, password=pw)
                list(g_o.Phases)  # a real request: fails at once when the security does not match
                return g_o
            except Exception as e:  # noqa: BLE001
                errors.append(f"{'secured' if pw else 'unsecured'}: {e}")
        raise SolverError(f"PLAXIS Output (port {port}) did not answer: " + "; ".join(errors))

    def export_more(self, run_file: Path, keys: list[str], run_dir: Path, say=lambda m, p=None: None) -> list[str]:
        """More PLAXIS Output images of a run already calculated: its saved project is opened in PLAXIS Input,
        then viewed in PLAXIS Output (best effort: an image PLAXIS cannot make is skipped)."""
        from plxscripting.easy import new_server
        s_i, g_i = self._connect("2d" if run_file.suffix.lower() in (".p2dx", ".p2d") else "3d")
        s_i.open(str(Path(run_file).resolve()))
        g_o = self._output(g_i, self._phase(g_i, ""))
        return self._export_plots(g_i, g_o, keys, run_dir, say)

    # V26.1.8: the soil model of a material as a variable. PLAXIS SoilModel codes; each list = the names this PLAXIS
    # may use for a parameter (the first one that takes the value is kept).
    SSC_NAMES = {"lambda": ("lambdaModified", "LambdaModified", "lambdastar"), "kappa": ("kappaModified", "KappaModified", "kappastar"),
                 "mu": ("muModified", "MuModified", "mustar")}

    def _soil_model(self, g_i, name: str, code: int, mu: float | None = None) -> str:
        """Switches a soil material to another constitutive model and fills the new model's parameters from the
        existing ones: MC E' = E50ref; HSsmall G0ref, γ0.7 kept or from Eur; SSC λ* = pref / Eoed,ref,
        κ* = 3 (1 − 2 νur) pref / Eur,ref, μ* = λ* / 25 (or the value given). Returns what was set."""
        _try(g_i, "gotosoil")
        mat = self._find(g_i, name)
        num = lambda prop, d=None: (lambda x: float(getattr(x, "value", x)))(getattr(mat, prop)) if hasattr(mat, prop) else d

        def get(prop, d=None):
            try:
                return num(prop, d)
            except Exception:  # noqa: BLE001
                return d
        pref, eoed, eur, e50, nuur = get("pRef", 100.0), get("EOedRef"), get("EURRef"), get("E50Ref"), get("nuUR", 0.2)
        first = lambda *names: next((x for x in (get(n) for n in names) if x is not None), None)
        sci, gsi = first("SigmaCi", "SigmaCI", "Sigmaci", "sigmaci"), first("GSI")
        sets, note = {}, ""
        if code == 2 and sci and gsi is not None:
            # V26.1.9 (P2): Mohr-Coulomb equivalent to Hoek-Brown (Hoek, Carranza-Torres & Corkum 2002), σ3max = σci / 4
            c, phi, info = hoek_brown_mc(sci, gsi, first("mi", "Mi") or 10.0, first("D", "Disturbance") or 0.0)
            erm = first("ERef", "Eref", "E", "Erm", "ERm")
            sets = {("cRef", "cref", "c"): c, ("phi", "Phi"): phi, ("psi", "Psi", "?"): 0.0}
            if erm:
                sets[("ERef", "Eref", "E", "?")] = erm
            note = f" ({info})"
        else:
            if not (eoed and eur):
                raise SolverError(f"{name}: Eoed,ref and Eur,ref are needed to derive the {code} parameters")
        if code == 2 and not sets:
            sets = {("ERef", "Eref", "E"): e50 or eoed, ("nu", "Nu", "?"): 0.3}  # "?" = optional
        elif code == 4:
            g0 = get("G0Ref") or 2.5 * eur / (2 * (1 + nuur))
            sets = {("G0Ref", "G0ref"): g0, ("gamma07",): get("gamma07") or 1.5e-4}
        elif code in (5, 6, 8):
            lam = pref / eoed
            # κ* from Eur is close to λ* for a stiff sand (Eur ≈ 3 Eoed), which made the K0 procedure fail in PLAXIS
            # (LogInfo -5, QA run 23 Nov); the usual SSC ratio κ*/λ* = 0.2 is used when it is smaller
            kap = min(3 * (1 - 2 * nuur) * pref / eur, 0.2 * lam)
            if code == 6:
                m_ = mu if mu is not None else lam / 25
                sets = {self.SSC_NAMES["lambda"]: lam, self.SSC_NAMES["kappa"]: kap, self.SSC_NAMES["mu"]: m_}
            elif code == 5:  # V26.1.9 (P2): Soft Soil, the same λ*, κ* without creep
                sets = {self.SSC_NAMES["lambda"]: lam, self.SSC_NAMES["kappa"]: kap}
            else:  # V26.1.9 (P2): Modified Cam-Clay: λ = λ* (1 + e0), κ = κ* (1 + e0), M from φ' (triaxial compression)
                e0 = first("eInit", "einit", "e0") or 1.0
                phi = first("phi", "Phi") or 25.0
                sp = math.sin(math.radians(phi))
                sets = {("lambda", "Lambda"): lam * (1 + e0), ("kappa", "Kappa"): kap * (1 + e0),
                        ("M", "MCC_M", "?"): 6 * sp / (3 - sp), ("nu", "Nu", "nuUR", "?"): min(max(nuur, 0.1), 0.35)}
        try:
            mat.setproperties("SoilModel", int(code))
        except Exception as e:  # noqa: BLE001
            raise SolverError(f"{name}: PLAXIS did not take soil model {code}: {e}")
        done = [f"SoilModel={code}"]
        for names, val in sets.items():
            for nm in names:
                try:
                    mat.setproperties(nm, float(val))
                    done.append(f"{nm}={float(val):.5g}")
                    break
                except Exception:  # noqa: BLE001
                    continue
            else:
                if "?" not in names:
                    raise SolverError(f"{name}: none of {', '.join(names)} could be set for soil model {code}")
        return f"{name}: " + ", ".join(done) + note

    LONGTERM_ID = "Long term"

    def _add_longterm(self, g_i, days: float) -> str:
        """V26.1.8: a consolidation phase of `days` after the last construction stage (not a safety phase), so the
        results can be read one year (or any time) after the works. Returns its name."""
        _try(g_i, "gotostages")
        base = [p for p in list(g_i.Phases) if not _is_safety(p)][-1]
        ph = g_i.phase(base)
        errors = []
        for how in (lambda: setattr(ph, "DeformCalcType", "consolidation"),
                    lambda: g_i.set(ph.DeformCalcType, "Consolidation")):
            try:
                how()
                break
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
        else:
            raise SolverError("Could not make the long-term phase a consolidation phase: " + "; ".join(errors[:2]))
        for prop, val in (("TimeInterval", float(days)), ("Identification", f"{self.LONGTERM_ID} ({days:g} days)")):
            try:
                setattr(ph, prop, val)
            except Exception:  # noqa: BLE001
                try:
                    g_i.set(getattr(ph, prop), val)
                except Exception as e:  # noqa: BLE001
                    if prop == "TimeInterval":
                        raise SolverError(f"Could not set the time interval of the long-term phase: {e}")
        return _obj_name(ph)

    def _phase(self, g, name: str):
        phases = list(g.Phases)
        from .features import DYNAMIC_ID, DYNAMIC_PHASE, SAFETY_ID, SAFETY_PHASE
        if name.startswith(SAFETY_PHASE) or name == DYNAMIC_PHASE:  # V26.1.9: a phase OPTIMUS added in this run
            want = f"{SAFETY_ID} {name[len(SAFETY_PHASE):]}" if name.startswith(SAFETY_PHASE) else DYNAMIC_ID
            for p in reversed(phases):
                try:
                    if str(p.Identification.value).startswith(want):
                        return p
                except Exception:  # noqa: BLE001
                    continue
            raise SolverError(f"not in this run: no phase '{want}'")
        if name == "@longterm":  # the long-term phase added by _add_longterm (V26.1.8)
            for p in reversed(phases):
                try:
                    if str(p.Identification.value).startswith(self.LONGTERM_ID):
                        return p
                except Exception:  # noqa: BLE001
                    continue
            raise SolverError("not in this run: no long-term phase")
        if not name:  # the last stage of the works (not the long-term phase added for time results)
            for p in reversed(phases):
                try:
                    ident = str(p.Identification.value)
                    if ident.startswith(self.LONGTERM_ID) or ident.startswith(DYNAMIC_ID) or ident.startswith(SAFETY_ID):
                        continue
                except Exception:  # noqa: BLE001
                    pass
                return p
            return phases[-1]
        for p in phases:
            if name in (p.Name.value, p.Identification.value):
                return p
        raise SolverError(f"Phase '{name}' not found")

    def run(self, files, inputs: dict, variables: list[Variable], targets: list[Target], run_dir: Path) -> dict:
        from plxscripting.easy import new_server

        run_dir = run_dir.resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        say = getattr(self, "progress", None) or (lambda msg, pct=None: None)  # what the run is doing (and %), shown on the page
        path = Path(self._base_model(files, run_dir)).resolve()
        say("Connecting to PLAXIS Input", 5)
        s_i, g_i = self._connect("2d" if path.suffix.lower() in (".p2dx", ".p2d") else "3d")
        say(f"Opening {path.name} in PLAXIS", 12)
        s_i.open(str(path))
        say("Applying the run's values", 25)

        geometry_changed, mesh_size, removals = False, None, []
        areas: dict[str, list] = {}  # load name: edits of its area (edges / corners)
        moved: dict[tuple[str, str], list] = {}  # (point, axis): [before, expected], checked after all are set
        soil_models = []  # V26.1.8: what each soil model switch set, kept with the run
        # V26.1.9: features applied after the variables (water levels and conditions per stage, interfaces with their
        # own material, design approach, new structures, the dynamic load)
        levels, conds, ifaces, design, adds, dyn, staged_done = {}, [], {}, 0, {}, dict(self.dynamic or {}), []
        p2 = new_p2()  # V26.1.9 (batch 2): slopes, staged fill / excavation, mesh refinement, phase settings
        is3 = path.suffix.lower() in (".p3d", ".p3dxml")
        first = lambda v: 0 if any(r.strip().lower().endswith((".soilmodel", ".sscmu", ".stiffnessfactor")) for r in (v.plaxis_ref or "").split(";")) else 1
        for v in sorted(variables, key=first):  # a soil model is switched before the other parameters of the run
            if v.name not in inputs:
                continue
            if not v.plaxis_ref:
                raise SolverError(f"{v.name} has no PLAXIS reference (e.g. Clay.cref or Point_7.z)")
            value = numeric(inputs[v.name])
            if value is None:
                raise SolverError(f"{v.name}: '{inputs[v.name]}' is not a number. Use 'label: number'.")
            for one_ref in [r.strip() for r in v.plaxis_ref.split(";") if r.strip()]:
                ref, _, phase = one_ref.partition("@")
                factor, base = 1.0, 0.0
                sm = SHIFT_REF.match(ref)
                if sm:  # "Point_5.z+": move by the value; "Point_9.x+*0.8~6": move by 0.8 * (value - 6)
                    obj_name, prop, shift = sm.group(1), sm.group(2), True
                    factor = float(sm.group(3)) if sm.group(3) else 1.0
                    base = float(sm.group(4)) if sm.group(4) else 0.0
                else:
                    obj_name, _, prop = ref.rpartition(".")
                    shift = False
                ae = area_edit(prop, value)
                if ae:  # "SurfaceLoad_1.area:xmax" / ".corner3:y" / ".areasize1": the surcharge area, applied below
                    areas.setdefault(obj_name, []).append(ae)
                    geometry_changed = True
                    continue
                if prop.lower() == "soilmodel":  # V26.1.8: the material's constitutive model
                    soil_models.append(self._soil_model(g_i, obj_name, int(round(value))))
                    continue
                if prop.lower() == "sscmu":  # V26.1.8: Soft Soil Creep with this creep index μ*
                    soil_models.append(self._soil_model(g_i, obj_name, 6, mu=float(value)))
                    continue
                if prop.lower() == "stiffnessfactor":  # V26.1.8: E50, Eoed, Eur, G0 (or E') scaled together
                    _try(g_i, "gotosoil")
                    mat, done = self._find(g_i, obj_name), []
                    for sp in ("E50Ref", "EOedRef", "EURRef", "G0Ref", "ERef", "Eref", "E"):
                        try:
                            now = getattr(mat, sp)
                            now = float(getattr(now, "value", now))
                        except Exception:  # noqa: BLE001
                            continue
                        if now:
                            mat.setproperties(sp, now * float(value))
                            done.append(f"{sp} {now:g} → {now * float(value):g}")
                    if not done:
                        raise SolverError(f"{obj_name}: no stiffness parameter to scale")
                    soil_models.append(f"{obj_name}: stiffness × {float(value):g}: " + ", ".join(done))
                    continue
                pl = prop.lower()
                if pl == "drainagetype":  # V26.1.9: Drained, Undrained A / B / C, Non-porous
                    soil_models.append(self._drainage(g_i, obj_name, int(round(value))))
                    continue
                if pl == "porepresscalctype":  # V26.1.9: phreatic or steady-state groundwater flow
                    staged_done.append(self._pore_type(g_i, obj_name, int(round(value))))
                    continue
                if obj_name == "WaterLevel" and pl == "level" and phase:  # V26.1.9: water level per stage
                    levels[phase] = value
                    continue
                if pl == "watercondition" and phase:  # V26.1.9: dry / interpolate clusters in a stage
                    conds.append((obj_name, phase, int(round(value))))
                    continue
                if pl.startswith("iface"):  # V26.1.9: the interface's own Rinter / virtual thickness
                    ifaces.setdefault(obj_name, {})[prop[5:]] = value
                    continue
                if self._p2_take(p2, obj_name, prop, phase, value):  # V26.1.9 (batch 2)
                    continue
                if obj_name == "Design" and pl == "approach":  # V26.1.9: EC7 design approach
                    design = int(round(value))
                    continue
                if obj_name == "Dynamic":  # V26.1.9: amplitude / frequency / duration of the dynamic phase
                    dyn[pl] = value
                    continue
                if obj_name in (self.additions or {}) and (pl == "add" or pl.startswith("shift")):  # V26.1.9
                    a = adds.setdefault(obj_name, {"on": False, "shift": 0.0})
                    if pl == "add":
                        a["on"] = value >= 0.5
                    else:
                        a["shift"] = value
                    continue
                if phase and prop in ("PrestressForce", "Qwell", "Q", "Discharge", "h", "Head", "Cref"):  # V26.1.9
                    staged_done.append(self._set_staged(g_i, obj_name, prop, phase, value))
                    continue
                if obj_name.lower() == "mesh":  # "Mesh.size": relative element size used when meshing
                    mesh_size = value
                    continue
                if prop.lower() == "remove":  # "Anchor_1.remove" = 1: deleted / deactivated in this run
                    if value >= 0.5:
                        removals.append(obj_name)
                        if obj_name.lower().startswith("point"):
                            _try(g_i, "gotostructures")
                            g_i.delete(self._find(g_i, obj_name))
                            geometry_changed = True
                    continue
                root = obj_name.split(".")[0].split("[")[0].lower()
                if root.startswith(("soillayer", "borehole")) and not phase:  # soil layers / heads: Soil mode
                    _try(g_i, "gotosoil")
                    geometry_changed = True
                elif prop.lower() in ("x", "y", "z") and not phase:
                    if not geometry_changed:
                        _try(g_i, "gotostructures")
                    geometry_changed = True
                elif phase or root.startswith(("phase", "initialphase")):
                    _try(g_i, "gotostages")
                obj = self._find(g_i, obj_name)
                if phase:
                    getattr(obj, prop)[self._phase(g_i, phase)] = value
                elif shift:
                    now = getattr(obj, prop)
                    now = float(getattr(now, "value", now))
                    obj.setproperties(prop, now + factor * (value - base))
                    if prop.lower() in ("x", "y", "z"):
                        moved[(obj_name, prop)] = [moved.get((obj_name, prop), [now])[0], now + factor * (value - base)]
                elif prop.lower() in LOAD_PROPS_ALL:
                    self._set_load(g_i, obj_name, prop, value)
                else:
                    if prop.lower() in ("x", "y", "z") and not phase:
                        now = getattr(obj, prop)
                        moved[(obj_name, prop)] = [moved.get((obj_name, prop), [float(getattr(now, "value", now))])[0], value]
                    obj.setproperties(prop, value)

        for name, eds in areas.items():
            say(f"Changing the area of {name}", 28)
            applied_area = self._reshape_load(g_i, name, eds, path.suffix.lower() == ".p3d")
            (run_dir / f"area_{name}.txt").write_text(applied_area, encoding="utf-8")
        effects = 1.0
        if design:  # V26.1.9: factored strengths and variable loads (after the other values of the run)
            done, effects = self._design_approach(g_i, design)
            soil_models += done
        if ifaces:
            say("Interfaces with their own properties", 29)
            soil_models += self._interfaces(g_i, ifaces)
        made = []
        specs = [{**self.additions[n], "shift": a["shift"]} for n, a in adds.items() if a["on"]]
        if specs:
            say(f"Drawing {len(specs)} new structure(s)", 30)
            made = self._add_structures(g_i, specs, is3)
            geometry_changed = True
        p2_done, remesh = self._p2_geometry(g_i, p2, is3, moved)
        soil_models += p2_done
        geometry_changed = geometry_changed or remesh
        applied = self._check_geometry(g_i, moved)
        (run_dir / "geometry_applied.txt").write_text("\n".join(applied) or "no geometry change", encoding="utf-8")
        if geometry_changed or mesh_size is not None:  # new geometry or mesh size: re-mesh before calculating
            say("Meshing", 32)
            _try(g_i, "gotomesh")
            g_i.mesh(mesh_size) if mesh_size is not None else g_i.mesh()
        g_i.gotostages()
        for name in removals:
            if not name.lower().startswith("point"):
                self._remove(g_i, name)
        if made:
            staged_done += self._activate_added(g_i, made)
        if levels:
            staged_done += self._water_levels(g_i, levels, is3)
        if conds:
            staged_done += self._cluster_conditions(g_i, conds)
        staged_done += self._p2_stages(g_i, p2)
        if getattr(self, "longterm", 0):
            say(f"Adding the long-term phase ({self.longterm:g} days)", 38)
            self._add_longterm(g_i, self.longterm)
        if self.safety_after:  # V26.1.9: a safety phase after each chosen stage (FoS per stage)
            say(f"Adding {len(self.safety_after)} safety phase(s)", 38)
            staged_done += self._add_safety(g_i, self.safety_after)
        if self.dynamic:  # V26.1.9: the dynamic phase of the study
            say("Adding the dynamic phase", 39)
            staged_done += self._add_dynamic(g_i, {**self.dynamic, **dyn}, is3)
        if getattr(self, "gravity_initial", False):
            # V26.1.8: with Soft Soil Creep the K0 procedure of the initial phase fails in PLAXIS (LogInfo -5, QA
            # diagnosis on UA-22, any κ*); gravity loading calculates the initial stresses. Every run of such a study
            # (Run 0 included) uses it, so the runs stay comparable.
            ph0 = list(g_i.Phases)[0]
            for how in (lambda: setattr(ph0, "DeformCalcType", "gravity loading"),
                        lambda: g_i.set(ph0.DeformCalcType, "Gravity loading")):
                try:
                    how()
                    break
                except Exception:  # noqa: BLE001
                    continue
            else:
                raise SolverError("Could not set the initial phase to gravity loading (needed for Soft Soil Creep).")
            soil_models.append("InitialPhase: gravity loading (K0 procedure fails with Soft Soil Creep)")
        self._mark_phases(g_i)
        say(f"Calculating every phase in PLAXIS ({len(list(g_i.Phases))} phases); a 3D model can take a long time", 40)
        g_i.calculate()
        say("Checking the phases and saving the run", 85)
        for p in g_i.Phases:
            info = str(getattr(getattr(p, "LogInfo", None), "value", "0") or "0")
            if info not in ("0", ""):
                raise SolverError(f"Phase {p.Name.value} failed (LogInfo {info})")

        run_file = run_dir / (path.stem + "_" + run_dir.name + path.suffix)
        g_i.save(str(run_file))

        out, g_o = {"_geometry": applied} if applied else {}, None
        if soil_models:
            out["_soil_models"] = soil_models
            (run_dir / "soil_models.txt").write_text("\n".join(soil_models), encoding="utf-8")
        if staged_done:  # V26.1.9: what was set in the stages (prestress, water, new structures, added phases)
            out["_stages_applied"] = staged_done
            (run_dir / "stages_applied.txt").write_text("\n".join(staged_done), encoding="utf-8")
        say("Reading the results in PLAXIS Output", 90)
        def read(t):  # one result of the run (the first read opens PLAXIS Output)
            nonlocal g_o
            key = t.result.strip()
            if key.lower() == "file":
                return str(run_file)
            phase_i = self._phase(g_i, t.phase)
            if key.lower() in ("fos", "summsf", "msf"):
                # QA V26.1.8: ΣMsf only exists in a safety phase; the last plastic phase gave "FoS = 1" for a model
                # without any safety phase. Now a safety phase is required (or the result is "not in this model").
                if not _is_safety(phase_i):
                    phase_i = next((p for p in reversed(list(g_i.Phases)) if _is_safety(p)), None)
                    if phase_i is None:
                        raise SolverError("not in this model: no safety (strength reduction) phase")
                return float(phase_i.Reached.SumMsf.value)
            if g_o is None:
                g_o = self._output(g_i, phase_i)
            phase_o = self._phase(g_o, t.phase)
            key, _, stat = key.partition("#")  # "#min" / "#avg": the minimum / average instead of the maximum
            key, _, where = key.partition("|")
            key = key.strip()
            group, _, rtype = key.partition(".")
            if not rtype:  # Utot_max -> Soil.Utot
                group, rtype = "Soil", key.split("_")[0]
            try:
                rgroup = getattr(g_o.ResultTypes, group)
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"not in this model: no {group} results ({e})")
            w = where.strip().lower()
            if w.startswith("at "):  # V26.1.9: the value at a chosen point
                return self._at_point(g_o, phase_o, _rtype(rgroup, rtype), where.strip()[3:], is3)
            if w.startswith("trough"):  # V26.1.9: settlement trough volume / volume loss
                return self._trough(g_o, phase_o, rgroup, _rtype(rgroup, rtype), w, is3)
            if w.startswith("profile"):  # V26.1.9 (batch 2): a curve along the surface / the wall, kept with the run
                pts = self._profile(g_o, phase_o, rgroup, _rtype(rgroup, rtype), w, is3)
                out.setdefault("_profiles", {})[t.name] = [[round(a, 4), round(b, 6)] for a, b in pts]
                return max(abs(b) for _, b in pts)
            res = g_o.getresults(phase_o, _rtype(rgroup, rtype), "node")
            try:
                values = [float(x) for x in res]
            except (TypeError, ValueError):  # e.g. anchors not yet active in this stage: PLAXIS answers with a text
                raise SolverError(f"not in this model at this stage: no active {group} ({str(res)[:60]})")
            if not values:
                raise SolverError(f"not in this model at this stage: no active {group}")
            if where.strip():
                coord, op, lim = _parse_filter(where)
                coords = [float(x) for x in g_o.getresults(phase_o, getattr(rgroup, coord), "node")]
                if lim == "max":
                    top = max(coords)
                    keep = [v for v, c in zip(values, coords) if abs(c - top) < 1e-3]
                elif lim == "min":
                    low = min(coords)
                    keep = [v for v, c in zip(values, coords) if abs(c - low) < 1e-3]
                else:
                    keep = [v for v, c in zip(values, coords) if _cmp(c, op, lim)]
                if not keep:
                    raise SolverError(f"{t.name}: no nodes match '{where.strip()}'")
                values = keep
            mags = [abs(v) for v in values]
            return min(mags) if stat == "min" else (sum(mags) / len(mags) if stat == "avg" else max(mags))


        missing = []
        for t in targets:
            for attempt in range(2):
                try:
                    out[t.name] = read(t)
                    if effects != 1.0 and isinstance(out[t.name], float) and self.is_force(t.result):
                        out[t.name] *= effects  # V26.1.9: DA1-C1 / DA2*: design value of the structural forces
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 0 and "no active project" in str(e).lower():
                        g_o = None  # PLAXIS Output lost its project (seen after a failed request): open it again
                        continue
                    if t.operator != "report":
                        raise
                    out[t.name] = None  # a result this model does not have (no plates, no anchors...): reported as "—"
                    missing.append(f"{t.label}: {e}")
                    break
        if missing:
            out["_missing"] = missing
        want = getattr(self, "want_plots", None) or []
        if want:
            try:
                if g_o is None:
                    g_o = self._output(g_i, self._phase(g_i, ""))
                out["_plots"] = self._export_plots(g_i, g_o, want, run_dir, say)
            except Exception as e:  # noqa: BLE001
                out.setdefault("_missing", []).append(f"PLAXIS Output images: {e}")
        if getattr(self, "want_model", False):  # the full run of the uploaded model: read its geometry while it is open
            try:
                from .plaxis_model import extract
                say("Reading the model (geometry, materials, phases)", 96)
                self.model_read = extract(s_i, g_i)
            except Exception:  # noqa: BLE001 - the study page can still read it from PLAXIS
                self.model_read = None
        return out


# QA V26.1.8: PLAXIS 3D 2024 names some result types differently from the names the app used (Plate.N1 is
# Plate.N11, NodeToNodeAnchor.AnchorForce3D is NodeToNodeAnchor.N): every name has its alternatives, the first one
# this PLAXIS has is read.
RESULT_ALIASES = {"N1": ["N11", "N_1", "N1"], "N11": ["N11", "N1"], "N2": ["N22", "N2"],
                  "AnchorForce3D": ["AnchorForce3D", "N", "AnchorForce"], "AnchorForce2D": ["AnchorForce2D", "Nx2D", "N"],
                  "M11": ["M11", "M_11"], "Q13": ["Q13", "Q_13"], "Q12": ["Q12", "Q_12"],
                  # V26.1.9: structure, interface, flow and dynamic results
                  "Nx2D": ["Nx2D", "N"], "Tskin": ["Tskin", "TSkin", "Tskin3D"], "Tskin2D": ["Tskin2D", "Tskin", "TSkin"],
                  "M2": ["M2", "M_2"], "M3": ["M3", "M_3"],
                  "InterfaceEffectiveNormalStress": ["InterfaceEffectiveNormalStress", "SigmaNE", "EffectiveNormalStress", "SigN"],
                  "InterfaceShearStress": ["InterfaceShearStress", "TauRel", "Tau", "ShearStress"],
                  "InterfaceRelativeShearStress": ["InterfaceRelativeShearStress", "RelativeShearStress", "TauRelative"],
                  "Qtot": ["Qtot", "QTot", "qTot", "Q", "Qabs"], "GroundwaterHead": ["GroundwaterHead", "Head", "GWHead"],
                  "PSteady": ["PSteady", "PSteadyState", "SteadyStatePorePressure"], "PActive": ["PActive", "PActivePorePressure"],
                  "Ax": ["Ax", "AccelerationX"], "Vx": ["Vx", "VelocityX"]}


def _rtype(rgroup, name: str):
    for n in RESULT_ALIASES.get(name, [name]):
        try:
            return getattr(rgroup, n)
        except Exception:  # noqa: BLE001
            continue
    raise SolverError(f"not in this PLAXIS: result {name} (tried {', '.join(RESULT_ALIASES.get(name, [name]))})")


def _is_safety(phase) -> bool:
    for attr in ("DeformCalcType", "CalculationType"):
        try:
            v = getattr(phase, attr)
            if "safety" in str(getattr(v, "value", v)).lower():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def deterministic(err: str) -> bool:
    """A PLAXIS error that the same inputs will give again (an invalid material, a property PLAXIS does not know, a
    geometry PLAXIS refused): retrying it only repeats a 10-minute calculation (QA V26.1.8)."""
    e = (err or "").lower()
    return any(k in e for k in ("invalid material", "unknown property", "is not a number", "did not take the new geometry",
                                "not found", "no plaxis reference", "overlap", "is invalid", "not in this model", "soil model", "failed (loginfo",
                                # V26.1.9: a feature this PLAXIS refuses gives the same answer again
                                "drainage type", "did not draw", "dynamic", "interface material", "safety phase",
                                "pore pressure calculation", "water level", "water condition", "could not set", "exist in plaxis 3d only"))


def _try(g_i, mode: str) -> None:
    try:
        getattr(g_i, mode)()
    except Exception:  # noqa: BLE001 - mode switch not needed / not available in this PLAXIS version
        pass


def _parse_filter(where: str):
    import re
    m = re.match(r"\s*([XYZxyz])\s*(>=|<=|>|<|=)\s*(max|min|-?[\d.]+)\s*$", where)
    if not m:
        raise SolverError(f"Location filter '{where.strip()}' not understood. Use e.g. 'Z >= 10.5' or 'Z = max'.")
    lim = m.group(3)
    return m.group(1).upper(), m.group(2), lim if lim in ("max", "min") else float(lim)


def _cmp(c: float, op: str, lim: float) -> bool:
    return {">": c > lim, ">=": c >= lim - 1e-6, "<": c < lim, "<=": c <= lim + 1e-6,
            "=": abs(c - lim) < 1e-3}[op]


def get_solver(name: str):
    if name == "plaxis":
        return PlaxisSolver()
    return SimulatedSolver()
