# GridWise LLM Energy Optimizer API

LLM-assisted 24-hour campus energy scheduler for the BUP CSE Fest 2026 Hackathon (online preliminary).
One HTTP service that reads operator notes with a language model, validates the interpretation deterministically,
solves a cost-minimizing linear program under battery/solar/grid rules, and returns a judge-replayable plan.

- **Live endpoint:** `https://gridwise-api-0ucv.onrender.com` (Render, Singapore) — `GET /health`, `POST /optimize-energy`
- **Docker fallback image:** `ghcr.io/mikealvarez9999/gridwiseapi:latest` (digest listed below)
- **Endpoints:** `GET /health`, `POST /optimize-energy`

## Architecture

```
POST /optimize-energy
  │
  ├─ 1. Schema validation (Pydantic)         app/schemas.py      → 400 malformed / 422 semantically invalid
  ├─ 2. LLM interpreter (Groq gpt-oss-120b)   app/interpreter.py  notes → JSON directive candidates
  │       key pool + model fallback chain     app/llm/client.py
  │       prompt + strict JSON schema         app/llm/prompt.py
  ├─ 3. Deterministic guardrails              app/guardrails.py   enum/type/hours/range checks, repair retry, safe no_op
  ├─ 4. Directive → constraints               app/directives.py   effective solar, reserve, windows, grid cap
  ├─ 5. LP optimizer (scipy HiGHS)            app/optimizer.py    min Σ grid_kwh·tariff s.t. all rules
  ├─ 6. Final validator (judge replica)       app/validator.py    hour-by-hour replay before responding
  └─ 7. Response + deterministic summary      app/summary.py
```

**The LLM is on the critical path**: every `directive_interpretation` entry comes from the model's structured output.
Deterministic code only validates/normalizes it (sorting hours, deriving `applies` from the type, rejecting invalid
values) and never invents a directive. If the model output for a note cannot be validated after one repair attempt,
that note falls back to `no_op` (safe failure) and the request still succeeds.

### Model / provider
| Item | Value |
|---|---|
| Provider | Groq (OpenAI-compatible API, `https://api.groq.com/openai/v1`) |
| Primary model | `openai/gpt-oss-120b` (`reasoning_effort=low`, `temperature=0`, JSON-schema structured output) |
| Fallback models | `openai/gpt-oss-20b`, then `qwen/qwen3.8-27b` (only when every key is rate-limited/unavailable for the model above) |
| Keys | Several free-tier keys rotate round-robin; a key hit by HTTP 429 is cooled down for `retry-after` seconds |
| Caching | Validated interpretations are cached per (normalized note text, battery capacity) to cut latency and quota use on repeated notes |

### Optimizer
A linear program over 72 variables (`solar_used`, `charge`, `discharge` per hour) solved with `scipy.optimize.linprog(method="highs")`:

- `grid_h = demand_h + charge_h − discharge_h − solar_used_h ≥ 0`, and `≤ max_grid_kwh` in `max_grid_window` hours
- `0 ≤ solar_used_h ≤ solar_h × factor_h` (`factor_h` from `solar_reduction`, else 1)
- `0 ≤ charge_h ≤ max_charge` (0 inside `no_charge_window`), `0 ≤ discharge_h ≤ max_discharge` (0 inside `no_discharge_window`)
- `max(base_min, reserve_h) ≤ E_h ≤ capacity`, `E_h = E_init + Σ_{k≤h}(charge_k − discharge_k)`, `E_23 = E_init`
- objective `min Σ tariff_h · grid_h`

