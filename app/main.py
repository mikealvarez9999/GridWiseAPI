import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.directives import build_constraints
from app.interpreter import Interpreter, deadline_from_now
from app.optimizer import infeasibility_reason, solve
from app.schemas import InfeasibleError, OptimizeRequest, OptimizeResponse, SemanticError
from app.summary import build_summary
from app.validator import validate_plan

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gridwise.api")

app = FastAPI(title="GridWise LLM Energy Optimizer", version="1.0.0", docs_url=None, redoc_url=None)
interpreter = Interpreter(settings)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    detail = f"{loc}: {first.get('msg', 'invalid')}" if loc else str(first.get("msg", "invalid request"))
    return JSONResponse(status_code=400, content={"error": "invalid_request", "detail": detail})


@app.exception_handler(SemanticError)
async def _semantic_error(_: Request, exc: SemanticError):
    return JSONResponse(status_code=422, content={"error": "invalid_scenario", "detail": str(exc)})


@app.exception_handler(InfeasibleError)
async def _infeasible_error(_: Request, exc: InfeasibleError):
    return JSONResponse(status_code=422, content={"error": "infeasible_request", "detail": str(exc)})


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    log.exception("unhandled error: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"error": "internal_error"})


@app.middleware("http")
async def _access_log(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    log.info("%s %s -> %d in %.3fs", request.method, request.url.path, response.status_code, time.monotonic() - t0)
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(req: OptimizeRequest):
    req.semantic_check()
    hours = req.sorted_hours()
    battery = req.battery
    deadline = deadline_from_now(settings.request_budget_s)

    directives, warnings = await interpreter.interpret(req.operator_notes, battery, deadline)
    cons = build_constraints(directives, battery)
    result = solve(hours, battery, cons)
    if warnings:
        log.warning("%s: %s", req.scenario_id, warnings)

    if result is None:
        # Every directive is a hard LP constraint: an infeasible LP means the operator notes cannot
        # all be honoured, so refuse rather than return a plan that violates the interpretation.
        reason = infeasibility_reason(hours, battery, cons) or (
            "the interpreted directives cannot all be satisfied by any 24-hour schedule"
        )
        log.warning("%s: infeasible: %s", req.scenario_id, reason)
        raise InfeasibleError(reason)

    errors = validate_plan(hours, battery, cons, result.hourly_plan,
                           (result.total_grid_kwh, result.total_cost_bdt, result.peak_grid_kwh))
    if errors:
        log.error("%s: final validator flagged %s", req.scenario_id, errors[:3])

    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=directives,
        hourly_plan=result.hourly_plan,
        total_grid_kwh=result.total_grid_kwh,
        total_cost_bdt=result.total_cost_bdt,
        peak_grid_kwh=result.peak_grid_kwh,
        plan_summary=build_summary(directives, result),
    )

