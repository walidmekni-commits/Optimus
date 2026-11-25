"""V26.1.9: the PLAXIS commands of the P1 features of the QA/QC "Report for Missing Features" (excavation and wall
design like UA-22). Mixed into app.solvers.PlaxisSolver.

Variable plaxis_ref formats added in V26.1.9:
    "Clay.DrainageType"                         drainage type of a soil material (0 Drained … 4 Non-porous)
    "Phase_3.PorePresCalcType"                  0 Phreatic, 1 Steady-state groundwater flow, 2 from previous phase
    "WaterLevel.level@Phase_3"                  a horizontal phreatic level over the whole model in that stage
    "Soil_2_1.WaterCondition@Phase_3"           cluster water condition: 0 Global level, 1 Dry, 2 Interpolate
    "NodeToNodeAnchor_1.PrestressForce@Phase_3" prestress (Adjust prestress is switched on in that stage)
    "Well_1.Qwell@Phase_2", "Drain_1.h@Phase_2", "SurfaceContraction_1.Cref@Phase_4"   staged values
    "PositiveInterface_1|Clay.ifaceRinter"      the interface's own Rinter / DeltaInter: a custom interface material
                                                (a copy of its present material, or of Clay, the adjacent soil)
    "Design.approach"                           0 none, 1 DA1-C1, 2 DA1-C2, 3 DA2*, 4 DA3 (EC7 partial factors)
    "New_N1.add", "New_N1.shiftz"               a new structure of the study (features.added) and its level
    "Dynamic.amplitude / .frequency / .duration"   the dynamic phase of the study (features.dynamic)
Target result formats added:  "Soil.Utot|at 12;0;-3"  (value at a point),  "Soil.Uy|trough"  (settlement trough
volume Vs, per metre),  "Soil.Uy|trough:8"  (volume loss VL in % for a tunnel of diameter 8), phases "@safety:Phase_3"
(the safety phase added after Phase_3) and "@dynamic".
"""
import math
import re

DRAINAGE_NAMES = {0: ("Drained",), 1: ("Undrained (A)", "Undrained A", "UndrainedA"),
                  2: ("Undrained (B)", "Undrained B", "UndrainedB"), 3: ("Undrained (C)", "Undrained C", "UndrainedC"),
                  4: ("Non-porous", "NonPorous", "Non porous")}
# QA V26.1.9 D5: PLAXIS 3D 2024.3 refused "Steady-state groundwater flow"; its own token is tried first, in the
# spellings of the PLAXIS 2D / 3D command references, then the lower case forms
PORE_NAMES = {0: ("Phreatic", "phreatic"),
              1: ("Steady state groundwater flow", "steady state groundwater flow", "SteadyStateGroundwaterFlow",
                  "Steady-state groundwater flow", "Steady state flow"),
              2: ("Use pressures from previous phase", "use pressures from previous phase", "UsePressuresFromPreviousPhase")}
CLUSTER_NAMES = {0: ("Global level", "GlobalLevel"), 1: ("Dry",), 2: ("Interpolate",)}
# EC7 partial factors applied by OPTIMUS (variable actions γQ, soil strength γφ' on tan φ', γc', γcu, and the
# factor on the effects of actions for the structural forces). DA2* and DA1-C1 factor the effects (1.35), the
# variable load by 1.5 / 1.35; DA1-C2 and DA3 factor the variable load by 1.3 and the strength by M2.
DESIGN = {1: ("EC7 DA1 Combination 1", 1.5 / 1.35, 1.0, 1.0, 1.0, 1.35), 2: ("EC7 DA1 Combination 2", 1.3, 1.25, 1.25, 1.4, 1.0),
          3: ("EC7 DA2*", 1.5 / 1.35, 1.0, 1.0, 1.0, 1.35), 4: ("EC7 DA3", 1.3, 1.25, 1.25, 1.4, 1.0)}
FORCE_GROUPS = ("plate", "nodetonodeanchor", "fixedendanchor", "embeddedbeam", "embeddedbeamrow", "beam", "geogrid")
STRUCT_GROUPS = ("Plates", "NodeToNodeAnchors", "FixedEndAnchors", "EmbeddedBeams", "EmbeddedBeamRows", "Geogrids", "Beams",
                 "Wells", "Drains", "LineContractions", "SurfaceContractions", "Contractions", "PositiveInterfaces",
                 "NegativeInterfaces", "Interfaces", "LineLoads", "SurfaceLoads", "PointLoads")
PHI = ("phi", "Phi", "phiRef")
COH = ("cref", "cRef", "CRef", "c")
SU = ("SuRef", "suRef", "Su", "su", "Cu")
PSI = ("psi", "Psi")


def _val(x):
    return getattr(x, "value", x)


def _name(o) -> str:
    try:
        return str(_val(o.Name))
    except Exception:  # noqa: BLE001
        return str(o)


def _set(g_i, obj, prop: str, value) -> None:
    """obj.prop = value, whatever way this PLAXIS takes it."""
    errors = []
    for how in (lambda: obj.setproperties(prop, value), lambda: setattr(obj, prop, value),
                lambda: g_i.set(getattr(obj, prop), value)):
        try:
            how()
            return
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError(f"{_name(obj)}.{prop}: " + "; ".join(dict.fromkeys(errors))[:300])


