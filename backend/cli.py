"""
CLI entry-points for the GitHub Action and any ad-hoc scripts.

Usage:
    python -m backend.cli apply-to-portkey
    python -m backend.cli reconcile-from-portkey

Exit code is 0 when reconcile succeeds (regardless of whether anything
changed). Non-zero only on uncaught exceptions, so the workflow fails loudly
on real errors but ignores no-op runs.
"""

from __future__ import annotations

import sys

from backend import reconciler


def _print_counts(name: str, counts: dict) -> None:
    print(f"[cli] {name}: {counts}")


def cmd_apply_to_portkey() -> int:
    counts = reconciler.reconcile_to_portkey()
    _print_counts("apply-to-portkey", counts)
    return 0


def cmd_reconcile_from_portkey() -> int:
    counts = reconciler.reconcile_from_portkey()
    _print_counts("reconcile-from-portkey", counts)
    return 0


def cmd_delete_on_portkey() -> int:
    """Delete one or more template_ids on Portkey.

    Usage: python -m backend.cli delete-on-portkey <tid1> [<tid2> ...]
    """
    from backend.portkey_client import delete_prompt_on_portkey
    tids = sys.argv[2:]
    if not tids:
        print("[cli] No template_ids provided.")
        return 0
    failed = 0
    for tid in tids:
        if not delete_prompt_on_portkey(tid):
            failed += 1
    print(f"[cli] delete-on-portkey: requested={len(tids)} failed={failed}")
    return 0


COMMANDS = {
    "apply-to-portkey": cmd_apply_to_portkey,
    "reconcile-from-portkey": cmd_reconcile_from_portkey,
    "delete-on-portkey": cmd_delete_on_portkey,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python -m backend.cli <command>")
        print("Commands: " + ", ".join(COMMANDS))
        return 2
    return COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    sys.exit(main())
