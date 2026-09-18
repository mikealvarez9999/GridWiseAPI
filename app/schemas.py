import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]
DIRECTIVE_TYPES: tuple[str, ...] = DirectiveType.__args__  # type: ignore[attr-defined]


class SemanticError(ValueError):
    """Well-formed request that cannot describe a valid scenario (HTTP 422)."""


def _finite_non_negative(v: float, name: str) -> float:
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite")
    if v < 0:
        raise ValueError(f"{name} must be non-negative")
    return v


class HourEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hour: int = Field(ge=0, le=23)
    demand_kwh: float
    solar_kwh: float
    tariff_bdt_per_kwh: float

    @field_validator("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh")
    @classmethod
    def _check_numbers(cls, v: float, info):
        return _finite_non_negative(v, info.field_name)


class Battery(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capacity_kwh: float
    initial_energy_kwh: float
    minimum_energy_kwh: float
    max_charge_kwh_per_hour: float
    max_discharge_kwh_per_hour: float

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
    )
    @classmethod
    def _check_numbers(cls, v: float, info):
        return _finite_non_negative(v, info.field_name)


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    scenario_id: str = Field(min_length=1)
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourEntry] = Field(min_length=24, max_length=24)
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def _notes_non_empty(cls, v: list[str]):
        if any(not isinstance(n, str) or not n.strip() for n in v):
            raise ValueError("operator_notes entries must be non-empty strings")
        return v

    def semantic_check(self) -> None:
        seen = sorted(h.hour for h in self.hours)
        if seen != list(range(24)):
            raise SemanticError("hours must contain each hour 0..23 exactly once")
        b = self.battery
        if b.minimum_energy_kwh > b.capacity_kwh:
            raise SemanticError("battery.minimum_energy_kwh exceeds capacity_kwh")
        if not (b.minimum_energy_kwh <= b.initial_energy_kwh <= b.capacity_kwh):
            raise SemanticError("battery.initial_energy_kwh must lie within [minimum_energy_kwh, capacity_kwh]")

    def sorted_hours(self) -> list[HourEntry]:
        return sorted(self.hours, key=lambda h: h.hour)


class DirectiveInterpretation(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: dict | None
    explanation: str


class HourlyPlanEntry(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: Literal["charge", "discharge", "idle"]
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
