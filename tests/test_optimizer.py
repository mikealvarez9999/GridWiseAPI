import pytest

from app.directives import build_constraints
from app.optimizer import solve
from app.schemas import DirectiveInterpretation, OptimizeRequest
from app.validator import validate_plan
from tests.conftest import load_public_cases

CASES = load_public_cases()


def _solve_case(case: dict, directives: list[dict] | None = None):
    req = OptimizeRequest.model_validate(case["input"])
    req.semantic_check()
    hours = req.sorted_hours()
    dirs = [DirectiveInterpretation.model_validate(d) for d in (directives or case["expected_output"]["directive_interpretation"])]
    cons = build_constraints(dirs, req.battery)
    result = solve(hours, req.battery, cons)
    return req, hours, cons, result


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_public_case_valid_and_optimal(case):
    req, hours, cons, result = _solve_case(case)
    assert result is not None, "LP infeasible on a feasible public case"
    errors = validate_plan(
        hours, req.battery, cons, result.hourly_plan,
        (result.total_grid_kwh, result.total_cost_bdt, result.peak_grid_kwh),
    )
    assert errors == []
    ref_cost = case["expected_output"]["total_cost_bdt"]
    assert result.total_cost_bdt <= ref_cost + 0.01, f"cost {result.total_cost_bdt} worse than reference {ref_cost}"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reference_plan_passes_validator(case):
    """Sanity check that our validator accepts the organizer's own reference schedules."""
    from app.schemas import HourlyPlanEntry

    req, hours, cons, _ = _solve_case(case)
    exp = case["expected_output"]
    plan = [HourlyPlanEntry.model_validate(p) for p in exp["hourly_plan"]]
    errors = validate_plan(hours, req.battery, cons, plan, (exp["total_grid_kwh"], exp["total_cost_bdt"], exp["peak_grid_kwh"]))
    assert errors == []


def test_no_directives_is_never_worse_than_with_directives():
    case = CASES[4]  # grid cap case
    _, _, _, with_dir = _solve_case(case)
    _, _, _, without = _solve_case(case, directives=[])
    assert without.total_cost_bdt <= with_dir.total_cost_bdt + 0.01


def test_all_day_no_charge_forces_idle_battery_and_neutrality():
    case = CASES[1]
    directives = [{
        "note_index": 0, "applies": True, "directive_type": "no_charge_window",
        "structured_adjustment": {"hours": list(range(24))}, "explanation": "",
    }]
    req, hours, cons, result = _solve_case(case, directives)
    assert result is not None
    assert all(p.battery_action != "charge" for p in result.hourly_plan)
    assert abs(result.hourly_plan[-1].battery_energy_after_kwh - req.battery.initial_energy_kwh) < 1e-6
    assert validate_plan(hours, req.battery, cons, result.hourly_plan) == []


def test_reserve_equal_to_capacity_is_feasible_when_reachable():
    case = CASES[2]
    cap = case["input"]["battery"]["capacity_kwh"]
    directives = [{
        "note_index": 0, "applies": True, "directive_type": "minimum_battery_reserve",
        "structured_adjustment": {"hours": [12], "minimum_energy_kwh": cap}, "explanation": "",
    }]
    req, hours, cons, result = _solve_case(case, directives)
    assert result is not None
    assert abs(result.hourly_plan[12].battery_energy_after_kwh - cap) < 1e-6
    assert validate_plan(hours, req.battery, cons, result.hourly_plan) == []


def test_contradictory_directives_return_none():
    case = CASES[4]
    directives = [{
        "note_index": 0, "applies": True, "directive_type": "max_grid_window",
        "structured_adjustment": {"hours": list(range(24)), "max_grid_kwh": 0}, "explanation": "",
    }]
    _, _, _, result = _solve_case(case, directives)
    assert result is None
