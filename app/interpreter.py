import logging
import time
from collections import OrderedDict

from app.config import Settings
from app.guardrails import safe_no_op, validate_batch
from app.llm.client import LLMClient, LLMUnavailable
from app.llm.prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_user_message
from app.schemas import Battery, DirectiveInterpretation

log = logging.getLogger("gridwise.interpreter")


class Interpreter:
    def __init__(self, settings: Settings, client: LLMClient | None = None):
        self.settings = settings
        self.client = client or LLMClient(settings)
        self._cache: OrderedDict[tuple[str, float], DirectiveInterpretation] = OrderedDict()
        self._cache_max = 2000

    def _cache_key(self, note: str, battery: Battery) -> tuple[str, float]:
        return (" ".join(note.lower().split()), float(battery.capacity_kwh))

    def _cache_put(self, key, entry: DirectiveInterpretation) -> None:
        self._cache[key] = entry
        if len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)

    async def interpret(
        self, notes: list[str], battery: Battery, deadline: float, feedback: str | None = None,
        skip_cache: bool = False,
    ) -> tuple[list[DirectiveInterpretation], list[str]]:
        """Returns (one entry per note in note_index order, warnings)."""
        n = len(notes)
        results: dict[int, DirectiveInterpretation] = {}
        warnings: list[str] = []

        if not skip_cache:
            for i, note in enumerate(notes):
                hit = self._cache.get(self._cache_key(note, battery))
                if hit is not None:
                    results[i] = hit.model_copy(update={"note_index": i})

        pending = [i for i in range(n) if i not in results]
        if pending and self.settings.llm_mock:
            for i in pending:
                results[i] = DirectiveInterpretation(
                    note_index=i, applies=False, directive_type="no_op", structured_adjustment=None,
                    explanation="LLM mock mode: no directive applied.",
                )
            pending = []

        attempt = 0
        reasons: dict[int, str] = {}
        while pending and attempt < 2:
            effort = self.settings.groq_reasoning_effort if attempt == 0 else "medium"
            fb = feedback
            if attempt > 0 and reasons:
                fb = "; ".join(f"note {i}: {r}" for i, r in reasons.items()) + (f". {feedback}" if feedback else "")
            try:
                raw = await self.client.complete_json(
                    SYSTEM_PROMPT, build_user_message(notes, battery, pending, fb), RESPONSE_SCHEMA, effort, deadline,
                )
            except LLMUnavailable as e:
                warnings.append(f"llm_unavailable: {e}")
                log.error("LLM unavailable: %s", e)
                break
            valid, reasons = validate_batch(raw.get("interpretations"), n, battery)
            for i in pending:
                if i in valid:
                    results[i] = valid[i]
                    self._cache_put(self._cache_key(notes[i], battery), valid[i])
            reasons = {i: r for i, r in reasons.items() if i in pending and i not in results}
            pending = sorted(reasons)
            if pending:
                log.warning("guardrail rejected notes %s: %s", pending, reasons)
            attempt += 1

        for i in pending:
            warnings.append(f"note {i} fell back to no_op ({reasons.get(i, 'unresolved')})")
            results[i] = safe_no_op(i)

        return [results[i] for i in range(n)], warnings


def deadline_from_now(seconds: float) -> float:
    return time.monotonic() + seconds
