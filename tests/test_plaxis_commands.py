"""Commands sent to PLAXIS for loads (staged values) and phases (marked for calculation), with a fake PLAXIS."""
import pytest

from app.solvers import PlaxisSolver, SolverError


class Staged(dict):
    """A staged property: one value per phase, like Surcharge_1.sigx[Phase_1]."""


class Obj:
    def __init__(self, g, name):
        self.Name, self._g = name, g


class Load(Obj):
    def __init__(self, g, name, active):
        super().__init__(g, name)
        self.sigx, self.Active, self.ref = Staged(), Staged(active), 0.0

    def setproperties(self, prop, value):
        if self._g.mode != "structures":  # as PLAXIS 3D 2024 in Staged construction
            raise RuntimeError(f"Unknown property: {prop}")
        self.ref = value


class Phase(Obj):
    ShouldCalculate = False


class FakeG:
    def __init__(self):
        self.mode = "stages"
        self.Phases = [Phase(self, "InitialPhase"), Phase(self, "Phase_1"), Phase(self, "Phase_2")]
        p0, p1, p2 = self.Phases
        self.Surcharge = Load(self, "Surcharge", {p1: False, p2: False})
        self.Surcharge_1 = Load(self, "Surcharge_1", {p1: True, p2: True})
        self.Other_1 = Load(self, "Other_1", {p1: True, p2: True})
        self.SurfaceLoads = [self.Surcharge, self.Surcharge_1, self.Other_1]
        self.LineLoads = self.PointLoads = []
        self.Materials = []

    def gotostructures(self):
        self.mode = "structures"

    def gotostages(self):
        self.mode = "stages"


def test_load_value_is_set_in_structures_and_in_the_active_phases():
    g = FakeG()
    PlaxisSolver._set_load(PlaxisSolver.__new__(PlaxisSolver), g, "Surcharge", "sigx", 22.5)
    p1, p2 = g.Phases[1:]
    assert g.Surcharge.ref == 22.5
    assert g.Surcharge_1.sigx == {p1: 22.5, p2: 22.5}
    assert g.Surcharge.sigx == {} and g.Other_1.sigx == {}  # inactive pieces and other loads are left alone


def test_load_that_cannot_be_set_says_why():
    g = FakeG()
    with pytest.raises(SolverError, match="Could not set Nothing.sigx"):
        PlaxisSolver._set_load(PlaxisSolver.__new__(PlaxisSolver), g, "Nothing", "sigx", 1.0)


def test_every_phase_is_marked_for_calculation():
    g = FakeG()
    PlaxisSolver._mark_phases(g)
    assert all(p.ShouldCalculate is True for p in g.Phases)


class Pt(Obj):
    def __init__(self, g, name, x, sticky=False):
        super().__init__(g, name)
        self.x, self._sticky = x, sticky

    def setproperties(self, prop, value):
        if not self._sticky:
            setattr(self, prop, value)


def _solver_with(g):
    s = PlaxisSolver.__new__(PlaxisSolver)
    s._find = lambda g_i, n: getattr(g_i, n)
    return s


def test_moved_geometry_is_read_back_from_plaxis():
    g = FakeG()
    g.Point_8 = Pt(g, "Point_8", 30.0)
    g.Point_8.setproperties("x", 33.5)
    lines = _solver_with(g)._check_geometry(g, {("Point_8", "x"): [30.0, 33.5]})
    assert lines == ["Point_8.x: 30 -> 33.5"]


def test_geometry_not_taken_by_plaxis_stops_the_run():
    g = FakeG()
    g.Point_8 = Pt(g, "Point_8", 30.0, sticky=True)
    g.Point_8.setproperties("x", 33.5)
    with pytest.raises(SolverError, match="PLAXIS did not take the new geometry: Point_8.x is 30"):
        _solver_with(g)._check_geometry(g, {("Point_8", "x"): [30.0, 33.5]})


def test_remove_deactivates_a_structure_in_every_stage():
    g = FakeG()
    calls = []
    g.NodeToNodeAnchors = [Obj(g, "Anchor_1_1"), Obj(g, "Anchor_2")]
    g.deactivate = lambda o, p: calls.append((o.Name, p.Name))
    _solver_with(g)._remove(g, "Anchor_1")
    assert calls == [("Anchor_1_1", "Phase_1"), ("Anchor_1_1", "Phase_2")]


def test_all_phases_blue():
    from app.solvers import PlaxisSolver

    class Ph:
        ShouldCalculate = False

    class G:
        Phases = [Ph(), Ph(), Ph()]

        def gotostages(self):
            pass

    sv = PlaxisSolver.__new__(PlaxisSolver)
    g = G()
    sv._connect = lambda version="3d": (None, g)
    assert sv.mark_open_phases() == 3 and all(p.ShouldCalculate is True for p in g.Phases)
