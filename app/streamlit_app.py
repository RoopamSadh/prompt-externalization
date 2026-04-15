"""
Streamlit Frontend – Prompt Externalization POC

Three-pane layout:

  LEFT SIDEBAR          MIDDLE (editor)              RIGHT (details)
  ─────────────         ──────────────────           ─────────────────
  • Prompt 1            Name / System / User         Selected template
  • Prompt 2            Provider                     Version radio
  • Prompt 3 (sel)      [Save] [Run]                 Metadata

Actions (kept strictly separate):
  • Save new prompt  → POST /v1/prompts     (creates prompt + v1)
  • Save edit        → PUT  /v1/prompts/{id} (creates a NEW version)
  • Run              → chat-completion; no save, no version bump
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from config.settings import LLM_PROVIDERS
from backend.prompt_manager import (
    add_prompt,
    update_response,
    get_templates,
    get_template_versions,
)
from backend.portkey_client import (
    push_prompt_to_portkey,
    update_prompt_on_portkey,
    send_prompt,
)
from backend.sync_service import git_commit_and_push, _print_git_banner, sync_once

st.set_page_config(
    page_title="Portkey Prompt Externalization",
    page_icon="🔗",
    layout="wide",
)

if "_git_banner_printed" not in st.session_state:
    _print_git_banner("app")
    st.session_state["_git_banner_printed"] = True

# ── Minimal CSS to make sidebar buttons look like links ─────────────────────
st.markdown(
    """
    <style>
    /* Flat "link-like" buttons in the sidebar */
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
    /* Tighten vertical gap between stacked sidebar buttons */
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0.15rem !important;
    }
    section[data-testid="stSidebar"] .stButton {
        margin-bottom: 0 !important;
    }
    section[data-testid="stSidebar"] button[kind="tertiary"]:hover,
    section[data-testid="stSidebar"] button[kind="secondary"]:hover {
        background: #eef2f7 !important;
        color: #0b2a5b !important;
    }
    .selected-link button {
        background: #eef2f7 !important;
        font-weight: 600 !important;
    }
    .muted { color: #888; font-size: 0.8rem; }
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


def _load_version_into_form(record: dict) -> None:
    ss.selected_template_id = record.get("template_id")
    ss.selected_version = record.get("version")
    ss.form_system = record.get("system_prompt", "") or ""
    ss.form_user = record.get("user_prompt", "") or ""
    prov = record.get("provider") or LLM_PROVIDERS[0]
    ss.form_provider = prov if prov in LLM_PROVIDERS else LLM_PROVIDERS[0]
    ss.form_name = record.get("name") or ""
    ss.last_response = record.get("response") or ""


def _clear_form() -> None:
    ss.selected_template_id = None
    ss.selected_version = None
    ss.form_system = ""
    ss.form_user = ""
    ss.form_name = ""
    ss.last_response = ""


def _require_user_prompt() -> bool:
    if not ss.form_user.strip():
        st.warning("Please enter a user prompt.")
        return False
    return True


# ── LEFT SIDEBAR – prompt list as link-style rows ───────────────────────────
templates = get_templates()

with st.sidebar:
    st.header("📚 Prompts")

    c1, c2 = st.columns(2)
    if c1.button("➕ New", use_container_width=True):
        _clear_form()
        st.rerun()
    if c2.button("🔄 Refresh", use_container_width=True):
        st.rerun()

    if st.button("☁ Sync from Portkey", use_container_width=True):
        with st.spinner("Pulling from Portkey…"):
            try:
                sync_once()
                st.toast("Synced with Portkey")
            except Exception as exc:
                st.error(f"Sync failed: {exc}")
        st.rerun()

    st.divider()

    if not templates:
        st.caption("No prompts yet. Click **+ New** to create one.")
    else:
        for t in templates:
            tid = t["template_id"]
            label = (
                t["name"]
                or (t["latest"].get("user_prompt", "") or "")[:40]
                or "(untitled)"
            )
            is_selected = bool(tid) and tid == ss.selected_template_id
            key = f"nav-{tid or t['latest'].get('id')}"

            # Wrap in a div so selected state can be styled.
            wrapper_class = "selected-link" if is_selected else ""
            st.markdown(f'<div class="{wrapper_class}">', unsafe_allow_html=True)
            if st.button(label, key=key, use_container_width=True, type="tertiary"):
                _load_version_into_form(t["latest"])
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


# ── MAIN AREA – middle (editor) + right (details) ──────────────────────────
st.markdown(
    "<h2 style='margin:0 0 0.5rem 0;'>🔗 Portkey Prompt Externalization</h2>",
    unsafe_allow_html=True,
)

editing = ss.selected_template_id is not None
col_edit, col_detail = st.columns([4, 1], gap="large")

# ── MIDDLE: editor ──────────────────────────────────────────────────────────
with col_edit:
    if editing:
        st.info(
            f"Editing **{ss.form_name or ss.selected_template_id}** "
            f"(v{ss.selected_version}). Saving creates a new version."
        )
    else:
        st.caption("Create a new prompt or select one from the sidebar.")

    if not editing:
        ss.form_name = st.text_input(
            "Name",
            value=ss.form_name,
            placeholder="(auto-generated if blank)",
        )

    ss.form_system = st.text_area(
        "System Prompt",
        value=ss.form_system,
        height=100,
        placeholder="You are a helpful assistant…",
    )
    ss.form_user = st.text_area(
        "User Prompt",
        value=ss.form_user,
        height=150,
        placeholder="Ask anything…",
    )
    ss.form_provider = st.selectbox(
        "LLM Provider",
        options=LLM_PROVIDERS,
        index=LLM_PROVIDERS.index(ss.form_provider)
        if ss.form_provider in LLM_PROVIDERS
        else 0,
    )

    b1, b2, b3 = st.columns(3)
    if editing:
        save_clicked = b1.button("💾 Save edit", type="primary", use_container_width=True)
        new_clicked = b2.button("✨ Save as new", use_container_width=True)
    else:
        save_clicked = b1.button("💾 Save", type="primary", use_container_width=True)
        new_clicked = False
    run_clicked = b3.button("▶ Run", use_container_width=True)

    # Response display sits below the editor.
    st.subheader("💬 Response")
    if ss.last_response:
        st.markdown(ss.last_response)
    else:
        st.caption("Run the current prompt to see the LLM response here.")

# ── RIGHT: details of selected template ─────────────────────────────────────
with col_detail:
    if not editing:
        st.caption("_Select a prompt to see its details and versions here._")
    else:
        tpl = next(
            (t for t in templates if t["template_id"] == ss.selected_template_id),
            None,
        )
        if tpl is None:
            st.caption("Template not found in local index yet.")
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

            versions = list(reversed(tpl["versions"]))  # newest first
            options = [v.get("version") for v in versions]
            labels = {
                v.get("version"): (
                    f"v{v.get('version')}"
                    + (" ⭐" if v.get("is_default") else "")
                )
                for v in versions
            }

            # Radio driven by session state; picking a new one loads that
            # version into the middle editor on next rerun.
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
                _load_version_into_form(chosen)
                st.rerun()


# ── ACTIONS ─────────────────────────────────────────────────────────────────

# Save new
if (save_clicked and not editing) or new_clicked:
    if _require_user_prompt():
        result = push_prompt_to_portkey(
            system_prompt=ss.form_system,
            user_prompt=ss.form_user,
            provider=ss.form_provider,
            name=ss.form_name or None,
        )
        if result and result.get("id"):
            add_prompt(
                system_prompt=ss.form_system,
                user_prompt=ss.form_user,
                provider=ss.form_provider,
                source="frontend",
                template_id=result["id"],
                version=result.get("version") or 1,
                name=ss.form_name or None,
                is_default=True,
            )
            ss.selected_template_id = result["id"]
            ss.selected_version = result.get("version") or 1
            git_commit_and_push(
                f"[app] create prompt '{ss.form_name or result['id']}' "
                f"(v{ss.selected_version})"
            )
            st.success(f"Saved to Portkey (v{ss.selected_version}).")
            st.rerun()
        else:
            st.error("Failed to create prompt on Portkey — see console.")

# Save edit (new version)
if save_clicked and editing:
    if _require_user_prompt():
        result = update_prompt_on_portkey(
            prompt_id=ss.selected_template_id,
            system_prompt=ss.form_system,
            user_prompt=ss.form_user,
            provider=ss.form_provider,
        )
        if result and result.get("version"):
            add_prompt(
                system_prompt=ss.form_system,
                user_prompt=ss.form_user,
                provider=ss.form_provider,
                source="frontend",
                template_id=ss.selected_template_id,
                version=result["version"],
                name=ss.form_name or None,
                is_default=False,
            )
            ss.selected_version = result["version"]
            git_commit_and_push(
                f"[app] edit prompt '{ss.form_name or ss.selected_template_id}' "
                f"→ v{result['version']}"
            )
            st.success(f"New version v{result['version']} saved.")
            st.rerun()
        else:
            st.error("Failed to update prompt on Portkey — see console.")

# Run (fire only)
if run_clicked:
    if _require_user_prompt():
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
        if ss.selected_template_id and ss.selected_version is not None:
            for v in get_template_versions(ss.selected_template_id):
                if v.get("version") == ss.selected_version and not v.get("response"):
                    update_response(v["id"], response_text)
                    git_commit_and_push(
                        f"[app] record response for "
                        f"'{ss.form_name or ss.selected_template_id}' "
                        f"v{ss.selected_version}"
                    )
                    break
        st.rerun()
