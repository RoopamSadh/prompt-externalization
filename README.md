# Portkey Prompt Externalization

A Git-tracked, role-aware prompt library that keeps **Portkey's prompt dashboard**, a **canonical `prompts.json` in this repo**, and a **Streamlit app** in sync automatically — with no always-on server.

---

## The idea in one line

> Treat prompts like code: store them in Git, let GitHub Actions be the always-on glue, and Portkey becomes just another deployment target.

## Architecture

```
                 ┌───────────────────┐
                 │   GitHub Repo     │   ← prompts.json (canonical)
                 │   (main branch)   │
                 └─┬─────────┬───────┘
   commits via API │         │ pull / clone
   ┌───────────────┘         └────────────────┐
   ▼                                          ▼
┌─────────────────┐                  ┌──────────────────┐
│ Streamlit Cloud │                  │   GitHub         │
│  (the app)      │                  │   Actions        │
└──┬──────────────┘                  └──────┬───────────┘
   │ runs prompts                            │ push trigger
   │ Portkey gateway                         │ Portkey Admin API
   ▼                                          ▼
┌─────────────────────────────────────────────────────────┐
│                       Portkey                           │
└─────────────────────────────────────────────────────────┘
```

Three surfaces can edit prompts, and all three stay in sync:

| Surface | How changes propagate |
|---|---|
| Streamlit app (dev mode) | App calls Portkey API, then commits `prompts.json` via GitHub Contents API |
| Portkey dashboard | App's auto-refresh (every 30 s while open) or manual **Sync** button pulls + commits |
| Direct `prompts.json` edit + `git push` | GitHub Action diffs the file and applies add/edit/delete to Portkey |

Loops are prevented by commit-message tagging (`[from-portkey]`, `[id-backfill]`) plus content-hash drift detection in the reconciler.

---

## Project layout

```
.
├── .github/
│   └── workflows/
│       └── apply-to-portkey.yml     # push Action: prompts.json → Portkey
├── app/
│   └── streamlit_app.py             # role-aware three-pane UI
├── backend/
│   ├── cli.py                       # Action entry-points
│   ├── github_writer.py             # commit via GitHub Contents API
│   ├── portkey_client.py            # Portkey Admin API wrapper
│   ├── prompt_manager.py            # prompts.json CRUD + schema helpers
│   └── reconciler.py                # bi-directional drift + sync
├── config/
│   └── settings.py                  # .env + st.secrets bridge
├── prompts.json                     # canonical prompt store
├── requirements.txt
└── .env                             # local secrets (gitignored)
```

---

## Roles

Controlled by the `APP_ROLE` env var:

| Role | UI behaviour |
|---|---|
| `dev` | Full CRUD: ➕ New, 💾 Save, ✨ Save as new, ▶ Run, 🗑 Delete |
| `user` | Read-only system prompt, editable user prompt, ▶ Run only. Sidebar shows only prompts flagged `is_production: true` |

Locally you run as `dev`; the Streamlit Cloud deployment is configured as `user`.

---

## Data model — `prompts.json`

Each record is one version of one system prompt:

```json
{
  "id":            "<local-uuid>",
  "template_id":   "<portkey-id | null>",
  "version":       1,
  "name":          "Test1",
  "is_production": false,
  "timestamp":     "2026-04-16T06:48:37Z",
  "system_prompt": "You are a botanist.",
  "provider":      "google",
  "last_origin":   "portkey | app | manual",
  "_hash":         "<sha1 of system_prompt + provider>"
}
```

Runtime fields (user prompt input, LLM response) are intentionally **not** persisted — they belong to a single Run, not the template.

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/RoopamSadh/prompt-externalization.git
cd prompt-externalization
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure `.env`

```
# Portkey
PORTKEY_API_KEY=...
PORTKEY_COLLECTION_ID=...
PORTKEY_VK_GOOGLE=...
PORTKEY_VK_HUGGINGFACE=...

# Providers
GOOGLE_API_KEY=...
HUGGINGFACE_API_KEY=...

# App behaviour
APP_ROLE=dev

# GitHub (used by the app to commit prompts.json)
GITHUB_TOKEN=github_pat_...       # fine-grained PAT, Contents: Read+Write on this repo
GITHUB_REPO=RoopamSadh/prompt-externalization
GITHUB_BRANCH=main
```

### 3. Run locally

```bash
streamlit run app/streamlit_app.py
```

### 4. Deploy to Streamlit Cloud (optional)

Push to GitHub, then on [share.streamlit.io](https://share.streamlit.io): point a new app at this repo, branch `main`, main file `app/streamlit_app.py`, and paste the same keys under **Advanced settings → Secrets** as TOML — with `APP_ROLE = "user"`.

### 5. Configure the push Action

In the repo on GitHub → **Settings → Secrets and variables → Actions**, add:
`PORTKEY_API_KEY`, `PORTKEY_COLLECTION_ID`, `PORTKEY_VK_GOOGLE`, `PORTKEY_VK_HUGGINGFACE`, `GOOGLE_API_KEY`, `HUGGINGFACE_API_KEY`.

`GITHUB_TOKEN` is injected automatically by Actions — no setup needed.

---

## Configuration reference

| Variable | Purpose |
|---|---|
| `PORTKEY_API_KEY` | Portkey Admin API auth |
| `PORTKEY_COLLECTION_ID` | Collection where prompts are created |
| `PORTKEY_VK_GOOGLE` / `PORTKEY_VK_HUGGINGFACE` | Portkey virtual key slugs |
| `GOOGLE_API_KEY` / `HUGGINGFACE_API_KEY` | Upstream provider keys (used for Run) |
| `APP_ROLE` | `dev` or `user` |
| `GITHUB_TOKEN` | Fine-grained PAT (Contents: Read+Write on this repo) |
| `GITHUB_REPO` | `owner/repo` |
| `GITHUB_BRANCH` | Default `main` |

---

## How the flows work

### A — Dev edits in the app

1. User clicks 💾 Save / Save edit / 🗑 Delete.
2. App calls Portkey API directly (create / update / delete).
3. Local `prompts.json` updated.
4. `github_writer.commit_file()` commits via Contents API (`[app] ...`).

### B — Edit in the Portkey dashboard

1. App auto-refresh (30 s) or manual **☁ Sync from Portkey** fires.
2. `reconciler.reconcile_from_portkey()` diffs, applies locally.
3. Commits to repo (`[from-portkey] sync`).

### C — Manual edit + `git push`

1. Push to `main` touching `prompts.json`.
2. Action diffs against `HEAD~1`, deletes removed template_ids from Portkey, then runs the create/update reconciler.
3. Any ids/versions assigned by Portkey are committed back as `[id-backfill]`.

---

## Why it doesn't loop

Two independent safeguards:

- **Commit-message gating**: the Action skips any commit whose message contains `[from-portkey]` or `[id-backfill]`.
- **Hash-based no-op**: the reconciler only writes when `_hash` differs, so after one round-trip everyone settles and nothing further fires.

---

## Known limitations

- Production-flag sync from Portkey's native `@production` label is TBD. Today, `is_production` is locally owned (auto-promoted to the latest version of each template when none is flagged).
- Auto-refresh pauses when the app is idle (Streamlit Cloud sleeps inactive apps). Changes made on Portkey while no one is watching appear on the next Sync click.
- No Portkey-API-failure safety net — for the POC, the truth is whatever Portkey returns. Recovery path is `git revert`.
- HuggingFace is not routable end-to-end until a proper Portkey virtual key is configured for it.
