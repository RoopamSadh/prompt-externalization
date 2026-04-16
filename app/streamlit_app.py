"""
Streamlit Frontend – Prompt Externalization (v2, GitHub-as-glue architecture)

Three-pane layout, role-aware:

  LEFT SIDEBAR          MIDDLE (editor / runner)        RIGHT (details)
  ─────────────         ──────────────────────────       ─────────────────
  • Prompt 1            System Prompt (RO for users)    Selected template
  • Prompt 2            User Prompt (always editable)   Version radio
  • Prompt 3            [Run]  +dev: [Save] [Save edit]  Metadata

Roles (config.settings.APP_ROLE):
  • "dev"  → full UI (New / Save / Save edit / Run)
  • "user" → read-only system prompts; only Run

Save / Save edit (dev only):
  1. add_prompt() locally
  2. reconciler.reconcile_to_portkey()    → push, capture template_id + version
  3. github_writer.commit_file()          → commit prompts.json to repo

Auto-refresh + manual Sync button both call reconcile_from_portkey() and,
if anything changed, commit the updated prompts.json to GitHub.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config.settings import APP_ROLE, LLM_PROVIDERS, PROMPTS_FILE
from backend.prompt_manager import (
    add_prompt,
    delete_template,
    get_templates,
    get_template_versions,
)
from backend.portkey_client import (
    send_prompt,
    push_prompt_to_portkey,
    update_prompt_on_portkey,
    delete_prompt_on_portkey,
)
from backend import reconciler
from backend.github_writer import commit_file, _enabled as _github_enabled
from config.settings import GITHUB_REPO as _GH_REPO, GITHUB_TOKEN as _GH_TOKEN

# ── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Portkey Prompt Externalization",
    page_icon="🔗",
    layout="wide",
)

IS_DEV = APP_ROLE == "dev"

# ── CSS ──────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    section[data-testid="stSidebar"] button[kind="tertiary"],
    section[data-testid="stSidebar"] button[kind="secondary"] {
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 2px 8px !important;
        min-height: 0 !important;
        line-height: 1.25 !important;
        border: none !important;
        background: transparent !important;
        color: #1f4e99 !important;
        font-weight: 400 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0.15rem !important;
    }
    section[data-testid="stSidebar"] .stButton { margin-bottom: 0 !important; }
    .selected-link button {
        background: #eef2f7 !important;
        font-weight: 600 !important;
    }
    .muted { color: #888; font-size: 0.8rem; }

    /* Sidebar pinned status box */
    section[data-testid="stSidebar"] > div { padding-bottom: 6rem; }
    .sidebar-status {
        position: fixed;
        bottom: 0.75rem;
        left: 0.75rem;
        width: calc(var(--sidebar-width, 320px) - 1.5rem);
        max-width: 280px;
        background: #f8f9fb;
        border: 1px solid #e2e6ed;
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 0.78rem;
        color: #4a4a4a;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .sidebar-status .row {
        display: flex;
        gap: 6px;
        align-items: baseline;
        margin: 2px 0;
    }
    .sidebar-status code {
        font-size: 0.72rem;
        background: transparent;
        padding: 0;
        color: #1f4e99;
    }
    .sidebar-status .muted-status { color: #888; }
    .sidebar-status .muted-status span:last-child {
        color: #444;
        font-weight: 500;
        word-break: break-word;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Session state ───────────────────────────────────────────────────────────
ss = st.session_state
ss.setdefault("selected_template_id", None)
ss.setdefault("selected_version", None)
ss.setdefault("form_system", "")
ss.setdefault("form_user", "")
ss.setdefault("form_provider", LLM_PROVIDERS[0])
ss.setdefault("form_name", "")
ss.setdefault("last_response", "")


def _load_record(record: dict) -> None:
    ss.selected_template_id = record.get("template_id")
    ss.selected_version = record.get("version")
    ss.form_system = record.get("system_prompt", "") or ""
    prov = record.get("provider") or LLM_PROVIDERS[0]
    ss.form_provider = prov if prov in LLM_PROVIDERS else LLM_PROVIDERS[0]
    ss.form_name = record.get("name") or ""


def _clear_form() -> None:
    ss.selected_template_id = None
    ss.selected_version = None
    ss.form_system = ""
    ss.form_user = ""
    ss.form_name = ""
    ss.last_response = ""


def _commit_after_change(message: str) -> str:
    """Commit prompts.json via GitHub API. Returns a human-readable status."""
    try:
        content = Path(PROMPTS_FILE).read_text(encoding="utf-8")
        ok = commit_file(content, message)
        return "committed ✅" if ok else "commit failed ❌"
    except Exception as exc:
        return f"commit error: {exc}"


def _do_sync(commit_msg: str = "[from-portkey] sync") -> dict:
    """Full reconcile + commit cycle. Stores a status string in session state.
    Never raises — Portkey API errors are surfaced via last_sync_status only.
    """
    try:
        remote_state = reconciler.fetch_portkey_state()
        remote_tids = {r["template_id"] for r in remote_state if r["template_id"]}
        from backend.prompt_manager import _read_file as _rf
        local_tids = {
            p.get("template_id") for p in _rf().get("prompts", [])
            if p.get("template_id")
        }
        counts = reconciler.reconcile_from_portkey()
    except Exception as exc:
        ss["last_sync_status"] = f"Portkey API failure: {exc}"
        return {"added": 0, "updated": 0, "deleted": 0}

    if counts.get("added") or counts.get("updated") or counts.get("deleted"):
        commit_status = _commit_after_change(commit_msg)
    else:
        commit_status = "no changes"
    ss["last_sync_status"] = (
        f"remote={len(remote_tids)} local={len(local_tids)} | "
        f"+{counts['added']} ~{counts['updated']} −{counts['deleted']} → {commit_status}"
    )
    return counts


# ── Auto-refresh tick (every 30 s while page is open) ───────────────────────
_tick = st_autorefresh(interval=30 * 1000, key="auto_sync_tick")
if _tick > 0:           # skip first synchronous render to keep load fast
    _do_sync()


# ── LEFT SIDEBAR – prompt list ──────────────────────────────────────────────
templates = get_templates()
if APP_ROLE == "user":
    templates = [
        {
            **t,
            "versions": [v for v in t["versions"] if v.get("is_production")],
            "latest": next(
                (v for v in reversed(t["versions"]) if v.get("is_production")),
                t["latest"],
            ),
        }
        for t in templates
        if any(v.get("is_production") for v in t["versions"])
    ]

with st.sidebar:
    st.header("📚 Prompts")
    st.caption(f"Mode: **{APP_ROLE}**")

    if st.button("☁ Sync from Portkey", use_container_width=True, type="primary"):
        with st.spinner("Pulling from Portkey…"):
            try:
                counts = _do_sync("[from-portkey] manual sync")
                st.toast(
                    f"Synced — added {counts['added']}, "
                    f"updated {counts['updated']}, deleted {counts['deleted']}"
                )
            except Exception as exc:
                st.error(f"Sync failed: {exc}")
        st.rerun()

    if IS_DEV:
        if st.button("➕ New", use_container_width=True):
            _clear_form()
            st.rerun()

    st.divider()

    if not templates:
        st.caption("No prompts available." if not IS_DEV else "No prompts yet — click ➕ New.")
    else:
        for t in templates:
            tid = t["template_id"]
            label = (
                t["name"]
                or (t["latest"].get("system_prompt", "") or "")[:40]
                or "(untitled)"
            )
            is_selected = bool(tid) and tid == ss.selected_template_id
            wrapper = "selected-link" if is_selected else ""
            key = f"nav-{tid or t['latest'].get('id')}"
            st.markdown(f'<div class="{wrapper}">', unsafe_allow_html=True)
            if st.button(label, key=key, use_container_width=True, type="tertiary"):
                _load_record(t["latest"])
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    # ── Status panel pinned at the bottom of the sidebar ────────────────────
    _gh_ok = _github_enabled()
    _gh_color = "#198754" if _gh_ok else "#dc3545"
    _gh_dot = "●"
    _last = ss.get("last_sync_status") or "no sync yet"
    st.markdown(
        f"""
        <div class="sidebar-status">
            <div class="row">
                <span style="color:{_gh_color};">{_gh_dot}</span>
                <span>GitHub <code>{_GH_REPO or '(unset)'}</code></span>
            </div>
            <div class="row muted-status">
                <span>Last sync:</span>
                <span>{_last}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── MAIN AREA ───────────────────────────────────────────────────────────────
