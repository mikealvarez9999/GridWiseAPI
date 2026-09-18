import json
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "public_cases.json"


def load_public_cases() -> list[dict]:
    return json.loads(SAMPLES.read_text())["cases"]


@pytest.fixture(scope="session")
def public_cases() -> list[dict]:
    return load_public_cases()
