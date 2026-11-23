"""AI assistant: reads the user's explanation, drawings and documents and fills in the variable parameters,
target outputs and goal. Asks a clarifying question when something needed is missing."""
import base64
import json
import mimetypes
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from .config import MODEL, ai_mode
from .schema import OBJECTIVES, SECTIONS, Goal, Target, Variable


class SetupTurn(BaseModel):
    status: Literal["clarify", "ready"]
    message: str
    variables: list[Variable]
    targets: list[Target]
    goal: Optional[Goal]


SYSTEM = """You are the AI assistant of a PLAXIS (2D/3D geotechnical finite element) design automation tool.
The user picked an objective and uploaded a PLAXIS model. Your job is to understand the problem and fill in:

1. Variable parameters: what the tool will change between PLAXIS runs. For each: a short name (x1, x2, ...),
   a label, unit (kPa, degrees, m, kN/m ...), plaxis_ref (the PLAXIS object.property to change, e.g.
   "Clay.cref", "Clay.phi", "Plate_1.d", "LineLoad_1_1.qy_start@Phase_3"; use names from the model
   description when given, else your best guess, or "" if unknown), and either kind "range" with min/max/step
   or kind "values" with a list (for load combinations write "LC1: 1.35" so each has a label and a number).
   Geometry (wall height, depth, length, excavation level) is changed through point coordinates, e.g.
   "Point_7.z" for the top of a wall; several points that move together are separated by ";",
   e.g. "Point_7.z; Point_8.z". Use the point names from the model description when given.
   current = the existing value if known (for "Design change" it is the NEW value to apply).
   section = where the variable is shown on the Variables page, one per PLAXIS Input mode: "soil" (soil
   material properties, soil layer levels), "structures" (point coordinates, wall height/depth, plates,
   anchors, embedded beams and their materials, loads), "mesh" (element distribution, ref "Mesh.size"), "flow"
   (groundwater head e.g. "Borehole_1.Head", water levels) or "stages" (phase settings and staged values, e.g.
   "Phase_3.TimeInterval", "LineLoad_1_1.qy_start@Phase_3", "NodeToNodeAnchor_1.PrestressForce@Phase_2"). The user writes from one of these sections (given in the
   current state below); put a new variable there unless it clearly belongs to another section.
2. Target outputs: what to read from each run and the criterion. Short name (T1, T2, ...), label,
   result (FoS for factor of safety from a safety phase, Utot_max / Ux_max / Uy_max / Uz_max for max
   displacement, "<Group>.<Type>" like "Plate.M11" for structural forces, or "file" to just keep each run's
   model file). To read a result only at some location, add a filter after "|", e.g. "Plate.M11 | Z >= 10.5"
   (moment near the top of a wall whose top is at z = 11) or "Plate.M11 | Z = max" (at the highest nodes), phase ("" = last phase), operator (> >= < <= = or "report" to only collect it), value, unit.
3. Goal (only for Optimize existing design / New design): what to minimize or maximize among passing designs,
   e.g. minimize x1 (wall thickness). Null otherwise.

Always return the COMPLETE current lists (keep what the user already has unless they ask to change it; the user
may have edited the tables by hand, respect their edits). Read numbers, dimensions, soil parameters and loads
from uploaded drawings and documents. If something essential is missing (no ranges, no criterion, unclear which
part of the model to change) set status "clarify" and ask ONE short, specific question. When the setup is
complete enough to run, set status "ready" and in "message" briefly say what you set up and ask the user to
review the tables and press "Review & run". Keep messages short."""


def _content_blocks(text: str, attachments: list[dict]) -> list[dict] | str:
    if not attachments:
        return text
    blocks = []
    for a in attachments:
        p = Path(a["path"])
        mime = mimetypes.guess_type(p.name)[0] or ""
        section = a.get("section", "assistant")
        note = f"[Uploaded for {section}: {p.name}]"
        if mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            blocks.append({"type": "text", "text": note})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                       "data": base64.b64encode(p.read_bytes()).decode()}})
        elif mime == "application/pdf":
            blocks.append({"type": "text", "text": note})
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                          "data": base64.b64encode(p.read_bytes()).decode()}})
        else:
            try:
                body = p.read_text(errors="replace")[:200_000]
                blocks.append({"type": "text", "text": f"{note}\n<file>\n{body}\n</file>"})
            except Exception:  # noqa: BLE001
                blocks.append({"type": "text", "text": note + " (binary file, not readable)"})
    blocks.append({"type": "text", "text": text or "(see the uploaded file)"})
    return blocks


