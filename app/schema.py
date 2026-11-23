"""Objectives and the setup data (variable parameters, target outputs) shared by the AI, planner and engine."""
from typing import Literal, Optional

from pydantic import BaseModel

OBJECTIVES = {
    "sensitivity": {
        "title": "Sensitivity analysis",
        "assistant_hint": "How the PLAXIS results respond when one input changes.",
        "variables_hint": "Inputs to vary, each over its range.",
        "targets_hint": "Control parameters: the PLAXIS results to follow.",
    },
    "optimize": {
        "title": "Design optimization",
        "assistant_hint": "Explain your problem and the target output.",
        "variables_hint": "Parameters the AI may change, with the range to search.",
        "targets_hint": "Criteria every acceptable design must meet, e.g. FoS > 1.5.",
    },
    "combinations": {
        "title": "Design scenarios",
        "assistant_hint": "Explain the combinations you want to run.",
        "variables_hint": "Can be load combinations or any other factors. Every combination is run.",
        "targets_hint": "What to collect from each run. Can simply be the files of the combinations or parameters.",
    },
    "design_change": {
        "title": "Design modification",
        "assistant_hint": "Explain the change you want to make to the existing design.",
        "variables_hint": "Parameters to change and their new values.",
        "targets_hint": "Criteria the changed design must still meet.",
    },
    "calibration": {
        "title": "Calibration / Back-analysis",
        "assistant_hint": "Give the measured values (settlement, deflection, anchor force...) to match.",
        "variables_hint": "Uncertain inputs (soil stiffness, strength...) and the range to search.",
        "targets_hint": "Measured values the PLAXIS results should match.",
    },
    "reporting": {
        "title": "Advanced reporting",
        "assistant_hint": "Calculate the existing design and report its results in full detail.",
        "variables_hint": "No variable is needed: the existing design is calculated once.",
        "targets_hint": "The PLAXIS results to report.",
    },
    "new_design": {
        "title": "New design",
        "assistant_hint": "Explain the new design: site, loads, constraints and what it must achieve.",
        "variables_hint": "Design parameters and the range to search.",
        "targets_hint": "Criteria the new design must meet.",
    },
}

# The Variables page: one section per PLAXIS Input mode (the tabs along the top of PLAXIS Input).
SECTIONS = {
    "soil": "Soil",
    "structures": "Structures",
    "mesh": "Mesh",
    "flow": "Flow conditions",
    "stages": "Staged construction",
}
# sections of earlier versions of the app
LEGACY_SECTIONS = {"geometry": "structures", "loads": "structures", "water": "flow", "materials": "soil"}
STRUCTURAL_PROPS = {"ea", "ea1", "ea2", "ei", "d", "w", "e1", "e2", "g12", "g13", "g23", "nu12", "lspacing",
                    "diameter", "fmax", "tskinstartmax", "tskinendmax", "gamma"}


def norm_section(sec: str | None, ref: str = "") -> str:
    """A Variables section key: PLAXIS' own modes; old names are mapped (materials: soil or structural)."""
    if sec in SECTIONS:
        return sec
    if sec == "materials" and ref and ref.rsplit(".", 1)[-1].lower() in STRUCTURAL_PROPS:
        return "structures"
    return LEGACY_SECTIONS.get(sec or "", "structures")


Operator = Literal[">", ">=", "<", "<=", "=", "report"]


class Variable(BaseModel):
    name: str                      # short id, e.g. x1
    label: str                     # e.g. "Diaphragm wall thickness"
    unit: str                      # e.g. m, kPa, degrees
    plaxis_ref: str                # Plaxis object.property, e.g. "Plate_1.d" or "Clay.cref" ("" if unknown yet)
    kind: Literal["range", "values"]
    min: Optional[float]
    max: Optional[float]
    step: Optional[float]
    values: list[str]              # discrete values; "LC1: 1.35" gives a label to a number
    current: Optional[str]         # existing value (optimize) or new value (design change)
    section: Optional[str]         # Variables page section: soil, structures, mesh, flow, stages
    item: Optional[str] = None     # the PLAXIS model item it was picked from (e.g. "mat:Clay.phi")


class Target(BaseModel):
    name: str                      # short id, e.g. T1
    label: str                     # e.g. "Factor of safety"
    result: str                    # FoS, Utot_max, Ux_max, Uy_max, Uz_max, Plate.M11 ..., or "file"
    phase: str                     # Plaxis phase to read it from ("" = last phase)
    operator: Operator
    value: Optional[float]
    unit: str


class Goal(BaseModel):
    direction: Literal["minimize", "maximize"]
    of: str                        # a variable or target name, e.g. x1


VAR_DEFAULTS = {"label": "", "unit": "", "plaxis_ref": "", "kind": "range", "min": None, "max": None,
                "step": None, "values": [], "current": None, "section": None, "item": None}
TARGET_DEFAULTS = {"label": "", "result": "", "phase": "", "operator": "report", "value": None, "unit": ""}


def _num(v):
    if v in ("", None):
        return None
    return float(v)


def coerce_variable(d: dict) -> Variable:
    d = {**VAR_DEFAULTS, **{k: v for k, v in d.items() if k in Variable.model_fields}}
    for k in ("min", "max", "step"):
        d[k] = _num(d[k])
    if isinstance(d["values"], str):
        d["values"] = [s.strip() for s in d["values"].split(",") if s.strip()]
    d["current"] = None if d["current"] in ("", None) else str(d["current"])
    d["section"] = norm_section(d["section"], d.get("plaxis_ref") or "") if d["section"] else None
    return Variable.model_validate(d)


def coerce_target(d: dict) -> Target:
    d = {**TARGET_DEFAULTS, **{k: v for k, v in d.items() if k in Target.model_fields}}
    d["value"] = _num(d["value"])
    return Target.model_validate(d)
