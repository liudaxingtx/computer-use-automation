"""LLM client — two provider-agnostic roles: decision and vision.

Both roles talk to any OpenAI-compatible endpoint through one thin httpx helper;
no third-party SDK, and nothing here is hard-wired to a specific vendor. Each
role's endpoint / model / parameters come from `agent.config` (`DECISION_LLM_*`
and `VISION_LLM_*`).
"""
import json
import re
import time

import httpx

from . import config


def _post(base_url: str, api_key: str, payload: dict, timeout: int = 120) -> dict:
    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"LLM API {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def _parse_json(text: str) -> dict:
    """Tolerantly parse a JSON object out of an LLM response."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def decide(messages: list, temperature: float | None = None,
           max_tokens: int | None = None) -> dict:
    """Ask the decision LLM for the next action. Returns a parsed JSON dict.

    Provider-agnostic: endpoint / model / params come from `DECISION_LLM_*`.
    Two defensive behaviours that make this robust across vendors:

      * reasoning models (DeepSeek, Kimi, o-series, …) emit `reasoning_content`
        (the monologue) *plus* `content` (the answer). A long monologue can
        consume the whole output budget and leave `content` empty, so we fall
        back to parsing the JSON out of `reasoning_content`.
      * a short backoff retry turns any transient drift into a self-heal.
    """
    temperature = config.DECISION_LLM_TEMPERATURE if temperature is None else temperature
    max_tokens = config.DECISION_LLM_MAX_TOKENS if max_tokens is None else max_tokens

    last_err: Exception | None = None
    for attempt in range(3):
        payload: dict = {
            "model": config.DECISION_LLM_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        # json_object is broadly supported by OpenAI-compatible APIs, but a few
        # providers don't accept it — disable via DECISION_LLM_JSON_MODE=0.
        if config.DECISION_LLM_JSON_MODE:
            payload["response_format"] = {"type": "json_object"}
        try:
            data = _post(config.DECISION_LLM_BASE_URL, config.DECISION_LLM_API_KEY, payload)
            msg = data["choices"][0]["message"]
            content = msg.get("content") or ""
            if not content.strip():
                rc = msg.get("reasoning_content") or ""
                if not rc.strip():
                    raise ValueError("decision LLM returned empty content and empty reasoning")
                content = rc
            return _parse_json(content)
        except Exception as e:  # noqa: BLE001 — retry any transient decode/empty failure
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"decision LLM failed after 3 attempts: {last_err}")


def vision(image_b64: str, prompt: str, mime: str = "image/png") -> str:
    """Ask the vision LLM to *understand* a screenshot. Returns the answer text.

    Provider-agnostic: endpoint / model / params come from `VISION_LLM_*`.
    Reasoning models put the final answer in `content` and the monologue in
    `reasoning_content`; non-reasoning models just have `content` — so prefer
    `content` and fall back to `reasoning_content`.
    """
    payload = {
        "model": config.VISION_LLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": config.VISION_LLM_TEMPERATURE,
        "max_tokens": config.VISION_LLM_MAX_TOKENS,
    }
    data = _post(config.VISION_LLM_BASE_URL, config.VISION_LLM_API_KEY, payload)
    msg = data["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning_content") or ""
