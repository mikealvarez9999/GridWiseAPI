import json

from app.schemas import Battery

SYSTEM_PROMPT = """Convert campus energy operator notes into directives for a 24-hour scheduler (hours 0-23, today only). One entry per note, in note_index order.

directive_type and exact structured_adjustment:
- solar_reduction {"hours":[..],"factor":f}: usable solar = forecast*f in those hours
- minimum_battery_reserve {"hours":[..],"minimum_energy_kwh":x}: battery energy must stay >= x after each listed hour
- no_charge_window {"hours":[..]}: battery cannot charge
- no_discharge_window {"hours":[..]}: battery cannot discharge
- max_grid_window {"hours":[..],"max_grid_kwh":x}: grid import per hour <= x
- no_op: applies=false, structured_adjustment=null. Everything else: applies=true.

Rules:
1. no_op if the note is not clearly about solar availability, battery charge/discharge availability, battery reserve level, or a grid-import cap for today. Demand/load, tariff/price, occupancy, events, admin items, other days/weeks -> no_op (no directive exists for them). Never invent a type or a number that is not in the note or battery context.
2. Windows are whole hours, start-inclusive, end-EXCLUSIVE for every phrasing (to/until/between/through/dash): 1 PM to 3 PM -> [13,14]; 13:00-15:00 -> [13,14]; 6 PM until 9 PM -> [18,19,20]; single hour "at 3 PM" -> [15]. noon=12, midnight=0. Bare numbers without AM/PM: pick the reading that fits the activity (solar/PV/panels happen in daylight, so "from one until three" -> [13,14]; evening/peak feeder limits are PM). Hours ascending, unique, 0-23; windows past midnight end at 23.
3. factor = usable fraction REMAINING, 0..1: "drops to 20%", "one-fifth of normal" -> 0.2; "80% reduction", "drops by 80%" -> 0.2; "half" -> 0.5; "a third" -> 0.3333; "offline"/"no output" -> 0.
4. Reserve: absolute kWh, or percentage-of-battery * capacity_kwh from the battery context (never above capacity).
5. Grid cap: the stated per-hour import limit (kW counts as kWh per hour): "must not exceed", "at or below", "cap", "no more than".
6. explanation: one short sentence.

Examples:
"Inverter work from 9 in the morning until 11 leaves roughly a third of usual PV." -> solar_reduction [9,10] 0.3333
"Generation falls by 60% between 14:00 and 16:00." -> solar_reduction [14,15] 0.4
"Hold at least 40% of battery capacity between 7 PM and 10 PM." (capacity 250) -> minimum_battery_reserve [19,20,21] 100
"Charger locked out from midnight until 3 AM." -> no_charge_window [0,1,2]
"Do not draw from the battery during the 5-7 PM relay test." -> no_discharge_window [17,18]
"Import limited to 160 kW per hour from 18:00 to 21:00." -> max_grid_window [18,19,20] 160
"Evening demand will be 10% higher due to exams." -> no_op
"Panel cleaning is scheduled for next Tuesday." -> no_op
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
