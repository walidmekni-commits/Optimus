"""V26.1.9 (batch 2): the PLAXIS commands of the P2 features of the QA/QC "Report for Missing Features". Mixed into
app.solvers.PlaxisSolver.

Variable plaxis_ref formats added in batch 2:
    "Point_8.x+*0.894~13.4; Point_8.y+*-0.447~13.4"   length of an anchor / pile / cable: its free end moves along it
    "Point_7|Point_8.slopeangle"                     slope angle (°) of an inclined line: the top moves horizontally,
                                                     the toe and the height stay
    "Point_7.y+~5; Point_7.x+*1.5~5"                 height of a slope with its angle kept
    "Soil_3_1.PlacedIn" / "Soil_3_1.ExcavatedIn"     the stage (1, 2 …) a cluster is placed / excavated in (0 = as in
                                                     the model): staged fill and excavation sequence
    "Plate_1.CoarsenessFactor"                       local mesh refinement around a structure
    "LineDisplacement_1.displ:ux_start"              a prescribed displacement value (structures and every stage)
    "InitialPhase.InitialCalcType"                   0 K0 procedure, 1 Gravity loading, 2 Field stress (3D)
    "Phase_3.CalcType"                               0 Plastic, 1 Consolidation, 2 Fully coupled flow-deformation
    "Phase_3.ThermalCalcType"                        0 Ignore temperature … 3 Steady state thermal flow
    "Loads.factor@Phase_3"                           every active load × factor in that stage (load scenarios)
    "Precipitation.q@Phase_3"                        rainfall (precipitation) in that stage, m/day
"""
import math
import re

from .plaxis_p1 import _name, _set, _set_names, _staged_set, _try, _val

# the PLAXIS names, then the PLAXIS number (QA V26.1.9 D1: 1 K0 procedure, 4 Plastic, 7 Safety in PLAXIS 3D 2024.3)
INITIAL_NAMES = {0: ("K0 procedure", "K0Procedure", 1), 1: ("Gravity loading", "GravityLoading", 2),
                 2: ("Field stress", "FieldStress", 3)}
CALC_NAMES = {0: ("Plastic", 4), 1: ("Consolidation", 5), 2: ("Fully coupled flow-deformation", "Flow-deformation coupled",
                                                              "FullyCoupledFlowDeformation", 6)}
THERMAL_NAMES = {0: ("Ignore temperature", "IgnoreTemperature"), 1: ("Earth gradient", "EarthGradient"),
                 2: ("Use temperatures from previous phase", "UseTemperaturesFromPreviousPhase"),
                 3: ("Steady state thermal flow", "SteadyStateThermalFlow")}
DISPL_GROUPS = ("LineDisplacements", "SurfaceDisplacements", "PointDisplacements")
LOAD_GROUPS = ("LineLoads", "SurfaceLoads", "PointLoads")
LOAD_VALUES = ("qx_start", "qy_start", "qz_start", "qx_end", "qy_end", "qz_end", "sigx", "sigy", "sigz", "sigz_ref",
               "Fx", "Fy", "Fz", "fx", "fy", "fz", "qx", "qy", "qz")


def new_p2() -> dict:
    """What the P2 variables of a run collect, applied after the other values (see P2Mixin._p2_take)."""
    return {"slopes": [], "clusters": [], "coarse": {}, "displ": [], "init": None, "calc": [], "thermal": [],
            "lfactor": {}, "rain": {}}


