"""
Prompt Manager – read / write operations on prompts.json.

The file acts as the single source of truth on the Git side.  Every prompt
fired from the UI is appended here *before* the Portkey API call, so the
file always contains the latest history.

Schema of prompts.json:
{
  "prompts": [
    {
      "id": "<uuid>",
      "timestamp": "<ISO-8601>",
      "system_prompt": "...",
      "user_prompt": "...",
      "provider": "openai",
      "response": "...",
      "source": "frontend" | "portkey"
    }
  ],
  "last_synced_at": "<ISO-8601> | null",
  "version": "1.0"
}
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import PROMPTS_FILE


def _read_file(path: Path = PROMPTS_FILE) -> dict:
    """Load the prompts JSON file.  Creates a fresh one if missing or corrupted."""
    skeleton = {"prompts": [], "last_synced_at": None, "version": "1.0"}
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
    user_prompt: str,
    provider: str,
    response: Optional[str] = None,
    source: str = "frontend",
    template_id: Optional[str] = None,
    version: Optional[int] = None,
    name: Optional[str] = None,
    is_default: bool = False,
) -> dict:
    """
    Append a new prompt record and return it. `template_id` + `version`
    identify a Portkey prompt version; `id` remains a local UUID used for
    updating the local response after an LLM call.
    """
    record = {
        "id": str(uuid.uuid4()),
        "template_id": template_id,
        "version": _ver_key(version) if version is not None else None,
        "name": name,
        "is_default": is_default,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "provider": provider,
        "response": response,
        "source": source,
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


def mark_default_version(template_id: str, version: int) -> None:
    """Set is_default flag for the given version of a template, clearing peers."""
    data = _read_file()
    for p in data["prompts"]:
        if p.get("template_id") == template_id:
            p["is_default"] = (p.get("version") == version)
    _write_file(data)


def update_response(prompt_id: str, response: str) -> None:
    """Attach the LLM response to an existing prompt record by its id."""
    data = _read_file()
    for p in data["prompts"]:
        if p["id"] == prompt_id:
            p["response"] = response
            break
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
    Merge a batch of prompt records (typically from Portkey sync).

    Dedup key: (template_id, version) when both are present; falls back to
    local 'id' for legacy records. Also refreshes `is_default` on existing
    rows so default-pointer changes in Portkey are reflected locally.
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
        if tid and ver is not None:
            ver_int = _ver_key(ver)
            rec["version"] = ver_int  # normalize on ingest
            key = (tid, ver_int)
            if key in existing_by_tv:
                # Refresh metadata that may have changed on Portkey side;
                # keep local-only fields (id, response).
                existing = existing_by_tv[key]
                existing["is_default"] = rec.get("is_default", False)
                for field in ("provider", "name", "system_prompt", "user_prompt"):
                    new_val = rec.get(field)
                    if new_val:
                        existing[field] = new_val
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

    # Always persist (even if 0 added) so is_default updates are saved.
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