def _set_names(g_i, obj, prop: str, code: int, names: dict, raw: bool = True) -> str:
    """An enumerated property: the PLAXIS name(s) of the code, then the code itself (raw=False: only the names /
    PLAXIS numbers listed, e.g. the calculation type whose numbers are not OPTIMUS' codes)."""
    errors = []
    for v in list(names.get(code, ())) + ([code] if raw else []):
        try:
            _set(g_i, obj, prop, v)
            return str(v)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError("; ".join(errors[:2]))


def _staged_set(obj, prop: str, phase, value, g_i=None) -> None:
    errors = []
    for how in (lambda: getattr(obj, prop).__setitem__(phase, value), lambda: g_i.set(getattr(obj, prop), phase, value)):
        try:
            how()
            return
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    raise RuntimeError("; ".join(errors))


def _try(g_i, mode: str) -> None:
    try:
        getattr(g_i, mode)()
    except Exception:  # noqa: BLE001
        pass


class P1Mixin:
    """PLAXIS commands of the V26.1.9 features (the solver keeps PLAXIS' g_i; errors are SolverError)."""

    safety_after: list = []
    additions: dict = {}
    dynamic: dict | None = None

    # --- staged values ----------------------------------------------------------------------------------------
    def _pieces(self, g_i, name: str) -> list:
        """The object and its staged pieces (Anchor_1_1, Anchor_1_2...) in Staged construction."""
        out = []
        for group in STRUCT_GROUPS:
            try:
                out += [o for o in getattr(g_i, group) if _name(o) == name or _name(o).startswith(name + "_")]
            except Exception:  # noqa: BLE001
                continue
        if not out:
            try:
                out = [getattr(g_i, name)]
            except Exception:  # noqa: BLE001
                pass
        return list({id(o): o for o in out}.values())

    def _set_staged(self, g_i, name: str, prop: str, phase_name: str, value: float) -> str:
        """A value of one stage (prestress, well discharge, drain head, contraction): on the object or its pieces.
        A prestress switches 'Adjust prestress' on in that stage."""
        from .solvers import SolverError
        _try(g_i, "gotostages")
        ph = self._phase(g_i, phase_name)
        objs, done, errors = self._pieces(g_i, name), 0, []
        for o in objs:
            try:
                if prop == "PrestressForce":
                    try:
                        _staged_set(o, "AdjustPrestress", ph, True, g_i)
                    except Exception:  # noqa: BLE001 - older PLAXIS: the prestress is taken as given
                        pass
                _staged_set(o, prop, ph, float(value), g_i)
                done += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{_name(o)}: {e}")
        if not done:
            raise SolverError(f"Could not set {name}.{prop} in {phase_name} = {value:g}: " +
                              ("; ".join(errors[:3]) or "not found in the PLAXIS model"))
        return f"{name}.{prop} in {phase_name} = {value:g}"

    # --- soil material and phase settings -----------------------------------------------------------------------
    def _drainage(self, g_i, name: str, code: int) -> str:
        from .solvers import SolverError
        _try(g_i, "gotosoil")
        mat = self._find(g_i, name)
        try:
            used = _set_names(g_i, mat, "DrainageType", int(code), DRAINAGE_NAMES)
        except Exception as e:  # noqa: BLE001
            raise SolverError(f"{name}: PLAXIS did not take drainage type {code}: {e}")
        return f"{name}: drainage type {used}"

    def _pore_type(self, g_i, phase_name: str, code: int) -> str:
        from .solvers import SolverError
        _try(g_i, "gotostages")
        ph = self._phase(g_i, phase_name)
        try:
            used = _set_names(g_i, ph, "PorePresCalcType", int(code), PORE_NAMES, raw=False)
        except Exception as e:  # noqa: BLE001
            want = PORE_NAMES.get(int(code), (str(code),))[0]
            hint = (" Steady-state groundwater flow needs the PLAXIS groundwater flow option of this licence; PLAXIS "
                    "refused every spelling of it, so this licence (or this phase) may not allow it.") if int(code) == 1 else ""
            raise SolverError(f"{phase_name}: PLAXIS did not take the pore pressure calculation '{want}'.{hint} PLAXIS: {e}")
        return f"{phase_name}: pore pressures by {used}"

    # --- water level per stage and cluster water conditions ------------------------------------------------------
    def _extent(self, g_i, is3: bool):
        xs, ys, zs = [], [], []
        _try(g_i, "gotostructures")
        for p in list(getattr(g_i, "Points", []) or []):
            try:
                xs.append(float(_val(p.x))), ys.append(float(_val(p.y)))
                if is3:
                    zs.append(float(_val(p.z)))
            except Exception:  # noqa: BLE001
                continue
        try:  # the soil contour gives the model's real extents
            c = g_i.SoilContour
            for attr, lst in (("xmin", xs), ("xmax", xs), ("ymin", ys), ("ymax", ys)):
                v = getattr(c, attr, None)
                if v is not None:
                    lst.append(float(_val(v)))
        except Exception:  # noqa: BLE001
            pass
        if not xs:
            return None
        return min(xs), max(xs), min(ys), max(ys)

    def _water_levels(self, g_i, levels: dict, is3: bool) -> list[str]:
        """Each stage's phreatic level: a horizontal user water level drawn over the whole model (Flow conditions)
        and set as the global water level of that stage."""
        from .solvers import SolverError
        if not levels:
            return []
        ext = self._extent(g_i, is3)
        if not ext:
            raise SolverError("The extents of the model could not be read, so no water level can be drawn.")
        x0, x1, y0, y1 = ext
        made = {}
        _try(g_i, "gotoflow")
        for phase_name, lev in levels.items():
            lev = float(lev)
            if lev not in made:
                pts = ([(x0, y0, lev), (x1, y0, lev), (x1, y1, lev), (x0, y1, lev)] if is3 else [(x0, lev), (x1, lev)])
                try:
                    r = g_i.waterlevel(*pts)
                except Exception as e:  # noqa: BLE001
                    raise SolverError(f"PLAXIS did not draw the water level at {lev:g} m: {e}")
                r = r if isinstance(r, (list, tuple)) else [r]
                made[lev] = next((o for o in reversed(r) if "waterlevel" in _name(o).lower()), r[-1])
        _try(g_i, "gotostages")
        done = []
        for phase_name, lev in levels.items():
            ph, wl = self._phase(g_i, phase_name), made[float(lev)]
            errors = []
            for how in (lambda: g_i.setglobalwaterlevel(wl, ph), lambda: _staged_set(g_i.WaterLevels, "GlobalWaterLevel", ph, wl, g_i),
                        lambda: _set(g_i, ph, "GlobalWaterLevel", wl)):
                try:
                    how()
                    break
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
            else:
                raise SolverError(f"Could not make the level {lev:g} m the water level of {phase_name}: " + "; ".join(errors[:2]))
            done.append(f"{phase_name}: phreatic level {float(lev):g} m ({_name(wl)})")
        return done

    def _cluster_conditions(self, g_i, conds: list) -> list[str]:
        """The water condition of a soil cluster in a stage: Global level, Dry (e.g. the clusters left inside a
        dewatered excavation) or Interpolate."""
        from .solvers import SolverError
        _try(g_i, "gotostages")
        done = []
        for cluster, phase_name, code in conds:
            ph = self._phase(g_i, phase_name)
            objs = [o for o in list(getattr(g_i, "Soils", []) or []) if _name(o) == cluster]
            if not objs:
                try:
                    objs = [getattr(g_i, cluster)]
                except Exception:  # noqa: BLE001
                    raise SolverError(f"Soil cluster {cluster} not found in Staged construction")
            ok, errors = False, []
            for o in objs:
                wc = getattr(o, "WaterConditions", None)
                for v in CLUSTER_NAMES.get(int(code), ()):
                    try:
                        _staged_set(wc, "Conditions", ph, v, g_i)
                        ok = True
                        break
                    except Exception as e:  # noqa: BLE001
                        errors.append(str(e))
            if not ok:
                raise SolverError(f"Could not set the water condition of {cluster} in {phase_name}: " + "; ".join(errors[:2]))
            done.append(f"{cluster} in {phase_name}: {CLUSTER_NAMES[int(code)][0]}")
        return done

    # --- interfaces ---------------------------------------------------------------------------------------------
    def _iface_copy(self, g_i, name: str, src, props: dict):
        """A copy of a soil material with the interface's own values (Rinter used: interface strength Manual)."""
        from .solvers import SolverError
        from .plaxis_model import _numeric_attrs
        _try(g_i, "gotosoil")
        try:
            new = g_i.soilmat()
            new = new[-1] if isinstance(new, (list, tuple)) else new
        except Exception as e:  # noqa: BLE001
            raise SolverError(f"{name}: PLAXIS did not create the interface material: {e}")
        for prop in ("SoilModel", "DrainageType"):
            try:
                _set(g_i, new, prop, _val(getattr(src, prop)))
            except Exception:  # noqa: BLE001
                pass
        for prop, val in _numeric_attrs(src).items():
            try:
                new.setproperties(prop, val)
            except Exception:  # noqa: BLE001
                continue
        self._iface_manual(g_i, new, name, props)
        try:
            _set(g_i, new, "Identification", f"{name} · {_val(getattr(src, 'Identification', '')) or _name(src)} (OPTIMUS)")
        except Exception:  # noqa: BLE001
            pass
        return new

    @staticmethod
    def _iface_manual(g_i, mat, name: str, props: dict) -> None:
        from .solvers import SolverError
        for prop in ("InterfaceStrengthDetermination", "InterfaceStrength"):  # Manual: Rinter is used (not Rigid)
            for v in ("Manual", 1):
                try:
                    _set(g_i, mat, prop, v)
                    break
                except Exception:  # noqa: BLE001
                    continue
        for prop, val in props.items():
            try:
                mat.setproperties(prop, float(val))
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"{name}: PLAXIS did not take {prop} = {float(val):g} for the interface material: {e}")

    def _iface_assign(self, g_i, name: str, mat, pieces=None) -> None:
        """The custom material of the interface (Structures mode) and of its pieces in every stage."""
        from .solvers import SolverError
        if pieces is None:
            _try(g_i, "gotostructures")
            itf = self._find(g_i, name)
            try:
                _set(g_i, itf, "MaterialMode", "Custom")
            except Exception:  # noqa: BLE001
                pass
            try:
                _set(g_i, itf, "Material", mat)
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"{name}: PLAXIS did not take the custom interface material: {e}")
        _try(g_i, "gotostages")
        for o in pieces if pieces is not None else self._pieces(g_i, name):
            for ph in list(g_i.Phases):
                for prop, val in (("MaterialMode", "Custom"), ("Material", mat)):
                    try:
                        _staged_set(o, prop, ph, val, g_i)
                    except Exception:  # noqa: BLE001
                        pass

    def _adjacent_layers(self, g_i, itf) -> list[tuple]:
        """QA V26.1.9 E1: the soil layers the interface runs along, top to bottom: [(material, name, top, bottom)]
        (first borehole, at the interface's levels read in model coordinates)."""
        from .plaxis_model import _obj_points, _surface_points, read_boreholes
        readings, is3 = [], False
        for o in (getattr(itf, "Parent", None), itf):
            if o is None:
                continue
            for flag in (True, False):
                try:
                    pts = _obj_points(o, flag)
                except Exception:  # noqa: BLE001
                    pts = []
                if pts:
                    is3 = flag and any(len(q) > 2 for q in pts)
                    try:
                        readings += [c for c in _surface_points(itf, o, True) if c] if is3 else []
                    except Exception:  # noqa: BLE001
                        pass
                    readings.append(pts)
                    break
            if readings:
                break
        if not readings:
            return []
        vi = 2 if is3 else 1
        _try(g_i, "gotosoil")
        try:
            holes = read_boreholes(g_i, is3)
        except Exception:  # noqa: BLE001
            holes = []
        lays = sorted(holes[0]["layers"], key=lambda x: -x["top"]) if holes else []
        for pts in readings:  # the reading that lies in the ground (N1)
            lv = [float(q[vi]) for q in pts if len(q) > vi]
            if not lv:
                continue
            lo, hi = min(lv), max(lv)
            mid = (lo + hi) / 2
            if not any(l_["bottom"] - 1e-9 <= mid <= l_["top"] + 1e-9 for l_ in lays):
                continue
            out = []
            for lay in lays:
                if min(hi, lay["top"]) - max(lo, lay["bottom"]) > 1e-6 and lay.get("material"):
                    try:
                        out.append((self._find(g_i, lay["material"]), lay["material"], lay["top"], lay["bottom"]))
                    except Exception:  # noqa: BLE001
                        continue
            if out:
                return out
        return []

    def _interfaces(self, g_i, edits: dict) -> list[str]:
        """The interface's own Rinter / virtual thickness. A custom interface material: a copy of it with the new values.
        'From adjacent soil' (QA V26.1.9 E1): along one layer, a copy of that layer with the new values; along several
        layers, the new values are set on the adjacent soil materials themselves (the interface stays 'from adjacent
        soil', each part with its own layer; no soil copies, which PLAXIS 24.3 could not stage)."""
        from .solvers import SolverError
        done = []
        for key, props in edits.items():
            name, _, base = key.partition("|")
            bases = [b for b in base.split(",") if b]
            vals = ", ".join(f"{k} = {float(v):g}" for k, v in props.items())
            _try(g_i, "gotostructures")
            itf = self._find(g_i, name)
            src = None
            try:
                if "custom" in str(_val(itf.MaterialMode)).lower():
                    src = _val(itf.Material)
            except Exception:  # noqa: BLE001
                pass
            if src is not None:  # its own material: one copy
                new = self._iface_copy(g_i, name, src, props)
                self._iface_assign(g_i, name, new)
                done.append(f"{name}: own material {_name(new)}, a copy of {_name(src)} ({vals})")
                continue
            layers = self._adjacent_layers(g_i, itf)
            if not layers and bases:
                for b in bases:
                    try:
                        layers.append((self._find(g_i, b), b, None, None))
                    except Exception:  # noqa: BLE001
                        continue
            if not layers:
                raise SolverError(f"{name}: no material to copy for its own interface properties: it takes its properties "
                                  "from the adjacent soil, and no soil layer of the first borehole was found at its level. "
                                  "Read the model again (Read from PLAXIS) so that its place is read in model coordinates.")
            if len(layers) == 1:
                mat, lname = layers[0][0], layers[0][1]
                new = self._iface_copy(g_i, name, mat, props)
                self._iface_assign(g_i, name, new)
                done.append(f"{name}: own material {_name(new)}, a copy of the adjacent soil {lname} ({vals})")
                continue
            # several layers: the values are set on the adjacent soil materials themselves, the interface stays
            # 'from adjacent soil' and each part keeps its own layer. Re-test of d5016d6: one soil copy per layer set on
            # the pieces made PLAXIS 24.3 fail at gotostages (ShearStrengthDepthDependency sub-objects of the copies)
            _try(g_i, "gotosoil")
            for lay in layers:
                self._iface_manual(g_i, lay[0], name, props)
            done.append(f"{name}: 'from adjacent soil' kept, {vals} set on each adjacent layer ("
                        + ", ".join(lay[1] for lay in layers) + "); other interfaces next to these layers take it too")
        return done

    # --- design approach (EC7 partial factors) ---------------------------------------------------------------------
    def _design_approach(self, g_i, code: int) -> tuple[list[str], float]:
        """Factored soil strength (tan φ', c', su) and variable loads of the design approach; returns what was done and
        the factor on the effects of actions for the structural forces."""
        from .solvers import LOAD_PROPS_ALL, SolverError
        from .plaxis_model import _kind_of_material
        if code not in DESIGN:
            return [], 1.0
        name, gq, gphi, gc, gcu, eff = DESIGN[code]
        done = [f"{name}: variable loads × {gq:.3g}, tan φ' / {gphi:g}, c' / {gc:g}, su / {gcu:g}, structural forces × {eff:g}"]
        if gphi != 1 or gc != 1 or gcu != 1:
            _try(g_i, "gotosoil")
            for mat in list(g_i.Materials):
                if _kind_of_material(mat) != "soil":
                    continue

                def get(names):
                    for n in names:
                        try:
                            return n, float(_val(getattr(mat, n)))
                        except Exception:  # noqa: BLE001
                            continue
                    return None, None
                parts = []
                n, phi = get(PHI)
                if n and phi and phi > 0:
                    phid = math.degrees(math.atan(math.tan(math.radians(phi)) / gphi))
                    mat.setproperties(n, phid)
                    parts.append(f"φ' {phi:g} → {phid:.3g}")
                    pn, psi = get(PSI)
                    if pn and psi and psi > 0:  # ψ' reduced like φ' (ψd = ψk − (φk − φd)), never below 0
                        mat.setproperties(pn, max(0.0, psi - (phi - phid)))
                n, c = get(COH)
                if n and c and c > 0:
                    mat.setproperties(n, c / gc)
                    parts.append(f"c' {c:g} → {c / gc:.3g}")
                n, su = get(SU)
                if n and su and su > 0:
                    mat.setproperties(n, su / gcu)
                    parts.append(f"su {su:g} → {su / gcu:.3g}")
                if parts:
                    done.append(f"{_name(mat)}: " + ", ".join(parts))
        if gq != 1:
            _try(g_i, "gotostages")
            n = 0
            for group in ("LineLoads", "SurfaceLoads", "PointLoads"):
                for o in list(getattr(g_i, group, []) or []):
                    for prop in dir(o):
                        if prop.lower() not in LOAD_PROPS_ALL:
                            continue
                        for ph in list(g_i.Phases)[1:]:
                            try:
                                v = float(_val(getattr(o, prop)[ph]))
                            except Exception:  # noqa: BLE001
                                continue
                            if v:
                                try:
                                    getattr(o, prop)[ph] = v * gq
                                    n += 1
                                except Exception:  # noqa: BLE001
                                    continue
            if not n and any(list(getattr(g_i, g, []) or []) for g in ("LineLoads", "SurfaceLoads", "PointLoads")):
                raise SolverError(f"{name}: the variable loads could not be factored in the stages")
            done.append(f"{n} staged load value(s) × {gq:.3g}")
        return done, eff

    # --- new structures ---------------------------------------------------------------------------------------
    def _add_structures(self, g_i, specs: list[dict], is3: bool) -> list:
        """Draws each new structure from its two points (Structures mode) with its material. Returns
        [(spec, object)] to activate in the stages once meshed."""
        from .solvers import SolverError
        out = []
        _try(g_i, "gotostructures")
        for sp in specs:
            a, b = [float(x) for x in sp["a"]], [float(x) for x in sp["b"]]
            k = 2 if is3 else 1
            a[k] += sp.get("shift", 0.0)
            b[k] += sp.get("shift", 0.0)
            co = lambda p: tuple(p[:3] if is3 else p[:2])
            kind = sp["kind"]
            try:
                if kind == "plate" and is3:  # a wall panel: the two points and a width along y
                    w = float(sp.get("width") or 1.0)
                    c, d = [b[0], b[1] + w, b[2]], [a[0], a[1] + w, a[2]]
                    try:
                        made = g_i.plate(co(a), co(b), co(c), co(d))
                    except Exception:  # noqa: BLE001
                        surf = g_i.surface(co(a), co(b), co(c), co(d))
                        made = g_i.plate(surf[-1] if isinstance(surf, (list, tuple)) else surf)
                else:
                    cmd = {"plate": "plate", "anchor": "n2nanchor", "geogrid": "geogrid", "beam": "beam",
                           "embedded_beam": "embeddedbeam" if is3 else "embeddedbeamrow"}[kind]
                    if kind == "beam" and not is3:
                        raise SolverError("Beams exist in PLAXIS 3D only: draw a plate or an anchor in 2D")
                    made = getattr(g_i, cmd)(co(a), co(b))
            except SolverError:
                raise
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"PLAXIS did not draw the new {kind} {sp['name']} from {co(a)} to {co(b)}: {e}")
            made = list(made) if isinstance(made, (list, tuple)) else [made]
            want = {"plate": "plate", "anchor": "anchor", "geogrid": "geogrid", "beam": "beam", "embedded_beam": "embedded"}[kind]
            obj = next((o for o in reversed(made) if want in _name(o).lower()), None)
            if obj is None:
                raise SolverError(f"PLAXIS drew {', '.join(_name(o) for o in made)} but no {kind} for {sp['name']}")
            if sp.get("material"):
                try:
                    _set(g_i, obj, "Material", self._find(g_i, sp["material"]))
                except Exception as e:  # noqa: BLE001
                    raise SolverError(f"{sp['name']}: PLAXIS did not take the material {sp['material']}: {e}")
            out.append((sp, _name(obj)))
        return out

    def _activate_added(self, g_i, made: list) -> list[str]:
        from .solvers import SolverError
        _try(g_i, "gotostages")
        phases = list(g_i.Phases)
        names = [_name(p) for p in phases]
        done = []
        for sp, name in made:
            start = names.index(sp["phase"]) if sp.get("phase") in names else 1
            later = self._descendants(g_i, phases, phases[start])
            objs = self._pieces(g_i, name)
            if not objs:
                raise SolverError(f"{name} (new {sp['kind']}) was not found in Staged construction")
            for o in objs:
                for ph in later:
                    try:
                        g_i.activate(o, ph)
                    except Exception:  # noqa: BLE001
                        try:
                            o.Active[ph] = True
                        except Exception as e:  # noqa: BLE001
                            raise SolverError(f"Could not activate {_name(o)} in {_name(ph)}: {e}")
                if sp["kind"] == "anchor" and sp.get("prestress"):
                    try:
                        _staged_set(o, "AdjustPrestress", phases[start], True, g_i)
                        _staged_set(o, "PrestressForce", phases[start], float(sp["prestress"]), g_i)
                    except Exception as e:  # noqa: BLE001
                        raise SolverError(f"Could not prestress {_name(o)} in {_name(phases[start])}: {e}")
            done.append(f"{sp['name']}: {sp['kind']} {name} active from {names[start]}"
                        + (f", prestress {sp['prestress']:g} kN" if sp.get("prestress") else ""))
        return done

    @staticmethod
    def _descendants(g_i, phases: list, start) -> list:
        """The stage and every stage that follows from it (its children in the phase tree, and theirs)."""
        names = {_name(p): p for p in phases}
        parent = {}
        for p in phases:
            try:
                parent[_name(p)] = _name(_val(p.PreviousPhase))
            except Exception:  # noqa: BLE001
                parent[_name(p)] = None
        keep = []
        for p in phases:
            n = _name(p)
            seen = set()
            while n and n not in seen:
                if n == _name(start):
                    keep.append(p)
                    break
                seen.add(n)
                n = parent.get(n)
        if len(keep) <= 1 and not any(parent.values()):  # no tree readable: every phase after it in the list
            i = phases.index(start)
            keep = phases[i:]
        return [p for p in keep if p in names.values()]

    # --- added phases: safety after chosen stages, dynamic -----------------------------------------------------
    def _add_safety(self, g_i, after: list[str]) -> list[str]:
        from .features import SAFETY_ID
        from .solvers import SolverError
        _try(g_i, "gotostages")
        done = []
        for base_name in after:
            base = self._phase(g_i, base_name)
            ph = g_i.phase(base)
            ph = ph[-1] if isinstance(ph, (list, tuple)) else ph
            try:
                _set_names(g_i, ph, "DeformCalcType", 0, {0: ("Safety", "safety", 7)}, raw=False)
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"Could not make a safety phase after {base_name}: {e}")
            try:
                _set(g_i, ph, "Identification", f"{SAFETY_ID} {base_name}")
            except Exception:  # noqa: BLE001
                pass
            for prop in ("Deform.ResetDisplacementsToZero", "Deform.UseDefaultIterationParams"):
                try:
                    obj, attr = ph.Deform, prop.split(".")[1]
                    _set(g_i, obj, attr, True)
                except Exception:  # noqa: BLE001
                    pass
            done.append(f"Safety phase {_name(ph)} after {base_name}")
        return done

    def _add_dynamic(self, g_i, d: dict, is3: bool) -> list[str]:
        """A dynamic phase after the chosen stage: a harmonic load multiplier on the chosen load, or a harmonic
        prescribed displacement of the model base (earthquake-like shaking); viscous boundaries."""
        from .features import DYNAMIC_ID
        from .solvers import SolverError
        amp, freq, dur = float(d.get("amplitude") or 0.0), float(d.get("frequency") or 1.0), float(d.get("duration") or 1.0)
        base_shaking = (d.get("source") or "base") == "base"
        _try(g_i, "gotostructures")
        errors = []
        mult = None
        for cmd in (("displmultiplier",) if base_shaking else ("loadmultiplier",)) + ("dynmultiplier",):
            try:
                mult = getattr(g_i, cmd)()
                mult = mult[-1] if isinstance(mult, (list, tuple)) else mult
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{cmd}: {e}")
        if mult is None:
            raise SolverError("PLAXIS did not create a dynamic multiplier (needs PLAXIS with Dynamics): " + "; ".join(errors[:2]))
        rec = d.get("record") or [] if d.get("signal") == "record" else []
        if rec:  # V26.1.9 (P2): an earthquake record: a table of accelerations, scaled (g → m/s²)
            amp = float(d.get("scale") if d.get("scale") is not None else 1.0) * (9.81 if d.get("units") == "g" else 1.0)
            if not d.get("duration"):
                dur = float(rec[-1][0])
            for prop, val in (("Signal", "Table"), ("DataType", "Accelerations"), ("DriftCorrection", True)):
                try:
                    _set(g_i, mult, prop, val)
                except Exception as e:  # noqa: BLE001
                    if prop != "DriftCorrection":
                        raise SolverError(f"The dynamic multiplier did not take {prop} = {val}: {e}")
            self._fill_table(g_i, mult, rec)
        else:
            for prop, val in (("Signal", "Harmonic"), ("Amplitude", 1.0), ("Frequency", freq), ("InitialPhaseAngle", 0.0)):
                try:
                    _set(g_i, mult, prop, val)
                except Exception as e:  # noqa: BLE001
                    if prop in ("Signal", "Frequency"):
                        raise SolverError(f"The dynamic multiplier did not take {prop} = {val}: {e}")
        dyn_objs, axis = [], "x"
        if base_shaking:
            ext = self._extent(g_i, is3)
            if not ext:
                raise SolverError("The extents of the model could not be read for the base shaking.")
            x0, x1, y0, y1 = ext
            bottom = None
            try:
                bottom = min(float(_val(p.z if is3 else p.y)) for p in list(g_i.Points))
            except Exception:  # noqa: BLE001
                pass
            if bottom is None:
                raise SolverError("The base of the model could not be found.")
            try:
                made = (g_i.surfdispl((x0, y0, bottom), (x1, y0, bottom), (x1, y1, bottom), (x0, y1, bottom)) if is3
                        else g_i.linedispl((x0, bottom), (x1, bottom)))
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"PLAXIS did not draw the prescribed displacement at the base: {e}")
            made = list(made) if isinstance(made, (list, tuple)) else [made]
            disp = next((o for o in reversed(made) if "displ" in _name(o).lower()), made[-1])
            for prop, val in (("Displacement_x", "Prescribed"), ("ux_start", amp), ("ux", amp), ("Multiplierx", mult),
                              ("Displacement_y" if is3 else "Displacement_y", "Free" if is3 else "Fixed"),
                              ("Displacement_z", "Fixed")):
                try:
                    _set(g_i, disp, prop, val)
                except Exception:  # noqa: BLE001
                    continue
            dyn_objs = [_name(disp)]
        else:
            name = d.get("source")
            load = self._find(g_i, name)
            dyn = None
            for cand in ("Dyn" + name, "Dyn" + re.sub(r"_\d+$", "", name), name):
                try:
                    dyn = getattr(g_i, cand)
                    break
                except Exception:  # noqa: BLE001
                    continue
            dyn = dyn if dyn is not None else load
            vprop = {"line": ("qy_start" if not is3 else "qz_start"), "surface": "sigz", "point": ("Fy" if not is3 else "Fz")}
            kind = "surface" if "surface" in name.lower() else "point" if "point" in name.lower() else "line"
            axis = "z" if is3 else "y"
            ok = False
            for prop, val in ((vprop[kind], -abs(amp)), ("Multiplier" + axis, mult), ("Multiplier_" + axis, mult)):
                try:
                    _set(g_i, dyn, prop, val)
                    ok = ok or prop.startswith("Multiplier")
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
            if not ok:
                raise SolverError(f"The dynamic multiplier could not be put on {name}: " + "; ".join(errors[-2:]))
            dyn_objs = [_name(dyn)]
        _try(g_i, "gotostages")
        base = self._phase(g_i, d["after"])
        ph = g_i.phase(base)
        ph = ph[-1] if isinstance(ph, (list, tuple)) else ph
        try:
            _set_names(g_i, ph, "DeformCalcType", 0, {0: ("Dynamic", "dynamic", 8)}, raw=False)
        except Exception as e:  # noqa: BLE001
            # QA V26.1.9 D3: PLAXIS 3D Advanced refuses dynamic phases: said plainly, the run fails
            raise SolverError(f"PLAXIS refused a dynamic phase after {d['after']}: this PLAXIS licence does not include "
                              f"Dynamics (PLAXIS 3D Advanced has none; Ultimate or the Dynamics module is needed). PLAXIS: {e}")
        try:
            _set(g_i, ph, "Identification", f"{DYNAMIC_ID} after {d['after']}")
        except Exception:  # noqa: BLE001
            pass
        for obj, prop in ((ph, "TimeIntervalSeconds"), (getattr(ph, "Deform", None), "TimeIntervalSeconds"),
                          (ph, "DynamicTimeInterval"), (ph, "TimeInterval")):
            try:
                _set(g_i, obj, prop, dur)
                break
            except Exception:  # noqa: BLE001
                continue
        else:
            raise SolverError("Could not set the dynamic time interval of the dynamic phase.")
        try:
            _set(g_i, ph.Deform, "ResetDisplacementsToZero", True)
        except Exception:  # noqa: BLE001
            pass
        for nm in dyn_objs:
            for o in self._pieces(g_i, nm):
                for how in (lambda: g_i.activate(o, ph), lambda: o.Active.__setitem__(ph, True)):
                    try:
                        how()
                        break
                    except Exception:  # noqa: BLE001
                        continue
                for sub in ("DynamicLoad", "Dynamic"):  # the dynamic component of the load / displacement
                    try:
                        g_i.activate(getattr(o, sub), ph)
                    except Exception:  # noqa: BLE001
                        pass
        sides = ["BoundaryXMin", "BoundaryXMax", "BoundaryYMin"] + (["BoundaryYMax", "BoundaryZMin"] if is3 else [])
        nvis = 0
        for side in sides:  # viscous boundaries (model conditions of the phase)
            for obj in (getattr(g_i, "Dynamics", None), ph, getattr(ph, "Deform", None)):
                if obj is None:
                    continue
                try:
                    _staged_set(obj, side, ph, "Viscous", g_i)
                except Exception:  # noqa: BLE001
                    try:
                        _set(g_i, obj, side, "Viscous")
                    except Exception:  # noqa: BLE001
                        continue
                nvis += 1
                break
        return [f"Dynamic phase {_name(ph)} after {d['after']}: harmonic {freq:g} Hz, amplitude {amp:g}, "
                f"{dur:g} s on {', '.join(dyn_objs)}; {nvis} viscous boundar{'y' if nvis == 1 else 'ies'}"]

    def _fill_table(self, g_i, mult, rows: list) -> None:
        """The rows (time, value) of a table multiplier: rows added to its table, then time and value set (the
        spellings of the PLAXIS 2D / 3D command references; to confirm on PLAXIS, QA/QC)."""
        from .solvers import SolverError
        table = getattr(mult, "Table", None)
        if table is None:  # (an empty table is falsy: it is still the table)
            table = getattr(mult, "table", None)
        if table is None:
            raise SolverError("The dynamic multiplier has no table for the earthquake record in this PLAXIS.")
        errors = []
        for i, (t, a) in enumerate(rows):
            row = None
            for how in (lambda: table.add(), lambda: table.addrow(), lambda: g_i.addrow(table), lambda: table.insert(i)):
                try:
                    row = how()
                    break
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
            if isinstance(row, (list, tuple)):
                row = row[-1]
            if row is None or not hasattr(row, "__dict__") and not hasattr(row, "Time"):
                try:
                    row = table[i]
                except Exception as e:  # noqa: BLE001
                    raise SolverError("PLAXIS did not take the rows of the earthquake record: " + "; ".join(dict.fromkeys(errors + [str(e)]))[:300])
            ok = 0
            for props, val in ((("Time", "time"), float(t)), (("Multiplier", "Value", "Signal"), float(a))):
                for prop in props:
                    try:
                        _set(g_i, row, prop, val)
                        ok += 1
                        break
                    except Exception as e:  # noqa: BLE001
                        errors.append(str(e))
            if ok < 2:
                raise SolverError(f"Row {i + 1} of the earthquake record was not taken by PLAXIS: " + "; ".join(dict.fromkeys(errors))[-300:])

    # --- results ----------------------------------------------------------------------------------------------
    @staticmethod
    def _at_point(g_o, phase_o, rtype, where: str, is3: bool) -> float:
        from .solvers import SolverError
        nums = [float(x) for x in re.findall(r"-?[\d.]+(?:[eE][-+]?\d+)?", where)]
        pt = tuple(nums[:3] if is3 else nums[:2])
        if len(pt) < (3 if is3 else 2):
            raise SolverError(f"point '{where}' not understood: give x;y" + (";z" if is3 else ""))
        errors = []
        vi = 2 if is3 else 1
        # QA V26.1.9 D7: a point on the ground surface or on a model boundary may fall just outside the mesh: it is
        # tried again 1 cm inside (down, then towards the model)
        tries = [pt]
        for dv in (-0.01, -0.05):
            q = list(pt)
            q[vi] += dv
            tries.append(tuple(q))
        for p in tries:
            for how in (lambda: g_o.getsingleresult(phase_o, rtype, p), lambda: g_o.getsingleresult(phase_o, rtype, p, False)):
                try:
                    r = how()
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
                    continue
                try:
                    return abs(float(_val(r)))
                except (TypeError, ValueError):
                    errors.append(str(r)[:80])
        where_txt = ", ".join(f"{c:g}" for c in pt)
        raise SolverError(f"not in this model at this stage: PLAXIS gave no value at ({where_txt}): the point lies outside "
                          f"the mesh, inside a structure, or in soil that is not active (excavated) in this stage. "
                          f"PLAXIS: {'; '.join(dict.fromkeys(errors))[:160]}")

    @staticmethod
    def _trough(g_o, phase_o, rgroup, rtype, where: str, is3: bool) -> float:
        """Settlement trough at the ground surface: Vs = ∫ settlement dx (per metre; 3D: section at mid y), or the
        volume loss VL = Vs / (π D² / 4) in % when the tunnel diameter is given (trough:D)."""
        from .solvers import SolverError
        vals = [float(x) for x in g_o.getresults(phase_o, rtype, "node")]
        xs = [float(x) for x in g_o.getresults(phase_o, rgroup.X, "node")]
        up = [float(x) for x in g_o.getresults(phase_o, rgroup.Z if is3 else rgroup.Y, "node")]
        top = max(up)
        keep = [(x, v, i) for i, (x, v, u) in enumerate(zip(xs, vals, up)) if abs(u - top) < 1e-3]
        if is3 and keep:
            ys = [float(x) for x in g_o.getresults(phase_o, rgroup.Y, "node")]
            y_all = [ys[i] for _, _, i in keep]
            y0 = (min(y_all) + max(y_all)) / 2
            tol = max(0.02 * (max(y_all) - min(y_all)), 0.25)
            near = [k for k in keep if abs(ys[k[2]] - y0) <= tol]
            keep = near or keep
        pts = sorted({round(x, 6): min(v, 0.0) for x, v, _ in keep}.items())
        if len(pts) < 3:
            raise SolverError("not in this model: too few surface nodes for the settlement trough")
        vs = abs(sum((x2 - x1) * (v1 + v2) / 2 for (x1, v1), (x2, v2) in zip(pts, pts[1:])))
        m = re.match(r"\s*trough\s*:\s*([\d.]+)", where)
        if m:
            d = float(m.group(1))
            return 100.0 * vs / (math.pi * d * d / 4) if d > 0 else 0.0
        return vs

    @staticmethod
    def q_total(g_o, phase_o, rgroup, is3: bool) -> list[float]:
        """QA V26.1.9 D6: |q| = √(qx² + qy² + qz²) at each node (PLAXIS 3D has no Qtot result)."""
        from .solvers import SolverError
        comps = []
        for c in ("Qx", "Qy", "Qz") if is3 else ("Qx", "Qy"):
            try:
                comps.append([float(x) for x in g_o.getresults(phase_o, getattr(rgroup, c), "node")])
            except Exception as e:  # noqa: BLE001
                raise SolverError(f"not in this PLAXIS: specific discharge {c} ({e})")
        return [math.sqrt(sum(v * v for v in vs)) for vs in zip(*comps)]

    @staticmethod
    def is_force(result: str) -> bool:
        return result.split(".")[0].strip().lower() in FORCE_GROUPS
