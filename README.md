# Portkey Prompt Externalization POC

A proof-of-concept that demonstrates **bi-directional prompt synchronisation** between a local Git repository (`prompts.json`) and the [Portkey AI Gateway](https://portkey.ai).

## What it does

| Direction | Flow |
|-----------|------|
| **Frontend → Git → Portkey** | User submits a prompt in the Streamlit UI → prompt is saved to `prompts.json` → prompt template is pushed to the Portkey Prompts library → LLM response is fetched via the Portkey gateway and stored back in `prompts.json`. |
| **Portkey → Git** | A background sync script polls Portkey's Prompts API on a configurable interval → any prompts created directly on the Portkey dashboard are pulled and merged into `prompts.json` → optionally auto-committed and pushed to the Git remote. |

---

## Project structure

```
.
├── app/
│   └── streamlit_app.py      # Streamlit frontend
├── backend/
│   ├── portkey_client.py      # Portkey SDK + REST API wrapper
│   ├── prompt_manager.py      # Read/write operations on prompts.json
│   └── sync_service.py        # Polling script for Portkey → Git sync
├── config/
│   └── settings.py            # Centralised env-var loader
├── prompts.json               # Single source of truth (Git side)
├── .env                       # Your secrets (not committed)
├── .env.example               # Template for .env
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Clone & install

```bash
git clone <your-repo-url>
cd git_portkey_externalization
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure secrets

```bash
cp .env.example .env
# Edit .env and fill in your Portkey API key + provider API keys
```

**Where to get the keys:**

- **PORTKEY_API_KEY** – Portkey dashboard → Settings → API Keys.
- **OPENAI_API_KEY** – Your OpenAI API key from https://platform.openai.com/api-keys
- **HUGGINGFACE_API_KEY** – Your HuggingFace token from https://huggingface.co/settings/tokens
- **ANTHROPIC_API_KEY** – Your Anthropic API key from https://console.anthropic.com/

### 3. Run the Streamlit app

```bash
streamlit run app/streamlit_app.py
```

The app opens at `http://localhost:8501`.

### 4. Run the sync service (separate terminal)

```bash
python -m backend.sync_service
```

This polls Portkey every `SYNC_INTERVAL_SECONDS` (default 60 s) and merges any new prompts into `prompts.json`.

To enable auto-commit and push to Git, set in `.env`:

```
GIT_AUTO_COMMIT=true
GIT_REMOTE=origin
GIT_BRANCH=main
```

---

## How it works – step by step

### Frontend submission flow

1. User fills in **System Prompt**, **User Prompt**, and picks an **LLM Provider**.
2. On submit, `prompt_manager.add_prompt()` appends the record to `prompts.json` **before** any API call.
3. `portkey_client.push_prompt_to_portkey()` creates a prompt template on the Portkey dashboard.
4. `portkey_client.send_prompt()` sends a chat-completion request through the Portkey gateway to the chosen provider.
5. The LLM response is saved back into `prompts.json` via `prompt_manager.update_response()`.
6. The response is rendered in the Streamlit UI.

### Portkey → Git sync flow

1. `sync_service.sync_once()` calls `portkey_client.fetch_portkey_prompts()` to list all prompt templates on Portkey.
2. New prompts (not already in `prompts.json` by id) are merged via `prompt_manager.bulk_add_prompts()`.
3. If `GIT_AUTO_COMMIT=true`, the service stages, commits, and pushes `prompts.json`.

---

## Configuration reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PORTKEY_API_KEY` | — | Your Portkey API key |
| `OPENAI_API_KEY` | — | OpenAI API key (direct provider auth) |
| `HUGGINGFACE_API_KEY` | — | HuggingFace API token (direct provider auth) |
| `ANTHROPIC_API_KEY` | — | Anthropic API key (direct provider auth) |
| `SYNC_INTERVAL_SECONDS` | 60 | Polling interval for the sync script |
| `PROMPTS_FILE_PATH` | prompts.json | Path to the prompts file |
| `GIT_AUTO_COMMIT` | false | Auto-commit prompts.json on sync |
| `GIT_REMOTE` | origin | Git remote name |
| `GIT_BRANCH` | main | Git branch name |

---

## prompts.json schema

```json
{
  "prompts": [
    {
      "id": "uuid",
      "timestamp": "ISO-8601",
      "system_prompt": "...",
      "user_prompt": "...",
      "provider": "openai | huggingface | anthropic | portkey",
      "response": "...",
      "source": "frontend | portkey"
    }
  ],
  "last_synced_at": "ISO-8601 | null",
  "version": "1.0"
}
```
