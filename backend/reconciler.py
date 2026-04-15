"""
Reconciler – the brain that diffs Portkey vs local prompts.json and applies
changes in either direction.

Two top-level entry points:
    reconcile_from_portkey() : pull remote state into prompts.json
    reconcile_to_portkey()   : push local changes to Portkey

Both are idempotent: running them when there's nothing to do is a no-op
(returns zero counts, doesn't write the file).

Drift detection key: `_hash` on each record (sha1 of system_prompt + provider).
This is what stops the cron and the push-action from looping forever — if the
hash matches, neither side considers a change pending.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from backend import portkey_client as pk
from backend.prompt_manager import (
    SCHEMA_VERSION,
    _read_file,
    _write_file,
    _ver_key,
    compute_hash,
)


# ── Fetch & normalize Portkey state into v2 records ─────────────────────────


def fetch_portkey_state() -> list[dict]:
    """
    Pull all Portkey prompts/versions and return them as v2-shaped records.
    Drops user_prompt/response since v2 doesn't persist runtime fields.
    """
    raw = pk.fetch_portkey_prompts()
    out: list[dict] = []
    for r in raw:
        sp = r.get("system_prompt", "") or ""
        provider = r.get("provider", "") or ""
        out.append({
            "template_id":  r.get("template_id"),
            "version":      _ver_key(r.get("version")),
            "name":         r.get("name") or "",
            "is_production": bool(r.get("is_default")),
            "timestamp":    r.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "system_prompt": sp,
            "provider":     provider,
            "last_origin":  "portkey",
            "_hash":        compute_hash(sp, provider),
        })
    return out


# ── Portkey → local ─────────────────────────────────────────────────────────


def diff_portkey_to_local() -> dict:
    """
    Returns {'add': [...], 'update': [...], 'delete': [...]} listing the
    Portkey records that need to be reflected into prompts.json.
    """
    remote = fetch_portkey_state()
    local = _read_file().get("prompts", [])

    remote_keys = {
        (r["template_id"], r["version"]): r
        for r in remote if r["template_id"]
    }
    local_by_key = {
        (p.get("template_id"), _ver_key(p.get("version"))): p
        for p in local if p.get("template_id")
    }
    remote_template_ids = {r["template_id"] for r in remote if r["template_id"]}

    add, update, delete = [], [], []
    for key, r in remote_keys.items():
        if key not in local_by_key:
            add.append(r)
        else:
            l = local_by_key[key]
            # Production flag is locally-owned for now (Portkey label sync TBD),
            # so only consider content drift here.
            if l.get("_hash") != r["_hash"]:
                update.append(r)
    for key, l in local_by_key.items():
        tid, _ = key
        if tid not in remote_template_ids:
            delete.append(l)
    return {"add": add, "update": update, "delete": delete}


def reconcile_from_portkey() -> dict:
    """Apply Portkey → local. Writes prompts.json only if something changed."""
    diff = diff_portkey_to_local()
    if not (diff["add"] or diff["update"] or diff["delete"]):
        return {"added": 0, "updated": 0, "deleted": 0}

    data = _read_file()

    # Deletes: drop every local record whose template_id no longer exists upstream.
    if diff["delete"]:
        dead_tids = {d.get("template_id") for d in diff["delete"]}
        data["prompts"] = [
            p for p in data["prompts"]
            if p.get("template_id") not in dead_tids
        ]

    # Updates: refresh content fields; preserve local id.
    update_map = {
        (u["template_id"], u["version"]): u for u in diff["update"]
    }
    for p in data["prompts"]:
        key = (p.get("template_id"), _ver_key(p.get("version")))
        if key in update_map:
            u = update_map[key]
            # Note: is_production is locally-owned and intentionally NOT
            # overwritten from Portkey here.
            for f in ("name", "system_prompt", "provider", "timestamp", "_hash"):
                p[f] = u[f]
            p["last_origin"] = "portkey"

    # Adds: brand new records get a fresh local id.
    for r in diff["add"]:
        rec = dict(r)
        rec["id"] = str(uuid.uuid4())
        data["prompts"].append(rec)

    data["prompts"].sort(key=lambda p: p.get("timestamp", ""))
    data["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    data["version"] = SCHEMA_VERSION
    _write_file(data)
    return {
        "added": len(diff["add"]),
        "updated": len(diff["update"]),
        "deleted": len(diff["delete"]),
    }


# ── Local → Portkey ─────────────────────────────────────────────────────────


def diff_local_to_portkey() -> dict:
    """
    Returns {'create': [...], 'update': [...]} of local records that should
    be pushed to Portkey. Only the LATEST version of each template_id is
    considered (older versions are already historical on Portkey).
    """
    remote = fetch_portkey_state()
    local = _read_file().get("prompts", [])

    # Latest local per template_id; loose records (no template_id) → create.
    latest_local: dict[str, dict] = {}
    no_tid_records: list[dict] = []
    for p in local:
        tid = p.get("template_id")
        if not tid:
            no_tid_records.append(p)
            continue
        ver = _ver_key(p.get("version"))
        cur = latest_local.get(tid)
        if cur is None or ver > _ver_key(cur.get("version")):
            latest_local[tid] = p

    # Latest remote per template_id.
    latest_remote: dict[str, dict] = {}
    for r in remote:
        tid = r["template_id"]
        cur = latest_remote.get(tid)
        if cur is None or r["version"] > cur["version"]:
            latest_remote[tid] = r

    create = list(no_tid_records)
    update = []
    for tid, lp in latest_local.items():
        rp = latest_remote.get(tid)
        if rp is None:
            # template_id exists locally but not upstream — most likely the
            # remote prompt was deleted; skip to avoid resurrecting it.
            continue
        if lp.get("_hash") != rp["_hash"]:
            update.append(lp)
    return {"create": create, "update": update}


def reconcile_to_portkey() -> dict:
    """
    Push local-only or content-drifted records to Portkey, then write the
    Portkey-assigned ids/versions back into prompts.json.
    """
    diff = diff_local_to_portkey()
    if not (diff["create"] or diff["update"]):
        return {"created": 0, "updated": 0, "failed": 0}

    data = _read_file()
    by_id = {p["id"]: p for p in data["prompts"]}

    created, updated, failed = 0, 0, 0

    for p in diff["create"]:
        result = pk.push_prompt_to_portkey(
            system_prompt=p.get("system_prompt", ""),
            user_prompt="",  # v2: not part of the stored template
            provider=p.get("provider", ""),
            name=p.get("name"),
        )
        if result and result.get("id"):
            local = by_id[p["id"]]
            local["template_id"] = result["id"]
            local["version"] = _ver_key(result.get("version") or 1)
            local["last_origin"] = "app"
            local["_hash"] = compute_hash(
                local.get("system_prompt", ""), local.get("provider", ""),
            )
            created += 1
        else:
            failed += 1

    for p in diff["update"]:
        result = pk.update_prompt_on_portkey(
            prompt_id=p["template_id"],
            system_prompt=p.get("system_prompt", ""),
            user_prompt="",
            provider=p.get("provider", ""),
        )
        if result and result.get("version"):
            local = by_id[p["id"]]
            # Portkey created a new version; reflect that locally.
            local["version"] = _ver_key(result["version"])
            local["last_origin"] = "app"
            local["_hash"] = compute_hash(
                local.get("system_prompt", ""), local.get("provider", ""),
            )
            updated += 1
        else:
            failed += 1

    data["last_synced_at"] = datetime.now(timezone.utc).isoformat()
    _write_file(data)
    return {"created": created, "updated": updated, "failed": failed}


# ── Convenience: run a full two-way reconcile ───────────────────────────────


def reconcile_both_ways() -> dict:
    """
    Pull from Portkey first (to ingest UI-side edits), then push local edits
    that aren't on Portkey yet. Order matters: we never want to push an old
    local state on top of a newer remote one.
    """
    pulled = reconcile_from_portkey()
    pushed = reconcile_to_portkey()
    return {"pulled": pulled, "pushed": pushed}
