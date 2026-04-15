"""
Diagnostic script v2 – discovers virtual keys and tests Create Prompt.

Usage:
    python debug_portkey_api.py
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv
import requests

load_dotenv(Path(__file__).parent / ".env")

API_KEY = os.getenv("PORTKEY_API_KEY", "")
BASE = "https://api.portkey.ai/v1"
HEADERS = {
    "x-portkey-api-key": API_KEY,
    "Content-Type": "application/json",
}

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")

coll_id = None
vk_id = None

# ── 1. List collections ──────────────────────────────────────────────────────
section("1. LIST COLLECTIONS")
try:
    r = requests.get(f"{BASE}/collections", headers=HEADERS, timeout=15)
    print(f"  Status: {r.status_code}")
    data = r.json()
    collections = data.get("data", []) if isinstance(data, dict) else data
    for c in collections:
        print(f"    id={c.get('id')}  name={c.get('name')}  is_default={c.get('is_default')}")
    if collections:
        coll_id = collections[0].get("id")
        print(f"\n  >>> Using collection_id: {coll_id}")
except Exception as e:
    print(f"  Error: {e}")


# ── 2. List virtual keys ─────────────────────────────────────────────────────
section("2. LIST VIRTUAL KEYS")
try:
    r = requests.get(f"{BASE}/virtual-keys", headers=HEADERS, timeout=15)
    print(f"  Status: {r.status_code}")
    data = r.json()
    print(f"  Full response:\n{json.dumps(data, indent=2)[:3000]}")
    vk_list = data.get("data", []) if isinstance(data, dict) else data
    if vk_list:
        vk_id = vk_list[0].get("slug") or vk_list[0].get("id")
        print(f"\n  >>> Using virtual_key: {vk_id}")
except Exception as e:
    print(f"  Error: {e}")


# ── 3. Retrieve the existing prompt to see its full structure ─────────────────
section("3. GET EXISTING PROMPT DETAILS")
try:
    r = requests.get(f"{BASE}/prompts", headers=HEADERS, timeout=15)
    data = r.json()
    prompts = data.get("data", []) if isinstance(data, dict) else data
    if prompts:
        pid = prompts[0].get("id")
        # Get full prompt detail
        r2 = requests.get(f"{BASE}/prompts/{pid}", headers=HEADERS, timeout=15)
        print(f"  Status: {r2.status_code}")
        print(f"  Full prompt object:\n{json.dumps(r2.json(), indent=2)[:4000]}")
except Exception as e:
    print(f"  Error: {e}")


# ── 4. Create prompt WITH virtual_key + collection_id ─────────────────────────
section("4. CREATE PROMPT (with virtual_key + collection_id)")
payload = {
    "name": "test-from-script",
    "string": json.dumps([
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello from debug script"},
    ]),
    "model": "gemini-2.5-flash",
    "collection_id": coll_id,
    "parameters": {},
}
if vk_id:
    payload["virtual_key"] = vk_id

print(f"  Payload:\n{json.dumps(payload, indent=2)}")
try:
    r = requests.post(f"{BASE}/prompts", headers=HEADERS, json=payload, timeout=15)
    print(f"\n  Status: {r.status_code}")
    print(f"  Response:\n{json.dumps(r.json(), indent=2)[:2000]}")
except Exception as e:
    print(f"  Error: {e}")


print(f"\n{'='*60}")
print("  DONE – share this output to fix the code.")
print(f"{'='*60}")
