"""LLM client — DeepSeek (decision) and Kimi K3 (vision), both OpenAI-compatible.

One thin httpx helper for both endpoints; no third-party SDK needed.
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


def deepseek_decide(messages: list, temperature: float = 0.2, max_tokens: int = 8192) -> dict:
    """Call DeepSeek for a structured decision. Returns a parsed JSON dict.

    deepseek-v4-pro is a *reasoning* model: it emits `reasoning_content` (the
    monologue) plus `content` (the answer). The monologue can be long — on a
    complex page with accumulated history it can consume the entire output
    budget, leaving `content` empty. Two mitigations:
      1. a generous output budget (8192) so the monologue rarely exhausts it;
      2. when `content` is empty, fall back to parsing the JSON out of
         `reasoning_content` (which usually still contains it).
    Retries with a short backoff turn any residual drift into a self-heal.
    """
    last_err: Exception | None = None
    for attempt in range(3):
        payload = {
            "model": config.DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            data = _post(config.DEEPSEEK_BASE_URL, config.DEEPSEEK_API_KEY, payload)
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


def kimi_vision(image_b64: str, prompt: str, mime: str = "image/png") -> str:
    """Call Kimi K3 to *understand* a screenshot. Returns the final answer text."""
    payload = {
        "model": config.KIMI_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        # kimi-k3 is a reasoning model: it only accepts temperature=1, and its
        # reasoning monologue can consume most of the budget, so leave headroom
        # for the final `content` answer (image CAPTCHAs especially).
        "temperature": 1,
        "max_tokens": 8192,
    }
    data = _post(config.KIMI_BASE_URL, config.KIMI_API_KEY, payload)
    msg = data["choices"][0]["message"]
    # K3 is a reasoning model: content holds the final answer, reasoning_content the monologue.
    return msg.get("content") or msg.get("reasoning_content") or ""
