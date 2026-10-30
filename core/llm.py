"""
Gemini Client Wrapper with Retries, Caching, JSON Parsing & Repair.
Uses the official google-genai SDK.
Handles 429/5xx errors with exponential backoff and timeouts.
"""

import os
import re
import json
import time
import hashlib
from typing import Any, Dict, Optional, Tuple, Union
from google import genai
from google.genai import types
from google.genai.errors import APIError


# In-memory query cache to preserve free quota across runs
_RESPONSE_CACHE: Dict[str, Any] = {}


def get_api_key() -> str:
    """
    Retrieve Gemini API key with resolution order:
    1. Streamlit secrets
    2. Environment variable GEMINI_API_KEY
    3. Environment variable ANTHROPIC_API_KEY (fallback if user placed it there)
    """
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "GEMINI_API_KEY" in st.secrets:
            key = st.secrets["GEMINI_API_KEY"]
            if key and not key.startswith("AIzaSyYour"):
                return key.strip()
    except Exception:
        pass

    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key and not env_key.startswith("AIzaSyYour"):
        return env_key.strip()

    fallback_key = os.environ.get("ANTHROPIC_API_KEY")
    if fallback_key and fallback_key.startswith("AIzaSy"):
        return fallback_key.strip()

    return ""


def get_model_name() -> str:
    """
    Retrieve model name with fallback to standard free-tier flash model.
    Default: 'gemini-3.5-flash-lite'
    """
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "GEMINI_MODEL" in st.secrets:
            return st.secrets["GEMINI_MODEL"].strip()
    except Exception:
        pass

    return os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()


def _cache_key(prompt: str, model: str, temperature: float) -> str:
    payload = f"{model}:{temperature}:{prompt}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def repair_and_parse_json(raw_text: str) -> Any:
    """
    Robust JSON parser that strips markdown code blocks,
    extracts JSON arrays/objects, removes trailing commas,
    and returns parsed Python objects.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Empty response received from LLM")

    cleaned = raw_text.strip()

    # 1. Strip markdown fences if present
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # 2. Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Extract array [...] or object {...} using regex
    array_match = re.search(r"(\[[\s\S]*\])", cleaned)
    if array_match:
        try:
            return json.loads(array_match.group(1))
        except json.JSONDecodeError:
            cleaned_candidate = array_match.group(1)
        else:
            cleaned_candidate = cleaned
    else:
        obj_match = re.search(r"(\{[\s\S]*\})", cleaned)
        if obj_match:
            try:
                return json.loads(obj_match.group(1))
            except json.JSONDecodeError:
                cleaned_candidate = obj_match.group(1)
            else:
                cleaned_candidate = cleaned
        else:
            cleaned_candidate = cleaned

    # 4. Remove trailing commas in arrays/objects: , ] -> ] and , } -> }
    cleaned_candidate = re.sub(r",\s*(\]|})", r"\1", cleaned_candidate)

    try:
        return json.loads(cleaned_candidate)
    except json.JSONDecodeError as err:
        raise ValueError(
            f"Failed to parse LLM response into valid JSON: {err}. Raw excerpt: {raw_text[:200]}"
        ) from err


def call_gemini(
    prompt: str,
    temperature: float = 0.1,
    max_retries: int = 3,
    force_json: bool = True,
    use_cache: bool = True
) -> Tuple[Any, Dict[str, Any]]:
    """
    Execute a Gemini API call using google-genai SDK with exponential backoff and caching.
    Returns:
        (parsed_output, telemetry_dict)
    """
    api_key = get_api_key()
    if not api_key:
        raise ValueError(
            "Gemini API Key is missing. Please set GEMINI_API_KEY in .streamlit/secrets.toml or your environment."
        )

    model_name = get_model_name()
    c_key = _cache_key(prompt, model_name, temperature)

    if use_cache and c_key in _RESPONSE_CACHE:
        cached_result, cached_telemetry = _RESPONSE_CACHE[c_key]
        telemetry = dict(cached_telemetry)
        telemetry["cached"] = True
        return cached_result, telemetry

    client = genai.Client(api_key=api_key)

    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json" if force_json else "text/plain"
    )

    last_exception = None
    delay = 1.5

    for attempt in range(1, max_retries + 1):
        start_time = time.time()
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config
            )

            latency = round(time.time() - start_time, 3)
            raw_text = response.text or ""

            if force_json:
                parsed = repair_and_parse_json(raw_text)
            else:
                parsed = raw_text

            telemetry = {
                "model": model_name,
                "latency_seconds": latency,
                "attempts": attempt,
                "cached": False,
                "raw_length": len(raw_text)
            }

            if use_cache:
                _RESPONSE_CACHE[c_key] = (parsed, telemetry)

            return parsed, telemetry

        except APIError as api_err:
            last_exception = api_err
            # Rate limit or quota exhausted (429) or transient 5xx server error
            err_msg = str(api_err).lower()
            if "429" in err_msg or "resource_exhausted" in err_msg or "503" in err_msg:
                time.sleep(delay)
                delay *= 2
            else:
                raise RuntimeError(f"Gemini API Error: {api_err}") from api_err
        except Exception as e:
            last_exception = e
            time.sleep(delay)
            delay *= 2

    raise RuntimeError(
        f"Gemini API request failed after {max_retries} attempts: {last_exception}"
    )


def clear_llm_cache():
    """Clear in-memory response cache."""
    _RESPONSE_CACHE.clear()

