import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PID_DATA_DIR", BASE_DIR / "data"))
RULES_FILE = Path(os.getenv("PID_RULES", BASE_DIR / "rules.yaml"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
# Preferred model. If it is not installed, the first installed qwen2.5* model is used.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "300"))
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))  # keep small for 4 GB VRAM

DATA_DIR.mkdir(parents=True, exist_ok=True)