st.markdown(
    "<h2 style='margin:0 0 0.5rem 0;'>🔗 Portkey Prompt Externalization</h2>",
    unsafe_allow_html=True,
)

editing = ss.selected_template_id is not None
col_edit, col_detail = st.columns([4, 1], gap="large")

# ── MIDDLE: editor / runner ─────────────────────────────────────────────────
with col_edit:
    if not editing and not IS_DEV:
        st.info("Pick a prompt from the sidebar to begin.")
    elif editing and IS_DEV:
        st.info(
            f"Editing **{ss.form_name or ss.selected_template_id}** "
            f"(v{ss.selected_version}). Saving creates a new version on Portkey."
        )
    elif editing and not IS_DEV:
        st.caption(
            f"Using **{ss.form_name}** (v{ss.selected_version}, production)."
        )

    if IS_DEV and not editing:
        ss.form_name = st.text_input(
            "Name",
            value=ss.form_name,
            placeholder="(auto-generated if blank)",
        )

    ss.form_system = st.text_area(
        "System Prompt" + ("" if IS_DEV else " (read-only)"),
        value=ss.form_system,
        height=110,
        placeholder="You are a helpful assistant…" if IS_DEV else "",
        disabled=not IS_DEV,
    )
    ss.form_user = st.text_area(
        "User Prompt",
        value=ss.form_user,
        height=140,
        placeholder="Ask anything…",
    )
    ss.form_provider = st.selectbox(
        "LLM Provider",
        options=LLM_PROVIDERS,
        index=LLM_PROVIDERS.index(ss.form_provider)
        if ss.form_provider in LLM_PROVIDERS
        else 0,
        disabled=not IS_DEV,
    )

    if IS_DEV:
        if editing:
            b1, b2, b3, b4 = st.columns(4)
            save_clicked = b1.button("💾 Save edit", type="primary", use_container_width=True)
            new_clicked = b2.button("✨ Save as new", use_container_width=True)
            run_clicked = b3.button("▶ Run", use_container_width=True)
            delete_clicked = b4.button("🗑 Delete", use_container_width=True)
        else:
            b1, b2 = st.columns(2)
            save_clicked = b1.button("💾 Save", type="primary", use_container_width=True)
            run_clicked = b2.button("▶ Run", use_container_width=True)
            new_clicked = False
            delete_clicked = False
    else:
        save_clicked = False
        new_clicked = False
        delete_clicked = False
        run_clicked = st.button("▶ Run", type="primary", use_container_width=True)

    st.subheader("💬 Response")
    if ss.last_response:
        st.markdown(ss.last_response)
    else:
        st.caption("Run the current prompt to see the LLM response here.")


