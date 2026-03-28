from __future__ import annotations

import json
import os
import re
import ssl
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from gemini_config import GEMINI_API_KEY as CONFIG_GEMINI_API_KEY


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

KEY_FILE_CANDIDATES = [
    PROJECT_ROOT / "scripts" / ".gemini_api_key",
    PROJECT_ROOT / ".gemini_api_key",
]


def load_gemini_api_key(explicit_key: str = "") -> str:
    candidates = [
        explicit_key.strip(),
        os.getenv("GEMINI_API_KEY", "").strip(),
        str(CONFIG_GEMINI_API_KEY or "").strip(),
    ]
    for key in candidates:
        if key:
            return key

    for path in KEY_FILE_CANDIDATES:
        if not path.exists():
            continue
        value = path.read_text(encoding="utf-8").strip()
        value = re.sub(r"\s+", "", value)
        if value:
            return value

    return ""


def parse_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        pass

    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}

    try:
        parsed = json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_candidate_text(payload: Dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return ""

    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            return part["text"]
    return ""


def call_gemini_json(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.1,
    timeout: int = 90,
) -> Dict[str, Any]:
    model_name = quote(str(model or "").strip(), safe="")
    key_q = quote(str(api_key or "").strip(), safe="")
    url = f"{GEMINI_BASE_URL}/{model_name}:generateContent?key={key_q}"

    payload = {
        "system_instruction": {
            "parts": [{"text": system_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "temperature": float(temperature),
            "responseMimeType": "application/json",
        },
    }

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    ssl_context = _build_ssl_context()

    try:
        with urlopen(request, timeout=timeout, context=ssl_context) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Gemini API HTTP {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Gemini API connection error: {exc.reason}") from exc

    text = _extract_candidate_text(body)
    parsed = parse_json_object(text)
    if not parsed:
        raise RuntimeError("Could not parse JSON object from Gemini response.")

    return parsed


def _build_ssl_context() -> ssl.SSLContext:
    """
    Prefer certifi CA bundle when available to avoid local trust-store issues.
    """
    try:
        import certifi  # type: ignore

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()
