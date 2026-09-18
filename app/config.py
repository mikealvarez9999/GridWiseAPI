import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """Minimal .env loader (values already in the environment win)."""
    path = Path(os.getenv("GRIDWISE_ENV_FILE", Path(__file__).resolve().parents[1] / ".env"))
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    groq_api_keys: list[str] = field(default_factory=list)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_models: list[str] = field(default_factory=list)
    groq_reasoning_effort: str = "low"
    llm_timeout_s: float = 10.0
    request_budget_s: float = 25.0
    llm_mock: bool = False
    log_level: str = "INFO"


def load_settings() -> Settings:
    keys = _split(os.getenv("GROQ_API_KEYS", "")) or _split(os.getenv("GROQ_API_KEY", ""))
    return Settings(
        groq_api_keys=keys,
        groq_base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_fallback_models=_split(
            os.getenv("GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b,llama-3.3-70b-versatile")
        ),
        groq_reasoning_effort=os.getenv("GROQ_REASONING_EFFORT", "low"),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "10")),
        request_budget_s=float(os.getenv("REQUEST_BUDGET_S", "25")),
        llm_mock=os.getenv("LLM_MOCK", "0") == "1",
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


settings = load_settings()
