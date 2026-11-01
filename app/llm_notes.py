"""Optional local LLM (Ollama / Qwen2.5) note extractor. Never calls a paid API."""
from __future__ import annotations

import json
import re

import httpx

from . import config

_resolved: str | None = None

SYSTEM = (
    "You extract engineering requirements from P&ID general notes. "
    "Reply with JSON only, shape: "
    '{"notes":[{"text":str,"category":str,"requirement":str,"tags":[str]}]}. '
    "category is one of: piping, instrument, equipment, safety, construction, other. "
    "tags are equipment/instrument/line tags literally mentioned in that note. "
    "Do not invent anything not in the text."
)


def status() -> dict:
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return {"reachable": True, "models": models, "selected": pick_model(models)}
    except Exception as e:
        return {"reachable": False, "error": str(e), "models": [], "selected": None}


def pick_model(models: list[str]) -> str | None:
    if config.OLLAMA_MODEL in models:
        return config.OLLAMA_MODEL
    for m in models:
        if m.lower().startswith("qwen2.5"):
            return m
    return models[0] if models else None


def extract_notes(notes: list[str]) -> list[dict]:
    """Returns structured notes. Raises on failure so the caller can fall back."""
    st = status()
    if not st["reachable"] or not st["selected"]:
        raise RuntimeError(f"Ollama not usable: {st.get('error') or 'no model installed'}")
    text = "\n".join(notes)[:5000]
    payload = {
        "model": st["selected"],
        "stream": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": config.OLLAMA_NUM_CTX},
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
    }
    r = httpx.post(f"{config.OLLAMA_URL}/api/chat", json=payload, timeout=config.OLLAMA_TIMEOUT)
    r.raise_for_status()
    content = r.json()["message"]["content"]
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    data = json.loads(content)
    out = []
    for n in data.get("notes", []):
        if isinstance(n, dict) and n.get("text"):
            out.append({
                "text": str(n.get("text", "")),
                "category": str(n.get("category", "other")),
                "requirement": str(n.get("requirement", "")),
                "tags": [str(t).upper() for t in n.get("tags", []) if isinstance(t, str)],
            })
    return out
