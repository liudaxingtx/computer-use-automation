"""Central config — loads .env once, exposes typed, provider-agnostic accessors.

The system has exactly two LLM *roles*, each filled by any OpenAI-compatible
provider (DeepSeek, Kimi/Moonshot, OpenAI, OpenRouter, a self-hosted vLLM, …):

  - DECISION LLM  — drives discovery (observe → decide → act), structured JSON.
  - VISION  LLM   — screenshot-understanding fallback for image-only surfaces.

Point each role at whatever provider you want via `*_BASE_URL` / `*_MODEL` /
`*_API_KEY`; nothing here is hard-wired to a specific vendor. The defaults are
just examples so the project runs out of the box.
"""
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True so the .env file is the single source of truth, even if a stale
# value leaked into the process environment.
load_dotenv(ROOT / ".env", override=True)

import os  # noqa: E402


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name) or default)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name) or default)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    v = _get(name).strip().lower()
    if v in ("", "none"):
        return default
    return v in ("1", "true", "yes", "on")


# --- Decision LLM (observe → decide → act, structured JSON output) ---
# Any OpenAI-compatible provider. Examples: DeepSeek, OpenAI, OpenRouter, vLLM.
DECISION_LLM_API_KEY = _get("DECISION_LLM_API_KEY")
DECISION_LLM_BASE_URL = _get("DECISION_LLM_BASE_URL", "https://api.deepseek.com")
DECISION_LLM_MODEL = _get("DECISION_LLM_MODEL", "deepseek-v4-pro")
DECISION_LLM_TEMPERATURE = _get_float("DECISION_LLM_TEMPERATURE", 0.2)
DECISION_LLM_JSON_MODE = _get_bool("DECISION_LLM_JSON_MODE", True)
DECISION_LLM_MAX_TOKENS = _get_int("DECISION_LLM_MAX_TOKENS", 8192)

# --- Vision LLM (screenshot understanding fallback) ---
# Any OpenAI-compatible provider. Examples: Kimi/Moonshot, OpenAI, OpenRouter.
VISION_LLM_API_KEY = _get("VISION_LLM_API_KEY")
VISION_LLM_BASE_URL = _get("VISION_LLM_BASE_URL", "https://api.moonshot.cn/v1")
VISION_LLM_MODEL = _get("VISION_LLM_MODEL", "kimi-k3")
VISION_LLM_TEMPERATURE = _get_float("VISION_LLM_TEMPERATURE", 1.0)
VISION_LLM_MAX_TOKENS = _get_int("VISION_LLM_MAX_TOKENS", 8192)

# --- Target surface ---
MOCK_URL = _get("MOCK_URL", "http://localhost:9000/")

# --- Safety (Phase 5, wired in later) ---
ALLOWED_ACTIONS = {"click", "type", "select", "navigate", "wait", "done", "fail"}