Post-processing nets simultaneous charge/discharge, rounds to 6 dp, recomputes `grid_kwh` from the rounded values so
the energy balance holds exactly, replays the battery state, and computes the totals from the final `hourly_plan`.
Every directive is a **hard** constraint. If the LP has no solution the request is refused with
`422 {"error":"infeasible_request"}` (see [Infeasible requests](#infeasible-requests)); the service never returns a
plan that violates its own `directive_interpretation`.

## Quickstart (local, from a clean machine)

Requirements: [uv](https://docs.astral.sh/uv/) (installs Python 3.12 automatically) and a Groq API key.

```bash
git clone https://github.com/mikealvarez9999/GridWiseAPI.git
cd GridWiseAPI
uv sync                       # creates .venv with Python 3.12 and all dependencies
cp .env.example .env          # then edit .env and set GROQ_API_KEYS=gsk_...   (comma-separated for several keys)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal:

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}

# one public sample case
python3 -c "import json; print(json.dumps(json.load(open('samples/public_cases.json'))['cases'][0]['input']))" > /tmp/sample-01.json
curl -s -X POST http://localhost:8000/optimize-energy -H 'content-type: application/json' --data @/tmp/sample-01.json | python3 -m json.tool | head -40

# all 10 public cases, replayed exactly like the judge (interpretation match + validity + cost quality)
uv run python scripts/run_public_samples.py --base-url http://localhost:8000
```

### Environment variables
| Name | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEYS` | yes | – | Comma-separated Groq API keys (`GROQ_API_KEY` with a single key also works) |
| `GROQ_MODEL` | no | `openai/gpt-oss-120b` | Primary interpretation model |
| `GROQ_FALLBACK_MODELS` | no | `openai/gpt-oss-20b,qwen/qwen3.8-27b` | Fallback chain |
| `GROQ_REASONING_EFFORT` | no | `low` | gpt-oss reasoning effort (`low`/`medium`/`high`) |
| `LLM_TIMEOUT_S` | no | `10` | Per LLM call timeout |
| `REQUEST_BUDGET_S` | no | `25` | Whole-request LLM budget (judge timeout is 30 s) |
| `PORT` | no | `8000` | Listening port |
| `LLM_MOCK` | no | `0` | `1` = offline smoke mode: every note becomes `no_op` without calling the LLM (CI only) |

Secrets are read from the environment (or a git-ignored `.env`); nothing is baked into the image or the repository.

## Docker fallback image

```bash
docker pull ghcr.io/mikealvarez9999/gridwiseapi:latest
docker run --rm -p 8000:8000 -e GROQ_API_KEYS=gsk_... ghcr.io/mikealvarez9999/gridwiseapi:latest
curl -s http://localhost:8000/health
```

- Exposed port: `8000` (override with `-e PORT=...` and matching `-p`); binds `0.0.0.0`.
- Pinned digest (commit 56038a3): `ghcr.io/mikealvarez9999/gridwiseapi@sha256:bdf70e903e8f5d25584769349889de251b7f0e486e9017699038307f92074974` — every push updates `:latest`; the `docker` workflow summary lists the digest for each commit.
- The image is built and pushed by `.github/workflows/docker.yml`, which also starts the pushed image and verifies
  `/health`, one public sample (`LLM_MOCK=1`) and the 400 path before the job passes.
- Build locally instead: `docker build -t gridwiseapi . && docker run --rm -p 8000:8000 -e GROQ_API_KEYS=gsk_... gridwiseapi`

## API contract

`GET /health` → `200 {"status":"ok"}`

`POST /optimize-energy` → request/response exactly as in the Problem Statement (Sections 07 and 10).
Status codes: `200` success · `400` malformed JSON or structurally invalid body (`invalid_request`) · `422` well-formed
but impossible: bad scenario such as duplicate hours or `battery.minimum_energy_kwh > capacity_kwh` (`invalid_scenario`),
or operator directives that no 24-hour schedule can honour (`infeasible_request`) · `500` controlled internal error
(`{"error":"internal_error"}`, no stack traces). Every error body is `{"error": "<code>", "detail": "<message>"}`.

Example response fragment:
```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
     "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
     "explanation": "Panel washing leaves about 25% of forecast solar from noon to 2 PM."},
    {"note_index": 1, "applies": false, "directive_type": "no_op", "structured_adjustment": null,
     "explanation": "An administrative deadline does not affect today's energy schedule."}
  ],
  "hourly_plan": [{"hour": 0, "grid_kwh": 90.0, "solar_used_kwh": 0.0, "battery_action": "idle", "battery_kwh": 0.0, "battery_energy_after_kwh": 110.0}, "..."],
  "total_grid_kwh": 2692.5, "total_cost_bdt": 38365.0, "peak_grid_kwh": 175.0,
  "plan_summary": "Applied reduced solar availability from the operator notes; ..."
}
```

### Infeasible requests

The optimizer treats all five directive types as hard LP constraints and checks the solver status: `OPTIMAL` → `200`,
`INFEASIBLE` → `422 infeasible_request` with **no plan** and a deterministic explanation where one can be proved per
hour (`min_grid_h = max(0, demand_h − solar_h·factor_h − max_discharge_if_allowed) > cap_h`, or a reserve above capacity).
Directives are never dropped, softened or clamped to make a plan appear.

```bash
# T11: hour 18 has demand 205 kWh, no solar and a 50 kWh/h discharge limit, so grid ≥ 155 kWh; a zero cap is impossible
python3 - <<'PY' > /tmp/t11.json
import json; c = json.load(open("samples/public_cases.json"))["cases"][0]["input"]
c.update(scenario_id="T11", operator_notes=["Grid import must be zero from 6 PM to 7 PM."],
         battery={"capacity_kwh": 220, "initial_energy_kwh": 110, "minimum_energy_kwh": 40,
                  "max_charge_kwh_per_hour": 50, "max_discharge_kwh_per_hour": 50})
