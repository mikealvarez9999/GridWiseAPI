import asyncio
import json
import logging
import time
from typing import Any

import openai
from openai import AsyncOpenAI

from app.config import Settings

log = logging.getLogger("gridwise.llm")


class LLMUnavailable(Exception):
    pass


class LLMClient:
    """OpenAI-compatible client for Groq with a rotating key pool and a model fallback chain."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.models = [settings.groq_model] + [m for m in settings.groq_fallback_models if m != settings.groq_model]
        self._clients = [
            AsyncOpenAI(api_key=k, base_url=settings.groq_base_url, timeout=settings.llm_timeout_s, max_retries=0)
            for k in settings.groq_api_keys
        ]
        self._next_key = 0
        self._cooldown: dict[tuple[int, str], float] = {}
        self._schema_unsupported: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._clients)

    async def _pick_key(self, model: str, tried: set[int]) -> int | None:
        async with self._lock:
            now = time.monotonic()
            for _ in range(len(self._clients)):
                i = self._next_key
                self._next_key = (self._next_key + 1) % len(self._clients)
                if i in tried:
                    continue
                if self._cooldown.get((i, model), 0.0) <= now:
                    return i
            return None

    def _cool(self, key_idx: int, model: str, seconds: float) -> None:
        self._cooldown[(key_idx, model)] = time.monotonic() + seconds

    async def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        reasoning_effort: str,
        deadline: float,
    ) -> dict[str, Any]:
        if not self._clients:
            raise LLMUnavailable("no API keys configured")
        last_error: str = "unknown"
        for sweep in range(4):
            for model in self.models:
                tried: set[int] = set()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining < 1.5:
                        raise LLMUnavailable(f"request budget exhausted ({last_error})")
                    key_idx = await self._pick_key(model, tried)
                    if key_idx is None:
                        break
                    tried.add(key_idx)
                    timeout = min(self.settings.llm_timeout_s, remaining - 0.5)
                    use_schema = model not in self._schema_unsupported
                    try:
                        content = await self._call(key_idx, model, system, user, schema, reasoning_effort, timeout, use_schema)
                        return _parse_json(content)
                    except openai.BadRequestError as e:
                        msg = str(e)
                        if use_schema and ("response_format" in msg or "json_schema" in msg or "schema" in msg):
                            log.warning("model %s rejected json_schema; falling back to json_object", model)
                            self._schema_unsupported.add(model)
                            tried.discard(key_idx)
                            continue
                        last_error = f"bad request on {model}: {_redact(msg)}"
                        log.warning(last_error)
                        break  # a prompt/format problem will not be fixed by another key
                    except openai.RateLimitError as e:
                        wait = _retry_after(e) or 20.0
                        self._cool(key_idx, model, wait)
                        last_error = f"429 on {model} (key #{key_idx}, cool {wait:.0f}s)"
                        log.warning(last_error)
                    except openai.AuthenticationError:
                        self._cool(key_idx, model, 3600)
                        last_error = f"auth failure on key #{key_idx}"
                        log.error(last_error)
                    except openai.NotFoundError:
                        self._cool(key_idx, model, 3600)
                        last_error = f"model {model} not found"
                        log.error(last_error)
                        break
                    except (openai.APITimeoutError, openai.APIConnectionError, openai.APIStatusError) as e:
                        self._cool(key_idx, model, 5.0)
                        last_error = f"{type(e).__name__} on {model}"
                        log.warning(last_error)
                    except (json.JSONDecodeError, ValueError) as e:
                        last_error = f"unparseable output from {model}: {e}"
                        log.warning(last_error)
            # Every key/model pair is cooling down: wait for the earliest one if the budget allows.
            wait = self._earliest_cooldown()
            remaining = deadline - time.monotonic()
            if wait is None or wait + 3.0 > remaining:
                break
            log.warning("all keys/models cooling; waiting %.1fs (sweep %d)", wait, sweep + 1)
            await asyncio.sleep(wait + 0.2)
        raise LLMUnavailable(last_error)

    def _earliest_cooldown(self) -> float | None:
        now = time.monotonic()
        waits = [
            until - now
            for (i, m), until in self._cooldown.items()
            if m in self.models and until > now and until - now < 600
        ]
        return min(waits) if waits else None

    async def _call(self, key_idx, model, system, user, schema, reasoning_effort, timeout, use_schema) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "timeout": timeout,
        }
        if use_schema:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": schema}
        else:
            kwargs["response_format"] = {"type": "json_object"}
        if "gpt-oss" in model:
            kwargs["reasoning_effort"] = reasoning_effort
        t0 = time.monotonic()
        resp = await self._clients[key_idx].chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        log.info("llm ok model=%s key=#%d latency=%.2fs", model, key_idx, time.monotonic() - t0)
        return content


def _parse_json(content: str) -> dict[str, Any]:
    content = content.strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(content[start:end + 1])
    if isinstance(data, list):
        data = {"interpretations": data}
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def _retry_after(e: openai.APIStatusError) -> float | None:
    try:
        v = e.response.headers.get("retry-after")
        return float(v) if v else None
    except Exception:
        return None


def _redact(msg: str) -> str:
    return msg[:200].replace("gsk_", "gsk_***")
