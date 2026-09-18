import pytest

from app.guardrails import GuardrailError, validate_batch, validate_entry
from app.schemas import Battery

BAT = Battery(capacity_kwh=200, initial_energy_kwh=100, minimum_energy_kwh=40,
              max_charge_kwh_per_hour=50, max_discharge_kwh_per_hour=50)


def _entry(**kw):
    base = {"note_index": 0, "applies": True, "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [2, 3]}, "explanation": "x"}
    base.update(kw)
    return base


def test_hours_are_sorted_and_deduped():
    e = validate_entry(_entry(structured_adjustment={"hours": [5, 3, 3, 4]}), 1, BAT)
    assert e.structured_adjustment == {"hours": [3, 4, 5]}


def test_extra_keys_are_stripped_and_applies_forced():
    e = validate_entry(_entry(applies=False, structured_adjustment={"hours": [2], "factor": None, "note": "x"}), 1, BAT)
    assert e.applies is True
    assert e.structured_adjustment == {"hours": [2]}


def test_no_op_forces_null_adjustment_and_false():
    e = validate_entry(_entry(applies=True, directive_type="no_op", structured_adjustment={"hours": [1]}), 1, BAT)
    assert e.applies is False and e.structured_adjustment is None


@pytest.mark.parametrize("bad", [
    _entry(directive_type="demand_increase"),
    _entry(structured_adjustment={"hours": [24]}),
    _entry(structured_adjustment={"hours": []}),
    _entry(structured_adjustment={"hours": ["2"]}),
    _entry(structured_adjustment=None),
    _entry(note_index=3),
    _entry(directive_type="solar_reduction", structured_adjustment={"hours": [1], "factor": 1.5}),
    _entry(directive_type="solar_reduction", structured_adjustment={"hours": [1]}),
    _entry(directive_type="minimum_battery_reserve", structured_adjustment={"hours": [1], "minimum_energy_kwh": 250}),
    _entry(directive_type="max_grid_window", structured_adjustment={"hours": [1], "max_grid_kwh": -1}),
    _entry(directive_type="max_grid_window", structured_adjustment={"hours": [1], "max_grid_kwh": "150"}),
])
def test_rejects_invalid(bad):
    with pytest.raises(GuardrailError):
        validate_entry(bad, 2, BAT)


def test_float_integer_hours_accepted():
    e = validate_entry(_entry(structured_adjustment={"hours": [2.0, 3.0]}), 1, BAT)
    assert e.structured_adjustment == {"hours": [2, 3]}


def test_batch_reports_missing_and_duplicates():
    raw = [_entry(note_index=1), _entry(note_index=1, structured_adjustment={"hours": [9]}),
           _entry(note_index=2, directive_type="bogus")]
    valid, reasons = validate_batch(raw, 3, BAT)
    assert set(valid) == {1}
    assert valid[1].structured_adjustment == {"hours": [2, 3]}
    assert set(reasons) == {0, 2}
    assert reasons[0] == "missing interpretation"


def test_batch_accepts_wrapped_object():
    valid, reasons = validate_batch({"interpretations": [_entry()]}, 1, BAT)
    assert 0 in valid and not reasons