# ── RIGHT: details panel ────────────────────────────────────────────────────
with col_detail:
    if not editing:
        st.caption("_Select a prompt to see its details and versions._")
    else:
        tpl = next(
            (t for t in templates if t["template_id"] == ss.selected_template_id),
            None,
        )
        if tpl is None:
            st.caption("Template not found in local index.")
        else:
            st.subheader("Details")
            st.markdown(f"**Name:** {tpl['name'] or '—'}")
            st.markdown(f"**Provider:** {tpl['latest'].get('provider', '—')}")
            st.markdown(
                f"<span class='muted'>ID: <code>{tpl['template_id']}</code></span>",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"<span class='muted'>Last updated: "
                f"{tpl['latest'].get('timestamp','')[:19].replace('T',' ')}</span>",
                unsafe_allow_html=True,
            )

            st.divider()
            st.markdown("**Versions**")

            versions = list(reversed(tpl["versions"]))
            options = [v.get("version") for v in versions]
            labels = {
                v.get("version"): (
                    f"v{v.get('version')}"
                    + (" 🟢" if v.get("is_production") else "")
                )
                for v in versions
            }
            try:
                current_idx = options.index(ss.selected_version)
            except ValueError:
                current_idx = 0
            picked = st.radio(
                "Select a version to load",
                options=options,
                index=current_idx,
                format_func=lambda x: labels.get(x, f"v{x}"),
                label_visibility="collapsed",
            )
            if picked != ss.selected_version:
                chosen = next(v for v in versions if v.get("version") == picked)
                _load_record(chosen)
                st.rerun()


