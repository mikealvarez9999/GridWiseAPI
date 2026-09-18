from app.optimizer import PlanResult
from app.schemas import DirectiveInterpretation

LABELS = {
    "solar_reduction": "reduced solar availability",
    "minimum_battery_reserve": "a minimum battery reserve",
    "no_charge_window": "a no-charge window",
    "no_discharge_window": "a no-discharge window",
    "max_grid_window": "a grid-import cap",
}


def build_summary(directives: list[DirectiveInterpretation], plan: PlanResult, relaxed: list[str]) -> str:
    applied = [LABELS[d.directive_type] for d in directives if d.directive_type != "no_op"]
    ignored = sum(1 for d in directives if d.directive_type == "no_op")
    charge_hours = [p.hour for p in plan.hourly_plan if p.battery_action == "charge"]
    discharge_hours = [p.hour for p in plan.hourly_plan if p.battery_action == "discharge"]

    parts = []
    if applied:
        parts.append("Applied " + ", ".join(applied) + " from the operator notes")
    else:
        parts.append("No operator note changed the schedule")
    if ignored:
        parts.append(f"treated {ignored} note(s) as no_op")
    if charge_hours:
        parts.append(f"charges the battery in low-tariff hours {_ranges(charge_hours)}")
    if discharge_hours:
        parts.append(f"discharges during high-tariff hours {_ranges(discharge_hours)}")
    parts.append("uses all available solar first and returns the battery to its initial level")
    text = "; ".join(parts) + f". Total grid cost {plan.total_cost_bdt:.2f} BDT, peak import {plan.peak_grid_kwh:.2f} kWh."
    if relaxed:
        text += " Note: the interpreted directives were mutually infeasible, so " + ", ".join(relaxed) + " were relaxed."
    return text


def _ranges(hours: list[int]) -> str:
    out, start, prev = [], hours[0], hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = h
    out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(out)
