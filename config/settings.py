"""
Centralised configuration loader.

Reads all settings from the .env file at the project root and exposes them
as plain Python constants so every other module can simply:

    from config.settings import PORTKEY_API_KEY, PROVIDER_API_KEYS, ...

Nothing else in the codebase should call os.getenv directly.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ── locate .env relative to this file (config/settings.py → project root) ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# On Streamlit Cloud the .env file doesn't exist; secrets come from st.secrets.
# Bridge them into os.environ so the rest of this module stays unchanged.
# Local runs are unaffected: setdefault never overwrites an existing value,
# and if st.secrets is unavailable/empty we just skip.
try:
    import streamlit as _st
    for _k, _v in _st.secrets.items():
        os.environ.setdefault(_k, str(_v))
except Exception:
    pass

# ── Portkey credentials ─────────────────────────────────────────────────────
PORTKEY_API_KEY: str = os.getenv("PORTKEY_API_KEY", "")

# Map each supported provider to its direct API key.
# Portkey uses the "provider" param to route the request and
# "Authorization" header to authenticate with the upstream LLM.

PROVIDER_API_KEYS: dict[str, str] = {"huggingface": os.getenv("HUGGINGFACE_API_KEY", ""),
                                     "google": os.getenv("GOOGLE_API_KEY", "")}

# The list of providers shown in the UI dropdown.
LLM_PROVIDERS: list[str] = list(PROVIDER_API_KEYS.keys())

# ── File paths ───────────────────────────────────────────────────────────────
PROMPTS_FILE: Path = PROJECT_ROOT / os.getenv("PROMPTS_FILE_PATH", "prompts.json")

# ── Sync configuration ──────────────────────────────────────────────────────
SYNC_INTERVAL_SECONDS: int = int(os.getenv("SYNC_INTERVAL_SECONDS", "60"))

# ── Git auto-commit (used by the sync script) ──────────────────────────────
GIT_AUTO_COMMIT: bool = os.getenv("GIT_AUTO_COMMIT", "false").lower() == "true"
GIT_REMOTE: str = os.getenv("GIT_REMOTE", "origin")
GIT_BRANCH: str = os.getenv("GIT_BRANCH", "main")

# ── App role ────────────────────────────────────────────────────────────────
# 'dev'  → full UI: New / Save / Save edit / Run buttons visible
# 'user' → read-only system prompts; only Run is available
APP_ROLE: str = os.getenv("APP_ROLE", "user").lower()

# ── GitHub (used by the app to commit prompts.json via Contents API) ────────
GITHUB_TOKEN:  str = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO:   str = os.getenv("GITHUB_REPO", "")     # e.g. "RoopamSadh/prompt-externalization"
GITHUB_BRANCH: str = os.getenv("GITHUB_BRANCH", "main")

# ── Portkey Admin API ────────────────────────────────────────────────────────
PORTKEY_BASE_URL: str = "https://api.portkey.ai/v1"

# Collection ID where prompts are stored (required by the Admin API).
PORTKEY_COLLECTION_ID: str = os.getenv("PORTKEY_COLLECTION_ID", "")

# Map each provider to its Portkey virtual key slug.
# The Admin API requires a virtual_key when creating prompts.
PROVIDER_VIRTUAL_KEYS: dict[str, str] = {
    "huggingface": os.getenv("PORTKEY_VK_HUGGINGFACE", ""),
    "google":      os.getenv("PORTKEY_VK_GOOGLE", ""),
}