class P2Mixin:
    """PLAXIS commands of the batch 2 features (the solver keeps g_i; errors are SolverError)."""

    def _p2_take(self, st: dict, obj_name: str, prop: str, phase: str, value: float) -> bool:
        """Keeps a P2 value for later (True), or leaves it to the usual setting (False)."""
        pl = prop.lower()
        if pl == "slopeangle":
            st["slopes"].append((obj_name, value))
        elif pl in ("placedin", "excavatedin"):
            if int(round(value)) > 0:
                st["clusters"].append((obj_name, pl, int(round(value))))
        elif pl == "coarsenessfactor":
            st["coarse"][obj_name] = value
        elif pl.startswith("displ:"):
            st["displ"].append((obj_name, prop[6:], value))
        elif pl == "initialcalctype":
            st["init"] = int(round(value))
        elif pl == "calctype":
            st["calc"].append((obj_name, int(round(value))))
        elif pl == "thermalcalctype":
            st["thermal"].append((obj_name, int(round(value))))
        elif obj_name == "Loads" and pl == "factor" and phase:
            st["lfactor"][phase] = value
        elif obj_name == "Precipitation" and pl == "q" and phase:
            st["rain"][phase] = value
        else:
            return False
        return True

    # --- before meshing ---------------------------------------------------------------------------------------
    def _p2_geometry(self, g_i, st: dict, is3: bool, moved: dict) -> tuple[list[str], bool]:
        """Slope angles (the top point moves horizontally) and coarseness factors. Returns (what was set, remesh)."""
        from .solvers import SolverError
        done, remesh = [], False
        up = "z" if is3 else "y"
        for key, ang in st["slopes"]:
            top_n, _, toe_n = key.partition("|")
            if not 1 <= ang <= 89:
                raise SolverError(f"Slope angle {ang:g}° of {top_n}–{toe_n}: give an angle between 1° and 89°")
            _try(g_i, "gotostructures")
            top, toe = self._find(g_i, top_n), self._find(g_i, toe_n)
            xt, xo = float(_val(top.x)), float(_val(toe.x))
            h = float(_val(getattr(top, up))) - float(_val(getattr(toe, up)))
            sx = 1.0 if xt >= xo else -1.0
            new = xo + sx * abs(h) / math.tan(math.radians(ang))
            _set(g_i, top, "x", new)
            moved[(top_n, "x")] = [moved.get((top_n, "x"), [xt])[0], new]
            done.append(f"{top_n}: slope {ang:g}° (height {abs(h):g} m kept), x {xt:g} → {new:g}")
        for name, f in st["coarse"].items():
            if not f or f <= 0:
                raise SolverError(f"{name}: the coarseness factor must be above 0")
            _try(g_i, "gotomesh")
            obj, ok, errors = self._find(g_i, name), False, []
            for o in (obj, getattr(obj, "Parent", None)):
                if o is None:
                    continue
                try:
                    _set(g_i, o, "CoarsenessFactor", float(f))
                    ok = True
                    break
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
            if not ok:
                raise SolverError(f"Could not set the coarseness factor of {name}: " + "; ".join(errors[:2]))
            done.append(f"{name}: coarseness factor {f:g}")
            remesh = True
        for name, prop, value in st["displ"]:
            done.append(self._set_displ(g_i, name, prop, value))
        return done, remesh or bool(st["slopes"])

    def _set_displ(self, g_i, name: str, prop: str, value: float) -> str:
        """A prescribed displacement value: in Structures mode and in every stage where it is active."""
        from .solvers import SolverError
        n, errors = 0, []
        _try(g_i, "gotostructures")
        try:
            _set(g_i, self._find(g_i, name), prop, value)
            n += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"Structures: {e}")
        _try(g_i, "gotostages")
        objs = []
        for group in DISPL_GROUPS:
            try:
                objs += [o for o in getattr(g_i, group) if _name(o) == name or _name(o).startswith(name + "_")]
            except Exception:  # noqa: BLE001
                pass
        for o in objs:
            for ph in list(g_i.Phases)[1:]:
                try:
                    if not bool(_val(o.Active[ph])):
                        continue
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _staged_set(o, prop, ph, value, g_i)
                    n += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
        if not n:
            raise SolverError(f"Could not set {name}.{prop} = {value}: " + "; ".join(errors[:3]))
        return f"{name}: {prop} = {value:g} m"

    # --- in Staged construction --------------------------------------------------------------------------------
    def _works(self, g_i) -> list:
        """The construction stages (the initial phase, safety phases and the phases OPTIMUS adds left out)."""
        from .features import DYNAMIC_ID, SAFETY_ID
        from .solvers import _is_safety
        out = []
        for p in list(g_i.Phases)[1:]:
            try:
                ident = str(_val(p.Identification))
            except Exception:  # noqa: BLE001
                ident = ""
            if _is_safety(p) or ident.startswith((SAFETY_ID, DYNAMIC_ID, self.LONGTERM_ID)):
                continue
            out.append(p)
        return out

    def _p2_stages(self, g_i, st: dict) -> list[str]:
        """Clusters placed / excavated in a chosen stage, calculation types, initial stresses, thermal, load factors
        and rainfall per stage."""
        from .solvers import SolverError
        done = []
        if not any(st[k] for k in ("clusters", "calc", "thermal", "lfactor", "rain")) and st["init"] is None:
            return done
        _try(g_i, "gotostages")
        works = self._works(g_i)
        soils = list(getattr(g_i, "Soils", []) or [])
        for cl, how, k in st["clusters"]:
            if k > len(works):
                raise SolverError(f"{cl}: stage {k} does not exist (the model has {len(works)} construction stages)")
            objs = [o for o in soils if _name(o) == cl] or [self._find(g_i, cl)]
            ph0 = list(g_i.Phases)[0]
            for o in objs:
                for j, ph in enumerate(works, 1):
                    on = j >= k if how == "placedin" else j < k
                    self._activate(g_i, o, ph, on)
                if how == "placedin":
                    self._activate(g_i, o, ph0, False)
            done.append(f"{cl}: {'placed' if how == 'placedin' else 'excavated'} in {_name(works[k - 1])}")
        if st["init"] is not None:
            ph0 = list(g_i.Phases)[0]
            try:
                v = _set_names(g_i, ph0, "DeformCalcType", st["init"], INITIAL_NAMES, raw=False)
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"Could not set the initial stress calculation: {e}")
            done.append(f"{_name(ph0)}: {v}")
        for name, code in st["calc"]:
            ph = self._phase(g_i, name)
            try:
                v = _set_names(g_i, ph, "DeformCalcType", code, CALC_NAMES, raw=False)
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"Could not set the calculation type of {name}: {e}")
            done.append(f"{name}: {v}")
        for name, code in st["thermal"]:
            ph = self._phase(g_i, name)
            try:
                v = _set_names(g_i, ph, "ThermalCalcType", code, THERMAL_NAMES)
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"Could not set the thermal calculation of {name} (PLAXIS Thermal needed, with the "
                                  f"thermal option on in the model properties): {e}")
            done.append(f"{name}: thermal {v}")
        for phase, f in st["lfactor"].items():
            done.append(self._load_factor(g_i, phase, f))
        for phase, q in st["rain"].items():
            done.append(self._rain(g_i, phase, q))
        return done

    def _activate(self, g_i, obj, ph, on: bool) -> None:
        errors = []
        for how in ((lambda: g_i.activate(obj, ph)) if on else (lambda: g_i.deactivate(obj, ph)),
                    lambda: obj.Active.__setitem__(ph, on), lambda: g_i.set(obj.Active, ph, on)):
            try:
                how()
                return
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
        from .solvers import SolverError
        raise SolverError(f"Could not {'activate' if on else 'deactivate'} {_name(obj)} in {_name(ph)}: " + "; ".join(errors[:2]))

    def _load_factor(self, g_i, phase: str, f: float) -> str:
        """Every load active in the stage × f (its values in that stage only)."""
        from .solvers import SolverError
        ph = self._phase(g_i, phase)
        n = 0
        for group in LOAD_GROUPS:
            for o in list(getattr(g_i, group, []) or []):
                try:
                    if not bool(_val(o.Active[ph])):
                        continue
                except Exception:  # noqa: BLE001
                    continue
                for prop in LOAD_VALUES:
                    try:
                        now = float(_val(getattr(o, prop)[ph]))
                    except Exception:  # noqa: BLE001
                        continue
                    if now:
                        _staged_set(o, prop, ph, now * f, g_i)
                        n += 1
        if not n:
            raise SolverError(f"Load factor in {phase}: no active load value in that stage")
        return f"{phase}: every active load × {f:g} ({n} values)"

    def _rain(self, g_i, phase: str, q: float) -> str:
        """Rainfall (precipitation) q in m/day in that stage (a flow boundary condition of the model)."""
        from .solvers import SolverError
        ph = self._phase(g_i, phase)
        pr = None
        for nm in ("Precipitation", "precipitation"):
            pr = getattr(g_i, nm, None)
            if pr is not None:
                break
        if pr is None:
            try:
                pr = g_i.ModelConditions.Precipitation
            except Exception:  # noqa: BLE001
                raise SolverError("Rainfall: this PLAXIS has no precipitation model condition")
        try:
            self._activate(g_i, pr, ph, q != 0)
        except Exception:  # noqa: BLE001
            pass
        errors = []
        for prop in ("q", "Q", "Discharge"):
            try:
                _staged_set(pr, prop, ph, float(q), g_i)  # sign to check on PLAXIS (QA/QC)
                return f"{phase}: rainfall {abs(q):g} m/day"
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))
        raise SolverError(f"Could not set the rainfall in {phase}: " + "; ".join(errors[:2]))

    # --- results ----------------------------------------------------------------------------------------------
    @staticmethod
    def _profile(g_o, phase_o, rgroup, rtype, where: str, is3: bool) -> list[tuple[float, float]]:
        """A result along a line: "profile:surface" the ground surface (x, value; 3D: the section at mid y),
        "profile:wall" the plates (level, value). The value with its sign, as PLAXIS gives it."""
        from .solvers import SolverError
        vals = [float(x) for x in g_o.getresults(phase_o, rtype, "node")]
        if not vals:
            raise SolverError("not in this model at this stage: no nodes for the profile")
        xs = [float(x) for x in g_o.getresults(phase_o, rgroup.X, "node")]
        up = [float(x) for x in g_o.getresults(phase_o, rgroup.Z if is3 else rgroup.Y, "node")]
        what = re.sub(r"^\s*profile\s*:?\s*", "", where).strip() or "surface"
        if what == "wall":
            if is3:  # the plate section at mid y
                ys = [float(x) for x in g_o.getresults(phase_o, rgroup.Y, "node")]
                y0 = (min(ys) + max(ys)) / 2
                tol = max(0.02 * (max(ys) - min(ys)), 0.25)
                idx = [i for i, y in enumerate(ys) if abs(y - y0) <= tol] or list(range(len(ys)))
            else:
                idx = list(range(len(vals)))
            pts = {}
            for i in idx:  # the largest |value| at each level (several plates / both faces)
                k = round(up[i], 3)
                if k not in pts or abs(vals[i]) > abs(pts[k]):
                    pts[k] = vals[i]
            return sorted(pts.items(), reverse=True)
        top = max(up)
        idx = [i for i, u in enumerate(up) if abs(u - top) < 1e-3]
        if is3 and idx:
            ys = [float(x) for x in g_o.getresults(phase_o, rgroup.Y, "node")]
            y_all = [ys[i] for i in idx]
            y0 = (min(y_all) + max(y_all)) / 2
            tol = max(0.02 * (max(y_all) - min(y_all)), 0.25)
            idx = [i for i in idx if abs(ys[i] - y0) <= tol] or idx
        pts = sorted({round(xs[i], 3): vals[i] for i in idx}.items())
        if len(pts) < 2:
            raise SolverError("not in this model: too few surface nodes for the profile")
        return pts
