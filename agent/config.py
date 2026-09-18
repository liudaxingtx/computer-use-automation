"""Central config — loads .env once, exposes typed accessors."""
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# override=True so the .env file is the single source of truth, even if a stale
# value leaked into the process environment.
load_dotenv(ROOT / ".env", override=True)

import os  # noqa: E402


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --- Decision LLM: DeepSeek (observe -> decide -> act, structured JSON) ---
DEEPSEEK_API_KEY = _get("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = _get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = _get("DEEPSEEK_MODEL", "deepseek-v4-pro")

# --- Vision LLM: Kimi K3 (screenshot understanding fallback) ---
KIMI_API_KEY = _get("KIMI_API_KEY")
KIMI_BASE_URL = _get("KIMI_BASE_URL", "https://api.moonshot.cn")
KIMI_MODEL = _get("KIMI_MODEL", "kimi-k3")

# --- Target surface ---
MOCK_URL = _get("MOCK_URL", "http://localhost:9000/")

# --- Safety (Phase 5, wired in later) ---
ALLOWED_ACTIONS = {"click", "type", "select", "navigate", "wait", "done", "fail"}
