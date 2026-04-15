"""
Prompt Manager – read / write operations on prompts.json.

This file is the canonical, git-tracked mirror of the Portkey prompt library.
Each record represents one VERSION of one system prompt.

Schema (v2):
{
  "prompts": [
    {
      "id":            "<local-uuid>",         # local stable identifier
      "template_id":   "<portkey-id|null>",    # null until Portkey assigns
      "version":       <int|null>,              # numeric Portkey version
      "name":          "string",
      "is_production": false,                   # carries Portkey 'production' label
      "timestamp":     "<ISO-8601>",
      "system_prompt": "string",                # the versioned artifact
      "provider":      "google",
      "last_origin":   "portkey | app | manual",
      "_hash":         "<sha1 of system_prompt + provider>"
    }
  ],
  "last_synced_at": "<ISO-8601>|null",
  "version": "2.0"
}

Runtime-only fields (user_prompt, response) are NOT stored — they belong to
the run context and would create commit noise.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import PROMPTS_FILE


SCHEMA_VERSION = "2.0"


def compute_hash(system_prompt: str, provider: str) -> str:
    """Stable content fingerprint used for drift detection between Portkey and git."""
    payload = f"{(system_prompt or '').strip()}|{(provider or '').strip().lower()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _read_file(path: Path = PROMPTS_FILE) -> dict:
    """Load the prompts JSON file.  Creates a fresh one if missing or corrupted."""
    skeleton = {"prompts": [], "last_synced_at": None, "version": SCHEMA_VERSION}
    if not path.exists():
        path.write_text(json.dumps(skeleton, indent=2))
        return skeleton
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        # File is corrupted – back it up and start fresh.
        backup = path.with_suffix(".json.bak")
        print(f"[prompt_manager] WARNING: {path} is corrupted ({exc}). "
              f"Backed up to {backup} and starting fresh.")
        path.rename(backup)
        path.write_text(json.dumps(skeleton, indent=2))
        return skeleton


def _write_file(data: dict, path: Path = PROMPTS_FILE) -> None:
    """Persist the full prompts dict back to disk."""
    path.write_text(json.dumps(data, indent=2, default=str))


# ── Public helpers ───────────────────────────────────────────────────────────


def _ver_key(v) -> int:
    """Coerce a version value (or record dict) to int for sort/compare."""
    raw = v.get("version") if isinstance(v, dict) else v
    try:
        return int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def add_prompt(
    system_prompt: str,
    provider: str,
    template_id: Optional[str] = None,
    version: Optional[int] = None,
    name: Optional[str] = None,
    is_production: bool = False,
    last_origin: str = "manual",
) -> dict:
    """
    Append a new system-prompt record (one Portkey version) and return it.

    `template_id` and `version` may be None on creation when the record was
    drafted manually without a Portkey id yet — the reconciler will fill them
    in on the next round-trip.
    """
    record = {
        "id": str(uuid.uuid4()),
        "template_id": template_id,
        "version": _ver_key(version) if version is not None else None,
        "name": name,
        "is_production": is_production,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system_prompt": system_prompt,
        "provider": provider,
        "last_origin": last_origin,
        "_hash": compute_hash(system_prompt, provider),
    }
    data = _read_file()
    data["prompts"].append(record)
    _write_file(data)
    return record


def get_templates() -> list[dict]:
    """
    Group prompts by template_id and return one entry per template with its
    latest version loaded. Records without a template_id (legacy) are each
    returned as their own single-version entry.
    """
    data = _read_file()
    groups: dict[str, list[dict]] = {}
    loose: list[dict] = []
    for p in data["prompts"]:
        tid = p.get("template_id")
        if tid:
            groups.setdefault(tid, []).append(p)
        else:
            loose.append(p)

    templates = []
    for tid, versions in groups.items():
        versions_sorted = sorted(versions, key=_ver_key)
        latest = versions_sorted[-1]
        templates.append({
            "template_id": tid,
            "name": latest.get("name") or "",
            "latest_version": latest.get("version"),
            "version_count": len(versions_sorted),
            "latest": latest,
            "versions": versions_sorted,
        })
    # Sort by latest timestamp, newest first.
    templates.sort(key=lambda t: t["latest"].get("timestamp", ""), reverse=True)

    # Append loose (legacy) records after grouped templates.
    for p in sorted(loose, key=lambda x: x.get("timestamp", ""), reverse=True):
        templates.append({
            "template_id": None,
            "name": "",
            "latest_version": None,
            "version_count": 1,
            "latest": p,
            "versions": [p],
        })
    return templates


def get_template_versions(template_id: str) -> list[dict]:
    """Return all local records for one template, oldest → newest."""
    data = _read_file()
    versions = [p for p in data["prompts"] if p.get("template_id") == template_id]
    return sorted(versions, key=_ver_key)


def mark_production_version(template_id: str, version: int) -> None:
    """Move the production label locally to the given version of a template."""
    data = _read_file()
    for p in data["prompts"]:
        if p.get("template_id") == template_id:
            p["is_production"] = (_ver_key(p.get("version")) == _ver_key(version))
    _write_file(data)


def get_recent_prompts(n: int = 5) -> list[dict]:
    """Return the *n* most recent prompts sorted by timestamp (newest first)."""
    data = _read_file()
    sorted_prompts = sorted(
        data["prompts"],
        key=lambda p: p.get("timestamp", ""),
        reverse=True,
    )
    return sorted_prompts[:n]


def get_all_prompts() -> list[dict]:
    """Return every prompt in chronological order."""
    return _read_file()["prompts"]


def prompt_exists(prompt_id: str) -> bool:
    """Check whether a prompt with the given id is already stored."""
    return any(p["id"] == prompt_id for p in _read_file()["prompts"])


def bulk_add_prompts(records: list[dict]) -> int:
    """
    Merge a batch of prompt records (typically from Portkey reconcile).

    Dedup key: (template_id, version). Refreshes content fields and the
    is_production flag on existing rows; preserves the local id.
    """
    data = _read_file()

    existing_by_tv: dict[tuple, dict] = {}
    existing_ids = set()
    for p in data["prompts"]:
        existing_ids.add(p.get("id"))
        tid, ver = p.get("template_id"), p.get("version")
        if tid and ver is not None:
            existing_by_tv[(tid, _ver_key(ver))] = p

    added = 0
    for rec in records:
        tid, ver = rec.get("template_id"), rec.get("version")
        # Always normalize: int version, recomputed hash, default last_origin.
        if ver is not None:
            rec["version"] = _ver_key(ver)
        rec["_hash"] = compute_hash(rec.get("system_prompt", ""), rec.get("provider", ""))
        rec.setdefault("last_origin", "portkey")

        if tid and rec.get("version") is not None:
            key = (tid, rec["version"])
            if key in existing_by_tv:
                existing = existing_by_tv[key]
                existing["is_production"] = rec.get("is_production", existing.get("is_production", False))
                for field in ("provider", "name", "system_prompt"):
                    new_val = rec.get(field)
                    if new_val:
                        existing[field] = new_val
                existing["_hash"] = compute_hash(
                    existing.get("system_prompt", ""),
                    existing.get("provider", ""),
                )
                existing["last_origin"] = rec["last_origin"]
                continue
            data["prompts"].append(rec)
            existing_by_tv[key] = rec
            existing_ids.add(rec.get("id"))
            added += 1
        else:
            if rec.get("id") in existing_ids:
                continue
            data["prompts"].append(rec)
            existing_ids.add(rec.get("id"))
            added += 1

    data["prompts"].sort(key=lambda p: p.get("timestamp", ""))
    _write_file(data)
    return added


def prune_missing_templates(remote_template_ids: set[str]) -> int:
    """
    Remove local records whose template_id is NOT in the remote set.

    Caller must only invoke this after a SUCCESSFUL fetch that returned at
    least one template — otherwise a transient API failure would wipe the
    library. Records without a template_id (legacy/loose) are kept.

    Returns the number of records removed.
    """
    if not remote_template_ids:
        return 0
    data = _read_file()
    before = len(data["prompts"])
    data["prompts"] = [
        p for p in data["prompts"]
        if not p.get("template_id") or p.get("template_id") in remote_template_ids
    ]
    removed = before - len(data["prompts"])
    if removed:
        _write_file(data)
    return removed


def update_sync_timestamp() -> None:
    """Stamp the file with the current UTC time after a successful sync."""
    data = _read_file()
    data["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    _write_file(data)
