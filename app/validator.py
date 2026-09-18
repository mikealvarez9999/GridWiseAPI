import math

from app.directives import HourlyConstraints
from app.schemas import Battery, HourEntry, HourlyPlanEntry

TOL = 0.01


def validate_plan(
    hours: list[HourEntry],
    battery: Battery,
    cons: HourlyConstraints,
    plan: list[HourlyPlanEntry],
    totals: tuple[float, float, float] | None = None,
    tol: float = TOL,
) -> list[str]:
    """Replay the plan hour by hour exactly as the judge does. Returns a list of violations (empty = valid)."""
    errors: list[str] = []
    if [p.hour for p in plan] != list(range(24)):
        errors.append("hourly_plan must contain hours 0..23 in order")
        return errors

    energy = battery.initial_energy_kwh
    total_grid = total_cost = peak = 0.0
    for h, (src, p) in enumerate(zip(hours, plan)):
        for name, v in (
            ("grid_kwh", p.grid_kwh),
            ("solar_used_kwh", p.solar_used_kwh),
            ("battery_kwh", p.battery_kwh),
            ("battery_energy_after_kwh", p.battery_energy_after_kwh),
        ):
            if not math.isfinite(v) or v < -tol:
                errors.append(f"h{h}: {name} must be finite and non-negative")

        eff_solar = src.solar_kwh * cons.solar_factor[h]
        if p.solar_used_kwh > eff_solar + tol:
            errors.append(f"h{h}: solar_used {p.solar_used_kwh} exceeds effective solar {eff_solar:.4f}")

        charge = discharge = 0.0
        if p.battery_action == "charge":
            charge = p.battery_kwh
            if not cons.charge_allowed[h]:
                errors.append(f"h{h}: charging during no_charge_window")
            if charge > battery.max_charge_kwh_per_hour + tol:
                errors.append(f"h{h}: charge {charge} exceeds max charge rate")
        elif p.battery_action == "discharge":
            discharge = p.battery_kwh
            if not cons.discharge_allowed[h]:
                errors.append(f"h{h}: discharging during no_discharge_window")
            if discharge > battery.max_discharge_kwh_per_hour + tol:
                errors.append(f"h{h}: discharge {discharge} exceeds max discharge rate")
        else:
            if p.battery_kwh > tol:
                errors.append(f"h{h}: idle hour must have battery_kwh = 0")

        energy = energy + charge - discharge
        if abs(energy - p.battery_energy_after_kwh) > tol:
            errors.append(f"h{h}: battery_energy_after {p.battery_energy_after_kwh} != replay {energy:.4f}")
        if energy < cons.min_energy[h] - tol:
            errors.append(f"h{h}: energy {energy:.4f} below minimum {cons.min_energy[h]}")
        if energy > battery.capacity_kwh + tol:
            errors.append(f"h{h}: energy {energy:.4f} above capacity")

        lhs = p.grid_kwh + p.solar_used_kwh + discharge
        rhs = src.demand_kwh + charge
        if abs(lhs - rhs) > tol:
            errors.append(f"h{h}: energy balance {lhs:.4f} != {rhs:.4f}")

        cap = cons.max_grid[h]
        if cap is not None and p.grid_kwh > cap + tol:
            errors.append(f"h{h}: grid {p.grid_kwh} exceeds cap {cap}")

        total_grid += p.grid_kwh
        total_cost += p.grid_kwh * src.tariff_bdt_per_kwh
        peak = max(peak, p.grid_kwh)

    if abs(energy - battery.initial_energy_kwh) > tol:
        errors.append(f"final energy {energy:.4f} != initial {battery.initial_energy_kwh}")

    if totals is not None:
        tg, tc, pk = totals
        if abs(tg - total_grid) > tol:
            errors.append(f"total_grid_kwh {tg} != recalculated {total_grid:.4f}")
        if abs(tc - total_cost) > tol:
            errors.append(f"total_cost_bdt {tc} != recalculated {total_cost:.4f}")
        if abs(pk - peak) > tol:
            errors.append(f"peak_grid_kwh {pk} != recalculated {peak:.4f}")
    return errors
