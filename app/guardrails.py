import math
from typing import Any

from app.schemas import DIRECTIVE_TYPES, Battery, DirectiveInterpretation

REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "solar_reduction": ("hours", "factor"),
    "minimum_battery_reserve": ("hours", "minimum_energy_kwh"),
    "no_charge_window": ("hours",),
    "no_discharge_window": ("hours",),
    "max_grid_window": ("hours", "max_grid_kwh"),
    "no_op": (),
}


class GuardrailError(ValueError):
    pass


def _number(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise GuardrailError(f"{name} must be a number")
    f = float(v)
    if not math.isfinite(f):
        raise GuardrailError(f"{name} must be finite")
    return f


def _hours(v: Any) -> list[int]:
    if not isinstance(v, list) or not v:
        raise GuardrailError("hours must be a non-empty list")
    out: set[int] = set()
    for h in v:
        if isinstance(h, bool):
            raise GuardrailError("hours must be integers")
        if isinstance(h, float) and h.is_integer():
            h = int(h)
        if not isinstance(h, int) or h < 0 or h > 23:
            raise GuardrailError(f"hour {h!r} is not an integer in 0..23")
        out.add(h)
    return sorted(out)


def validate_entry(raw: Any, note_count: int, battery: Battery) -> DirectiveInterpretation:
    """Deterministically validate one LLM-produced entry (Section 08 guardrails)."""
    if not isinstance(raw, dict):
        raise GuardrailError("entry must be an object")
    idx = raw.get("note_index")
    if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < note_count):
        raise GuardrailError(f"note_index {idx!r} does not identify an operator note")
    dtype = raw.get("directive_type")
    if dtype not in DIRECTIVE_TYPES:
        raise GuardrailError(f"unsupported directive_type {dtype!r}")
    explanation = raw.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = "Interpreted from the operator note."

    if dtype == "no_op":
        return DirectiveInterpretation(
            note_index=idx, applies=False, directive_type="no_op",
            structured_adjustment=None, explanation=explanation.strip(),
        )

    adj = raw.get("structured_adjustment")
    if not isinstance(adj, dict):
        raise GuardrailError("structured_adjustment must be an object for non-no_op directives")
    clean: dict[str, Any] = {"hours": _hours(adj.get("hours"))}
    if dtype == "solar_reduction":
        factor = _number(adj.get("factor"), "factor")
        if not (0.0 <= factor <= 1.0):
            raise GuardrailError("factor must be within [0, 1]")
        clean["factor"] = round(factor, 6)
    elif dtype == "minimum_battery_reserve":
        reserve = _number(adj.get("minimum_energy_kwh"), "minimum_energy_kwh")
        if reserve < 0 or reserve > battery.capacity_kwh:
            raise GuardrailError("minimum_energy_kwh must be within [0, capacity]")
        clean["minimum_energy_kwh"] = round(reserve, 6)
    elif dtype == "max_grid_window":
        cap = _number(adj.get("max_grid_kwh"), "max_grid_kwh")
        if cap < 0:
            raise GuardrailError("max_grid_kwh must be non-negative")
        clean["max_grid_kwh"] = round(cap, 6)

    return DirectiveInterpretation(
        note_index=idx, applies=True, directive_type=dtype,
        structured_adjustment=clean, explanation=explanation.strip(),
    )


def validate_batch(
    raw_entries: Any, note_count: int, battery: Battery
) -> tuple[dict[int, DirectiveInterpretation], dict[int, str]]:
    """Returns (valid entries by note_index, failure reasons by note_index for notes still unresolved)."""
    valid: dict[int, DirectiveInterpretation] = {}
    reasons: dict[int, str] = {}
    if isinstance(raw_entries, dict):
        raw_entries = raw_entries.get("interpretations") or raw_entries.get("directives") or [raw_entries]
    if not isinstance(raw_entries, list):
        raw_entries = []
    for raw in raw_entries:
        try:
            entry = validate_entry(raw, note_count, battery)
        except GuardrailError as e:
            idx = raw.get("note_index") if isinstance(raw, dict) else None
            if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < note_count and idx not in valid:
                reasons[idx] = str(e)
            continue
        if entry.note_index not in valid:
            valid[entry.note_index] = entry
            reasons.pop(entry.note_index, None)
    for i in range(note_count):
        if i not in valid and i not in reasons:
            reasons[i] = "missing interpretation"
    return valid, reasons


def safe_no_op(note_index: int) -> DirectiveInterpretation:
    return DirectiveInterpretation(
        note_index=note_index, applies=False, directive_type="no_op", structured_adjustment=None,
        explanation="The note could not be mapped to a supported directive with confidence, so it is treated as no_op.",
    )
