"""
GitHub Writer – commit prompts.json to the repo via the GitHub Contents API.

Why an HTTP API and not `git push`?
  The Streamlit Cloud container has no git binary, no SSH key, no credential
  helper. It does have outbound HTTPS. The Contents API lets us write a file
  + create a commit + push, all in one PUT call, authenticated by a PAT.

Two functions:
  - get_file_sha(path)    → current blob SHA (needed by the API to PUT an update)
  - commit_file(path, content, message) → write + commit + push in one shot

If GITHUB_TOKEN / GITHUB_REPO are unset, both functions no-op silently. This
keeps local dev (where the user might use plain git) and CI environments
unaffected.
"""

from __future__ import annotations

import base64
from typing import Optional

import requests

from config.settings import GITHUB_BRANCH, GITHUB_REPO, GITHUB_TOKEN

API_BASE = "https://api.github.com"


def _enabled() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_file_sha(path: str = "prompts.json", branch: Optional[str] = None) -> Optional[str]:
    """Return the current SHA of the file on the branch, or None if missing."""
    if not _enabled():
        return None
    branch = branch or GITHUB_BRANCH
    try:
        resp = requests.get(
            f"{API_BASE}/repos/{GITHUB_REPO}/contents/{path}",
            headers=_headers(),
            params={"ref": branch},
            timeout=15,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("sha")
    except requests.RequestException as exc:
        print(f"[github] Failed to read {path}: {exc}")
        return None


def commit_file(
    content: str,
    message: str,
    path: str = "prompts.json",
    branch: Optional[str] = None,
) -> bool:
    """
    PUT new content to the repo. Creates a commit on `branch`.
    Returns True on success, False on any failure (logged to stdout).

    No-op (returns False) when GITHUB_TOKEN/GITHUB_REPO aren't configured.
    """
    if not _enabled():
        print("[github] commit_file skipped: GITHUB_TOKEN or GITHUB_REPO not set")
        return False

    branch = branch or GITHUB_BRANCH
    sha = get_file_sha(path, branch)

    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha   # required to update an existing file

    try:
        resp = requests.put(
            f"{API_BASE}/repos/{GITHUB_REPO}/contents/{path}",
            headers=_headers(),
            json=body,
            timeout=20,
        )
        resp.raise_for_status()
        commit_sha = resp.json().get("commit", {}).get("sha", "")[:7]
        print(f"[github] Committed {path} on {branch} ({commit_sha}) – {message!r}")
        return True
    except requests.RequestException as exc:
        body_txt = exc.response.text if getattr(exc, "response", None) is not None else ""
        print(f"[github] commit_file failed: {exc}  body={body_txt[:200]}")
        return False
