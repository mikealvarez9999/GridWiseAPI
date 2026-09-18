import copy
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.schemas import DirectiveInterpretation
from tests.conftest import load_public_cases

CASES = load_public_cases()


class FakeInterpreter:
    """Returns the public reference interpretation, or a canned list when set."""

    def __init__(self):
        self.canned = None

    async def interpret(self, notes, battery, deadline, feedback=None, skip_cache=False):
        if self.canned is not None:
            return [DirectiveInterpretation.model_validate(d) for d in self.canned], []
        for c in CASES:
            if c["input"]["operator_notes"] == notes:
                return [DirectiveInterpretation.model_validate(d) for d in c["expected_output"]["directive_interpretation"]], []
        return [DirectiveInterpretation(note_index=i, applies=False, directive_type="no_op",
                                        structured_adjustment=None, explanation="x") for i in range(len(notes))], []


@pytest.fixture
def client(monkeypatch):
    fake = FakeInterpreter()
    monkeypatch.setattr(main, "interpreter", fake)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        c.fake = fake
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_public_case_end_to_end(client, case):
    r = client.post("/optimize-energy", json=case["input"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body.keys()) == [
        "scenario_id", "directive_interpretation", "hourly_plan",
        "total_grid_kwh", "total_cost_bdt", "peak_grid_kwh", "plan_summary",
    ]
    assert body["scenario_id"] == case["input"]["scenario_id"]
    exp = case["expected_output"]
    assert [d["note_index"] for d in body["directive_interpretation"]] == list(range(len(case["input"]["operator_notes"])))
    for got, want in zip(body["directive_interpretation"], exp["directive_interpretation"]):
        assert got["directive_type"] == want["directive_type"]
        assert got["applies"] == want["applies"]
        assert got["structured_adjustment"] == want["structured_adjustment"]
    assert len(body["hourly_plan"]) == 24
    assert [p["hour"] for p in body["hourly_plan"]] == list(range(24))
    assert body["total_cost_bdt"] <= exp["total_cost_bdt"] + 0.01
    assert isinstance(body["plan_summary"], str) and body["plan_summary"]


def test_malformed_json_returns_400(client):
    r = client.post("/optimize-energy", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


def test_missing_field_returns_400(client):
    body = copy.deepcopy(CASES[0]["input"])
    del body["battery"]
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 400


def test_wrong_hour_count_returns_400(client):
    body = copy.deepcopy(CASES[0]["input"])
    body["hours"] = body["hours"][:23]
    assert client.post("/optimize-energy", json=body).status_code == 400


def test_four_notes_returns_400(client):
    body = copy.deepcopy(CASES[0]["input"])
    body["operator_notes"] = ["a", "b", "c", "d"]
    assert client.post("/optimize-energy", json=body).status_code == 400


def test_empty_note_returns_400(client):
    body = copy.deepcopy(CASES[0]["input"])
    body["operator_notes"] = ["   "]
    assert client.post("/optimize-energy", json=body).status_code == 400


def test_negative_demand_returns_400(client):
    body = copy.deepcopy(CASES[0]["input"])
    body["hours"][3]["demand_kwh"] = -5
    assert client.post("/optimize-energy", json=body).status_code == 400


def test_duplicate_hours_returns_422(client):
    body = copy.deepcopy(CASES[0]["input"])
    body["hours"][5]["hour"] = 4
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_scenario"


def test_min_above_capacity_returns_422(client):
    body = copy.deepcopy(CASES[0]["input"])
    body["battery"]["minimum_energy_kwh"] = body["battery"]["capacity_kwh"] + 1
    assert client.post("/optimize-energy", json=body).status_code == 422


def test_unordered_hours_are_accepted(client):
    body = copy.deepcopy(CASES[1]["input"])
    body["hours"] = list(reversed(body["hours"]))
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 200
    assert [p["hour"] for p in r.json()["hourly_plan"]] == list(range(24))


def test_infeasible_interpretation_is_relaxed_not_500(client):
    body = copy.deepcopy(CASES[4]["input"])
    client.fake.canned = [{
        "note_index": 0, "applies": True, "directive_type": "max_grid_window",
        "structured_adjustment": {"hours": list(range(24)), "max_grid_kwh": 0}, "explanation": "impossible",
    }]
    r = client.post("/optimize-energy", json=body)
    assert r.status_code == 200
    assert "relaxed" in r.json()["plan_summary"]


def test_response_is_json_serialisable_numbers(client):
    r = client.post("/optimize-energy", json=CASES[0]["input"])
    text = json.dumps(r.json())
    assert "NaN" not in text and "Infinity" not in text
