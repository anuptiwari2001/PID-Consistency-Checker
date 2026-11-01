"""Local LLM helpers (Ollama). Free, offline, sized for a 4 GB GPU.

The LLM never decides a finding. It only (a) reads a free-text note and suggests a nozzle
status, and (b) optionally writes a short explanation. The rules engine stays deterministic.
"""
from __future__ import annotations
import json
import os
import re
import urllib.request
import urllib.error

DEFAULT_MODEL = os.environ.get("PID_LLM_MODEL", "qwen2.5:3b-instruct-q4_K_M")
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
STATUSES = ["active", "spare", "plugged", "future", "vent", "drain", "unused", "unknown"]

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": STATUSES},
        "evidence": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["status", "evidence", "confidence"],
}

SYSTEM = (
    "You read short engineering notes about one P&ID equipment nozzle and classify its status. "
    "Answer with JSON only. status must be one of: active, spare, plugged, future, vent, drain, "
    "unused, unknown. evidence must be an exact substring copied from the note. "
    "If the note does not state the nozzle status, use unknown with empty evidence."
)
SHOTS = [
    ("N2 SPARE, BLIND FLANGED", {"status": "spare", "evidence": "SPARE", "confidence": "high"}),
    ("Reserved for future tie-in by others", {"status": "future", "evidence": "future tie-in", "confidence": "high"}),
    ("Connect to suction line per rev C", {"status": "active", "evidence": "Connect to suction line", "confidence": "medium"}),
    ("See vendor drawing", {"status": "unknown", "evidence": "", "confidence": "low"}),
]

# ordered: first match wins (spare beats plugged for "SPARE, BLIND FLANGED")
HEURISTICS = [
    ("spare", r"\bspare\b"),
    ("plugged", r"\b(plugged|plug|blind(ed)?|blind flange(d)?|blanked|capped)\b"),
    ("future", r"\b(future|reserved|provision(al)? for)\b"),
    ("unused", r"\b(not used|unused|not required|n\.?u\.?)\b"),
    ("vent", r"\bvent\b"),
    ("drain", r"\bdrain\b"),
]


def heuristic_status(text: str) -> dict:
    t = text or ""
    for status, pat in HEURISTICS:
        m = re.search(pat, t, re.I)
        if m:
            return {"status": status, "evidence": m.group(0), "confidence": "medium", "source": "heuristic"}
    return {"status": "unknown", "evidence": "", "confidence": "low", "source": "heuristic"}


class Ollama:
    def __init__(self, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST, timeout: float = 120):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=2) as r:
                names = [m.get("name", "") for m in json.load(r).get("models", [])]
            base = self.model.split(":")[0]
            return any(n == self.model or n.split(":")[0] == base for n in names)
        except Exception:
            return False

    def chat(self, messages: list, schema: dict | None = None, num_predict: int = 160) -> str:
        body = {
            "model": self.model, "messages": messages, "stream": False, "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": 2048, "num_predict": num_predict},
        }
        if schema:
            body["format"] = schema
        req = urllib.request.Request(self.host + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["message"]["content"]


class NoteExtractor:
    """mode: 'off' = heuristics only, 'auto' = use Ollama if reachable, 'on' = require Ollama."""

    def __init__(self, mode: str = "auto", model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST,
                 cache_path: str | None = ".pid_llm_cache.json"):
        self.mode = mode
        self.client = Ollama(model, host)
        self.cache_path = cache_path
        self.cache = {}
        if cache_path and os.path.exists(cache_path):
            try:
                self.cache = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                self.cache = {}
        self.use_llm = False
        if mode != "off":
            self.use_llm = self.client.available()
            if mode == "on" and not self.use_llm:
                raise RuntimeError(f"Ollama model '{model}' not reachable at {host}. "
                                   f"Run: ollama pull {model}")
        self.calls = 0

    def _save(self):
        if self.cache_path:
            try:
                json.dump(self.cache, open(self.cache_path, "w", encoding="utf-8"), indent=1)
            except OSError:
                pass

    def classify(self, note: str) -> dict:
        note = (note or "").strip()
        if not note:
            return {"status": "unknown", "evidence": "", "confidence": "low", "source": "none"}
        h = heuristic_status(note)
        if h["status"] != "unknown" or not self.use_llm:
            return h                                   # cheap path first: no GPU call needed
        key = "status::" + self.client.model + "::" + note
        if key in self.cache:
            return self.cache[key]
        msgs = [{"role": "system", "content": SYSTEM}]
        for q, a in SHOTS:
            msgs += [{"role": "user", "content": "Note: " + q}, {"role": "assistant", "content": json.dumps(a)}]
        msgs.append({"role": "user", "content": "Note: " + note})
        try:
            self.calls += 1
            data = json.loads(self.client.chat(msgs, SCHEMA))
            status = data.get("status", "unknown")
            ev = (data.get("evidence") or "").strip()
            conf = data.get("confidence", "low")
            # anti-hallucination guard: evidence must really be in the note
            if status not in STATUSES or (status != "unknown" and (not ev or ev.lower() not in note.lower())):
                status, ev, conf = "unknown", "", "low"
            out = {"status": status, "evidence": ev, "confidence": conf, "source": "llm:" + self.client.model}
        except Exception as e:                         # model busy / OOM / timeout: degrade gracefully
            out = {**h, "source": f"heuristic (llm error: {type(e).__name__})"}
        self.cache[key] = out
        self._save()
        return out

    def explain(self, facts: str) -> str:
        if not self.use_llm:
            return ""
        key = "explain::" + self.client.model + "::" + facts
        if key in self.cache:
            return self.cache[key]
        msgs = [
            {"role": "system", "content": "You help a P&ID designer. Using ONLY the facts given, write at most two "
             "plain sentences saying why this finding matters and what to check on the drawing. "
             "Do not invent tags, standards or equipment."},
            {"role": "user", "content": facts},
        ]
        try:
            self.calls += 1
            text = self.client.chat(msgs, None, num_predict=110).strip()
        except Exception:
            return ""
        self.cache[key] = text
        self._save()
        return text
