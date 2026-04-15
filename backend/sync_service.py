"""
Sync Service – keeps prompts.json and Portkey prompts in bi-directional sync.

Run as a standalone script:
    python -m backend.sync_service

It will poll the Portkey Prompts API every SYNC_INTERVAL_SECONDS and:
  1. Pull any new prompts from Portkey → merge into prompts.json.
  2. Optionally auto-commit & push prompts.json to Git so the repo always
     reflects the latest state.

The polling interval and Git behaviour are controlled via .env:
  SYNC_INTERVAL_SECONDS  – default 60
  GIT_AUTO_COMMIT        – "true" to enable
  GIT_REMOTE / GIT_BRANCH – where to push
"""

import subprocess
import sys
import time
from datetime import datetime, timezone

import schedule  # lightweight scheduler

from config.settings import (
    GIT_AUTO_COMMIT,
    GIT_BRANCH,
    GIT_REMOTE,
    PROMPTS_FILE,
    SYNC_INTERVAL_SECONDS,
)
from backend.portkey_client import fetch_portkey_prompts
from backend.prompt_manager import (
    bulk_add_prompts,
    prune_missing_templates,
    update_sync_timestamp,
)


def git_commit_and_push(message: str = "") -> None:
    """Stage prompts.json, commit, and push if GIT_AUTO_COMMIT is enabled.

    Safe to call from anywhere (sync loop or Streamlit actions). Silently
    no-ops when GIT_AUTO_COMMIT is false or there is nothing to commit.
    """
    if not GIT_AUTO_COMMIT:
        return
    try:
        subprocess.run(
            ["git", "add", str(PROMPTS_FILE)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                message or f"[auto-sync] update prompts.json – {datetime.now(timezone.utc).isoformat()}",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "push", GIT_REMOTE, GIT_BRANCH],
            check=True,
            capture_output=True,
        )
        print("[sync] Committed and pushed prompts.json")
    except subprocess.CalledProcessError as exc:
        # Commit may fail if there are no changes – that's fine.
        print(f"[sync] Git operation skipped or failed: {exc}")


def sync_once() -> None:
    """
    Execute a single sync cycle:
      - Fetch prompts from Portkey.
      - Merge new ones into prompts.json.
      - Auto-commit to Git if configured.
    """
    print(f"[sync] Polling Portkey at {datetime.now(timezone.utc).isoformat()} …")

    portkey_prompts = fetch_portkey_prompts()
    if not portkey_prompts:
        print("[sync] No prompts returned from Portkey (or API error).")
        update_sync_timestamp()
        return

    added = bulk_add_prompts(portkey_prompts)

    # Remove local records for templates deleted on Portkey.
    remote_ids = {p["template_id"] for p in portkey_prompts if p.get("template_id")}
    removed = prune_missing_templates(remote_ids)

    update_sync_timestamp()
    print(
        f"[sync] Merged {added} new, pruned {removed} stale "
        f"prompt(s) from Portkey into prompts.json"
    )

    if added or removed:
        git_commit_and_push()


def _print_git_banner(context: str) -> None:
    status = "ON" if GIT_AUTO_COMMIT else "OFF"
    print(
        f"[{context}] git auto-commit: {status}  "
        f"(remote={GIT_REMOTE}, branch={GIT_BRANCH})"
    )


def run_scheduler() -> None:
    """Start the infinite polling loop."""
    print(
        f"[sync] Starting sync service – polling every "
        f"{SYNC_INTERVAL_SECONDS}s.  Press Ctrl+C to stop."
    )
    _print_git_banner("sync")

    # Run once immediately on startup.
    sync_once()

    schedule.every(SYNC_INTERVAL_SECONDS).seconds.do(sync_once)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[sync] Stopped.")
        sys.exit(0)


# ── Entry-point when executed as a module ────────────────────────────────────
if __name__ == "__main__":
    run_scheduler()
