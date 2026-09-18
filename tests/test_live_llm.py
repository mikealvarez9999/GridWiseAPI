"""Live tests against the real LLM provider. Run with: uv run pytest -m live  (needs GROQ_API_KEYS)."""
import json
import os
import time
from pathlib import Path

import pytest

from app.config import load_settings
from app.interpreter import Interpreter
from app.schemas import Battery
from tests.conftest import load_public_cases

pytestmark = pytest.mark.live

if not os.getenv("GROQ_API_KEYS") and not os.getenv("GROQ_API_KEY"):
    pytest.skip("GROQ_API_KEYS not set", allow_module_level=True)

CASES = load_public_cases()
BANK = json.loads((Path(__file__).resolve().parents[1] / "scripts" / "paraphrase_bank.json").read_text())


@pytest.fixture(scope="module")
def interp():
    return Interpreter(load_settings())


def _same(got: dict | None, want: dict | None) -> bool:
    if got is None or want is None:
        return got is want
    if got.get("hours") != want.get("hours"):
        return False
    for k in ("factor", "minimum_energy_kwh", "max_grid_kwh"):
        if k in want and abs(float(got.get(k, 1e9)) - float(want[k])) > 0.01:
            return False
    return True


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
async def test_public_case_interpretation(interp, case):
    battery = Battery.model_validate(case["input"]["battery"])
    got, warnings = await interp.interpret(case["input"]["operator_notes"], battery, time.monotonic() + 25)
    assert not warnings, warnings
    for g, w in zip(got, case["expected_output"]["directive_interpretation"]):
        assert g.directive_type == w["directive_type"], (case["input"]["operator_notes"][g.note_index], g)
        assert g.applies == w["applies"]
        assert _same(g.structured_adjustment, w["structured_adjustment"]), (g, w)


@pytest.mark.parametrize("item", BANK["cases"], ids=[c["note"][:40] for c in BANK["cases"]])
async def test_paraphrase_bank(interp, item):
    battery = Battery.model_validate(BANK["battery"])
    got, warnings = await interp.interpret([item["note"]], battery, time.monotonic() + 25)
    assert not warnings, warnings
    g = got[0]
    assert g.directive_type == item["type"], (item["note"], g)
    if item["type"] != "no_op":
        assert _same(g.structured_adjustment, item["adj"]), (item["note"], g)