print(json.dumps(c))
PY
curl -s -X POST http://localhost:8000/optimize-energy -H 'content-type: application/json' --data @/tmp/t11.json
# 422 {"error":"infeasible_request","detail":"hour 18 needs at least 155 kWh from the grid (demand 205 - usable solar 0 - max discharge 50) but the grid-import cap is 0 kWh"}

# U03: a reserve larger than the battery is reported as stated by the LLM, then refused (not clamped to capacity)
sed 's/Grid import must be zero from 6 PM to 7 PM./Keep a reserve of at least 500 kWh in the battery from 6 PM to 9 PM./' /tmp/t11.json \
  | curl -s -X POST http://localhost:8000/optimize-energy -H 'content-type: application/json' --data @-
# 422 {"error":"infeasible_request","detail":"minimum_battery_reserve of 500 kWh at hour 18 exceeds the battery capacity of 220 kWh"}
```

When infeasibility only arises from interacting windows (each hour fine on its own), `detail` is the generic
`"the interpreted directives cannot all be satisfied by any 24-hour schedule"`.

## Guardrails (Problem Statement §08)
- `directive_type` must be one of the six allowed values; anything else is rejected and re-queried, then `no_op`.
- `note_index` must cover `0..N-1` exactly once; duplicates are dropped, missing notes re-queried individually.
- `hours`: integers 0–23, de-duplicated and sorted ascending; empty or out-of-range → rejected.
- `factor ∈ [0,1]`; `minimum_energy_kwh ≥ 0`; `max_grid_kwh ≥ 0`; all finite. A reserve above capacity is kept as
  stated (the prompt forbids clamping) and rejected by the feasibility check with `422 infeasible_request`.
- `applies` is derived from the type (`no_op` → `false` + `null`, everything else → `true`); extra keys are stripped.
- The final schedule is replayed in-process (`app/validator.py`) with the same rules the judge uses before it is returned.

## Tests
```bash
uv run pytest -q -m "not live"     # optimizer on all 10 public cases (cost == reference), validator, guardrails, API (LLM mocked)
uv run pytest -q -m live           # real Groq calls: 10 public interpretations + scripts/paraphrase_bank.json
uv run python scripts/load_test.py --base-url http://localhost:8000 --concurrency 5 --rounds 2
```
`notebooks/colab_test.ipynb` runs the same public-sample check inside Google Colab.

## Deployment
Render web service `https://gridwise-api-0ucv.onrender.com` (Docker runtime, Singapore region) defined in `render.yaml`; `GROQ_API_KEYS` is set as a Render
secret. An external cron pinger calls `/health` every 5 minutes so the free instance never idles during evaluation.

## Known limitations
- Free-tier Groq rate limits are the main throughput constraint; the key pool, per-model fallback chain and note cache mitigate bursts. Under sustained heavy load a note may fall back to `no_op` after the LLM budget is exhausted (logged, never a crash).
- Overlapping directives of the same type are merged deterministically (solar factors multiply, reserves take the maximum, grid caps take the minimum, windows union).
- Notes are interpreted for the single 24-hour horizon only; notes about other days are `no_op` by design.
- Windows that cross midnight ("11 PM to 1 AM") map to `[0, 23]`. A reversed window that does not cross midnight
  ("4 PM to 2 PM") is ambiguous; the safe outcome is `no_op`, but the LLM may also pick one of the two readings.

## Dependencies / credits
FastAPI, Uvicorn, Pydantic v2, SciPy (HiGHS LP solver), NumPy, `openai` Python SDK (used against Groq's OpenAI-compatible endpoint), httpx, pytest.
Language model: OpenAI `gpt-oss-120b` served by Groq. Development assisted by AI coding tools; architecture and logic are the team's own.
