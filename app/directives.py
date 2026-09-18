from dataclasses import dataclass, field

from app.schemas import Battery, DirectiveInterpretation


@dataclass
class HourlyConstraints:
    """Per-hour effect of all applicable directives, merged deterministically."""

    solar_factor: list[float] = field(default_factory=lambda: [1.0] * 24)
    min_energy: list[float] = field(default_factory=lambda: [0.0] * 24)
    charge_allowed: list[bool] = field(default_factory=lambda: [True] * 24)
    discharge_allowed: list[bool] = field(default_factory=lambda: [True] * 24)
    max_grid: list[float | None] = field(default_factory=lambda: [None] * 24)


def build_constraints(
    directives: list[DirectiveInterpretation], battery: Battery
) -> HourlyConstraints:
    c = HourlyConstraints(min_energy=[battery.minimum_energy_kwh] * 24)
    for d in directives:
        if d.directive_type == "no_op" or not d.applies or not d.structured_adjustment:
            continue
        adj = d.structured_adjustment
        hours = adj.get("hours", [])
        if d.directive_type == "solar_reduction":
            for h in hours:
                c.solar_factor[h] *= float(adj["factor"])
        elif d.directive_type == "minimum_battery_reserve":
            for h in hours:
                c.min_energy[h] = max(c.min_energy[h], float(adj["minimum_energy_kwh"]))
        elif d.directive_type == "no_charge_window":
            for h in hours:
                c.charge_allowed[h] = False
        elif d.directive_type == "no_discharge_window":
            for h in hours:
                c.discharge_allowed[h] = False
        elif d.directive_type == "max_grid_window":
            for h in hours:
                cap = float(adj["max_grid_kwh"])
                c.max_grid[h] = cap if c.max_grid[h] is None else min(c.max_grid[h], cap)
    return c
