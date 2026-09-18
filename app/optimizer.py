from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from app.directives import HourlyConstraints
from app.schemas import Battery, HourEntry, HourlyPlanEntry

H = 24
# Tiny penalty on battery throughput so equal-cost solutions prefer fewer pointless cycles.
CYCLE_EPS = 1e-7
ROUND = 6


@dataclass
class PlanResult:
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float


def solve(hours: list[HourEntry], battery: Battery, cons: HourlyConstraints) -> PlanResult | None:
    """Minimize grid cost over 24 hours. Returns None when infeasible."""
    demand = np.array([h.demand_kwh for h in hours], dtype=float)
    solar = np.array([h.solar_kwh for h in hours], dtype=float)
    tariff = np.array([h.tariff_bdt_per_kwh for h in hours], dtype=float)
    eff_solar = solar * np.array(cons.solar_factor, dtype=float)

    # Variable layout: x = [s_0..s_23, c_0..c_23, d_0..d_23]
    S, C, D = 0, H, 2 * H
    n = 3 * H

    cost = np.zeros(n)
    cost[S:S + H] = -tariff
    cost[C:C + H] = tariff + CYCLE_EPS
    cost[D:D + H] = -tariff + CYCLE_EPS

    A_ub, b_ub = [], []
    for h in range(H):
        # grid_h = demand + c - d - s >= 0  ->  s - c + d <= demand
        row = np.zeros(n)
        row[S + h], row[C + h], row[D + h] = 1, -1, 1
        A_ub.append(row)
        b_ub.append(demand[h])
        if cons.max_grid[h] is not None:
            # demand + c - d - s <= G  ->  c - d - s <= G - demand
            row = np.zeros(n)
            row[S + h], row[C + h], row[D + h] = -1, 1, -1
            A_ub.append(row)
            b_ub.append(cons.max_grid[h] - demand[h])
        # E_h = E_init + sum_{k<=h}(c_k - d_k)
        cum = np.zeros(n)
        cum[C:C + h + 1] = 1
        cum[D:D + h + 1] = -1
        A_ub.append(cum)
        b_ub.append(battery.capacity_kwh - battery.initial_energy_kwh)
        A_ub.append(-cum)
        b_ub.append(battery.initial_energy_kwh - cons.min_energy[h])

    A_eq = np.zeros((1, n))
    A_eq[0, C:C + H] = 1
    A_eq[0, D:D + H] = -1
    b_eq = [0.0]

    bounds = []
    for h in range(H):
        bounds.append((0.0, float(eff_solar[h])))
    for h in range(H):
        bounds.append((0.0, battery.max_charge_kwh_per_hour if cons.charge_allowed[h] else 0.0))
    for h in range(H):
        bounds.append((0.0, battery.max_discharge_kwh_per_hour if cons.discharge_allowed[h] else 0.0))

    res = linprog(
        cost,
        A_ub=np.array(A_ub),
        b_ub=np.array(b_ub),
        A_eq=A_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
    )
    if res.status != 0:
        return None

    x = res.x
    return _build_plan(demand, eff_solar, tariff, battery, x[S:S + H], x[C:C + H], x[D:D + H])


def _build_plan(demand, eff_solar, tariff, battery: Battery, s, c, d) -> PlanResult:
    plan: list[HourlyPlanEntry] = []
    energy = battery.initial_energy_kwh
    total_grid = 0.0
    total_cost = 0.0
    peak = 0.0
    for h in range(H):
        net = c[h] - d[h]
        if abs(net) < 1e-7:
            net = 0.0
        battery_kwh = round(abs(net), ROUND)
        if battery_kwh == 0:
            action, charge, discharge = "idle", 0.0, 0.0
        elif net > 0:
            action, charge, discharge = "charge", battery_kwh, 0.0
        else:
            action, charge, discharge = "discharge", 0.0, battery_kwh

        solar_used = min(round(max(s[h], 0.0), ROUND), float(eff_solar[h]))
        grid = demand[h] + charge - discharge - solar_used
        if grid < 0:
            solar_used = round(max(demand[h] + charge - discharge, 0.0), ROUND)
            grid = 0.0
        grid = round(grid, ROUND)

        energy = round(energy + charge - discharge, ROUND)
        plan.append(
            HourlyPlanEntry(
                hour=h,
                grid_kwh=grid,
                solar_used_kwh=solar_used,
                battery_action=action,
                battery_kwh=battery_kwh,
                battery_energy_after_kwh=energy,
            )
        )
        total_grid += grid
        total_cost += grid * tariff[h]
        peak = max(peak, grid)

    return PlanResult(
        hourly_plan=plan,
        total_grid_kwh=round(total_grid, ROUND),
        total_cost_bdt=round(total_cost, ROUND),
        peak_grid_kwh=round(peak, ROUND),
    )