def context_message(objective: str, model_description: str, setup: dict) -> str:
    page = setup.get("current_section") or ""
    where = (f"The user is on the Variables page, section {SECTIONS[page]}." if page in SECTIONS else
             "The user is on the Output page (target outputs and goal)." if page == "output" else "")
    sections = "".join(
        f"\nCross section '{s['name']}' (origin upper left, x right, z = depth down, m): nodes "
        + ", ".join(f"({x:g},{z:g})" for x, z in s["nodes"][:80])
        + "; structure lines (walls, anchors, slabs...): "
        + ", ".join(f"({s['nodes'][a][0]:g},{s['nodes'][a][1]:g})-({s['nodes'][b][0]:g},{s['nodes'][b][1]:g})"
                    for a, b, *t in s["segments"][:200] if t and t[0] == "structure")
        for s in setup.get("sections") or [])
    return (f"Objective: {OBJECTIVES[objective]['title']}\n\nModel:\n{model_description}\n{sections}\n\n{where}\n"
            f"Current tables (JSON):\n{json.dumps({k: setup.get(k) for k in ('variables', 'targets', 'goal')})}")


def _keep(setup: dict, status: str, message: str) -> SetupTurn:
    return SetupTurn(status=status, message=message,
                     variables=[Variable.model_validate(v) for v in setup.get("variables", [])],
                     targets=[Target.model_validate(t) for t in setup.get("targets", [])],
                     goal=Goal.model_validate(setup["goal"]) if setup.get("goal") else None)


def converse(objective: str, model_description: str, setup: dict, history: list[dict]) -> SetupTurn:
    mode = ai_mode()
    if mode == "demo":
        return _demo(objective, setup, history)
    if mode == "offline":
        from . import offline_ai
        return offline_ai.converse(objective, model_description, setup, history)
    import anthropic
    from .config import api_key

    messages = []
    for m in history:
        messages.append({"role": m["role"], "content": _content_blocks(m["content"], m.get("attachments") or [])})
    # Current state goes last, as an operator note, so hand edits are always seen.
    messages.append({"role": "system", "content": context_message(objective, model_description, setup)})
    response = anthropic.Anthropic(api_key=api_key()).beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=SYSTEM,
        messages=messages,
        output_format=SetupTurn,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return _keep(setup, "clarify", "Sorry, I couldn't process that. Could you rephrase it?")
    return response.parsed_output


# ---------------------------------------------------------------------------
# Demo mode: canned setups per objective so the app can be tried without an API key.
# ---------------------------------------------------------------------------
def _v(name, label, unit, ref, **kw):
    d = {"name": name, "label": label, "unit": unit, "plaxis_ref": ref, "kind": "range", "min": None,
         "max": None, "step": None, "values": [], "current": None, "section": None}
    d.update(kw)
    return Variable.model_validate(d)


def _t(name, label, result, op, value, unit=""):
    return Target.model_validate({"name": name, "label": label, "result": result, "phase": "",
                                  "operator": op, "value": value, "unit": unit})


def _demo(objective: str, setup: dict, history: list[dict]) -> SetupTurn:
    if setup.get("variables") and setup.get("targets"):
        return SetupTurn(status="ready", message="Your tables look complete. Review them and press Review & run.",
                         variables=[Variable.model_validate(v) for v in setup["variables"]],
                         targets=[Target.model_validate(t) for t in setup["targets"]],
                         goal=Goal.model_validate(setup["goal"]) if setup.get("goal") else None)
    goal = None
    if objective == "combinations":
        variables = [_v("x1", "Load combination factor", "-", "LineLoad_1_1.qy_start", kind="values",
                        values=["LC1: 1.0", "LC2: 1.35", "LC3: 1.5"]),
                     _v("x2", "Surcharge", "kPa", "SurfaceLoad_1_1.qz", kind="values", values=["10", "20"])]
        targets = [_t("T1", "Model file of each combination", "file", "report", None),
                   _t("T2", "Max total displacement", "Utot_max", "report", None, "mm")]
    elif objective == "design_change":
        variables = [_v("x1", "Wall thickness", "m", "Plate_1.d", min=0.8, max=0.8, current="0.6")]
        targets = [_t("T1", "Factor of safety", "FoS", ">=", 1.5)]
    else:
        variables = [_v("x1", "Wall thickness", "m", "Plate_1.d", min=0.4, max=1.2, step=0.2, current="0.8"),
                     _v("x2", "Soil friction angle", "degrees", "Clay.phi", min=25, max=35, step=5),
                     _v("x3", "Cohesion", "kPa", "Clay.cref", min=5, max=15, step=5)]
        targets = [_t("T1", "Factor of safety", "FoS", ">", 1.5),
                   _t("T2", "Max total displacement", "Utot_max", "<", 50, "mm")]
        goal = Goal(direction="minimize", of="x1")
    return SetupTurn(status="ready",
                     message="(Demo) I set up example parameters and targets. Edit them as needed, "
                             "then press Review & run.",
                     variables=variables, targets=targets, goal=goal)
