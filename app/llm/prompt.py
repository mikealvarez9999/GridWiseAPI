import json

from app.schemas import Battery

SYSTEM_PROMPT = """You convert campus energy operator notes into structured directives for a 24-hour (hours 0-23) grid/solar/battery scheduler.

Return exactly one entry per note, in note_index order. Each entry:
{"note_index": int, "applies": bool, "directive_type": str, "structured_adjustment": object|null, "explanation": short string}

Supported directive_type values and their exact structured_adjustment shapes:
- solar_reduction: {"hours":[...], "factor": number}  -- usable solar during those hours becomes original*factor
- minimum_battery_reserve: {"hours":[...], "minimum_energy_kwh": number}  -- battery energy must stay >= this after each listed hour
- no_charge_window: {"hours":[...]}  -- battery may not charge in those hours
- no_discharge_window: {"hours":[...]}  -- battery may not discharge in those hours
- max_grid_window: {"hours":[...], "max_grid_kwh": number}  -- grid import per hour may not exceed this in those hours
- no_op: structured_adjustment null, applies false  -- note does not change today's schedule

Rules:
1. applies is true for every directive except no_op. Never invent a directive type outside the list.
2. If a note does not clearly describe solar availability, battery charge/discharge availability, a battery reserve level, or a grid-import cap for THIS 24-hour horizon, it is no_op. Notes about demand, tariffs/prices, occupancy, events, admin matters, other days/weeks, or general reminders are no_op (there is no directive for them).
3. Time windows are whole hours, start-inclusive and end-EXCLUSIVE, for every phrasing ("from X to Y", "from X until Y", "between X and Y", "X-Y", "X through Y", "the X-Y window"): 1 PM to 3 PM -> [13,14]; 13:00-15:00 -> [13,14]; 6 PM until 9 PM -> [18,19,20]. A single hour ("at 3 PM", "during the 3 PM hour") -> [15]. Noon = 12, midnight = 0, 12 AM = 0, 12 PM = 12. "Morning/afternoon/evening" without explicit times: use 6-12, 12-18, 18-22 respectively only if the note clearly implies a window; otherwise no_op. Hours must be unique integers 0-23 in ascending order. A window that wraps past midnight ends at hour 23 (the horizon is today only).
4. solar_reduction factor = usable fraction REMAINING. "drops to 20%", "about one-fifth of normal", "20% of forecast" -> 0.2. "80% reduction", "drops by 80%", "loses 80%" -> 0.2. "half" -> 0.5, "a quarter" -> 0.25, "a third" -> 0.3333. "no solar", "offline", "fully covered", "zero output" -> 0. Factor must be between 0 and 1.
5. minimum_battery_reserve: use the absolute kWh if stated. If stated as a percentage of the battery, compute percentage * battery capacity_kwh from the context provided. The value must not exceed capacity.
6. max_grid_window: the stated per-hour import limit in kWh (treat kW as kWh per hour). Words: "must not exceed", "at or below", "cap", "limit", "no more than", "stay under".
7. Use only numbers present in the note (or derived from the provided battery context). Do not guess missing numbers; if a required number is absent, return no_op.
8. explanation: one short sentence.

Examples (paraphrased):
- "Inverter servicing from 9 in the morning until 11 will cut usable PV to roughly a third." -> solar_reduction, hours [9,10], factor 0.3333
- "Expect rooftop generation to fall by 60% between 14:00 and 16:00 due to haze." -> solar_reduction, hours [14,15], factor 0.4
- "Hold at least 40% of battery capacity in reserve between 7 PM and 10 PM." (capacity 250) -> minimum_battery_reserve, hours [19,20,21], minimum_energy_kwh 100
- "Charging equipment is locked out from midnight until 3 AM." -> no_charge_window, hours [0,1,2]
- "Do not draw from the battery during the 5-7 PM relay test." -> no_discharge_window, hours [17,18]
- "Utility import is limited to 160 kW per hour from 18:00 to 21:00." -> max_grid_window, hours [18,19,20], max_grid_kwh 160
- "Evening demand will be about 10% higher because of the exam schedule." -> no_op (demand changes are not a supported directive)
- "Panel cleaning is scheduled for next Tuesday." -> no_op (not within this horizon)
"""

RESPONSE_SCHEMA = {
    "name": "directive_interpretations",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "interpretations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "note_index": {"type": "integer"},
                        "applies": {"type": "boolean"},
                        "directive_type": {
                            "type": "string",
                            "enum": [
                                "solar_reduction",
                                "minimum_battery_reserve",
                                "no_charge_window",
                                "no_discharge_window",
                                "max_grid_window",
                                "no_op",
                            ],
                        },
                        "structured_adjustment": {
                            "anyOf": [
                                {"type": "null"},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "properties": {
                                        "hours": {"type": "array", "items": {"type": "integer"}},
                                        "factor": {"type": ["number", "null"]},
                                        "minimum_energy_kwh": {"type": ["number", "null"]},
                                        "max_grid_kwh": {"type": ["number", "null"]},
                                    },
                                    "required": ["hours", "factor", "minimum_energy_kwh", "max_grid_kwh"],
                                },
                            ]
                        },
                        "explanation": {"type": "string"},
                    },
                    "required": ["note_index", "applies", "directive_type", "structured_adjustment", "explanation"],
                },
            }
        },
        "required": ["interpretations"],
    },
}


def build_user_message(notes: list[str], battery: Battery, note_indices: list[int] | None = None,
                       feedback: str | None = None) -> str:
    indices = note_indices if note_indices is not None else list(range(len(notes)))
    payload = {
        "battery_context": {
            "capacity_kwh": battery.capacity_kwh,
            "initial_energy_kwh": battery.initial_energy_kwh,
            "base_minimum_energy_kwh": battery.minimum_energy_kwh,
            "max_charge_kwh_per_hour": battery.max_charge_kwh_per_hour,
            "max_discharge_kwh_per_hour": battery.max_discharge_kwh_per_hour,
        },
        "operator_notes": [{"note_index": i, "text": notes[i]} for i in indices],
    }
    msg = "Interpret these notes. Respond with JSON: {\"interpretations\": [...]} containing one entry per note_index listed.\n"
    msg += json.dumps(payload, ensure_ascii=False)
    if feedback:
        msg += "\n\nA previous attempt was rejected by the validator: " + feedback + "\nFix the listed notes only."
    return msg