# ── ACTIONS ─────────────────────────────────────────────────────────────────


def _save_new_or_edit(is_new_template: bool) -> None:
    if not ss.form_system.strip():
        st.warning("System prompt cannot be empty.")
        return

    with st.spinner("Pushing to Portkey…"):
        if is_new_template:
            result = push_prompt_to_portkey(
                system_prompt=ss.form_system,
                user_prompt="",
                provider=ss.form_provider,
                name=ss.form_name or None,
            )
        else:
            result = update_prompt_on_portkey(
                prompt_id=ss.selected_template_id,
                system_prompt=ss.form_system,
                user_prompt="",
                provider=ss.form_provider,
            )

    if not result or not result.get("id"):
        st.error(
            "Portkey rejected the push. "
            "Check that PORTKEY_VK_GOOGLE / PORTKEY_API_KEY are valid."
        )
        return

    new_tid = result["id"]
    new_version = int(result.get("version") or 1)

    # A brand-new prompt has only one version → it's trivially the production one.
    # Edits (new version of an existing template) stay unpublished until the
    # dev explicitly promotes them.
    add_prompt(
        system_prompt=ss.form_system,
        provider=ss.form_provider,
        template_id=new_tid,
        version=new_version,
        name=ss.form_name or None,
        is_production=is_new_template,
        last_origin="app",
    )

    msg_kind = "create" if is_new_template else "edit"
    commit_status = _commit_after_change(
        f"[app] {msg_kind} prompt '{ss.form_name or new_tid}' v{new_version}"
    )
    ss["last_sync_status"] = (
        f"{msg_kind} v{new_version} on Portkey → {commit_status}"
    )

    # Load the new version into the form so we're now editing it.
    versions = get_template_versions(new_tid)
    if versions:
        _load_record(versions[-1])
    st.success(f"Saved v{new_version} on Portkey.")
    st.rerun()


if save_clicked:
    _save_new_or_edit(is_new_template=not editing)

if new_clicked:
    _save_new_or_edit(is_new_template=True)

if delete_clicked:
    ss["confirm_delete"] = ss.selected_template_id

if ss.get("confirm_delete") and ss["confirm_delete"] == ss.selected_template_id:
    st.warning(
        f"Delete **{ss.form_name or ss.selected_template_id}** "
        f"from Portkey AND repo? This removes ALL versions."
    )
    cc1, cc2 = st.columns(2)
    if cc1.button("✅ Yes, delete", type="primary", key="confirm_yes"):
        tid = ss.selected_template_id
        with st.spinner("Deleting on Portkey…"):
            ok = delete_prompt_on_portkey(tid)
        if not ok:
            st.error("Portkey delete failed — see Last sync line for details.")
        else:
            removed = delete_template(tid)
            commit_status = _commit_after_change(
                f"[app] delete prompt '{ss.form_name or tid}'"
            )
            ss["last_sync_status"] = (
                f"deleted {removed} local row(s) → {commit_status}"
            )
            ss.pop("confirm_delete", None)
            _clear_form()
            st.success("Deleted.")
            st.rerun()
    if cc2.button("Cancel", key="confirm_no"):
        ss.pop("confirm_delete", None)
        st.rerun()

if run_clicked:
    if not ss.form_user.strip():
        st.warning("Please enter a user prompt.")
    elif not ss.form_system.strip():
        st.warning("System prompt is empty — pick or write one first.")
    else:
        with st.spinner("Calling LLM…"):
            try:
                response_text = send_prompt(
                    system_prompt=ss.form_system,
                    user_prompt=ss.form_user,
                    provider=ss.form_provider,
                )
            except Exception as exc:
                response_text = f"⚠️ Error: {exc}"
        ss.last_response = response_text
        st.rerun()
