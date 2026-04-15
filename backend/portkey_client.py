"""
Portkey Client – handles all communication with the Portkey AI Gateway.

Three responsibilities:
1. **send_prompt()**  – forward a system+user prompt to an LLM provider via
   Portkey and return the response text.
2. **push_prompt_to_portkey()** – create a prompt in the Portkey Prompts
   section via the Admin API (POST /v1/prompts) so it is visible on the
   Portkey dashboard.
3. **fetch_portkey_prompts()** – list all prompts from the Portkey Prompts
   section via the Admin API (GET /v1/prompts) so prompts created on the
   dashboard can be merged into prompts.json.

Authentication approach:
  - `provider`      → tells Portkey which upstream LLM to route to
  - `Authorization` → the provider's own API key (e.g. OpenAI sk-…)
  This avoids the need to create Portkey Virtual Keys on the dashboard.

Admin API auth:
  - All Admin API calls use the  x-portkey-api-key  header.
  - Docs: https://portkey.ai/docs/api-reference/admin-api/control-plane/prompts/create-prompt
"""

import uuid
import json
from datetime import datetime, timezone
from typing import Optional

import requests
from portkey_ai import Portkey

from config.settings import (
    PORTKEY_API_KEY,
    PORTKEY_BASE_URL,
    PORTKEY_COLLECTION_ID,
    PROVIDER_API_KEYS,
    PROVIDER_VIRTUAL_KEYS,
)


def _get_client(provider: str) -> Portkey:
    """
    Build a Portkey SDK client for the chosen provider.

    Uses the 'provider' + 'Authorization' pattern so Portkey knows which
    upstream LLM to call and authenticates with the provider's own API key.
    No Portkey Virtual Keys required.
    """
    provider_key = PROVIDER_API_KEYS.get(provider, "")
    if not provider_key:
        raise ValueError(
            f"No API key configured for provider '{provider}'. "
            f"Set {provider.upper()}_API_KEY in your .env file."
        )
    else:
        if provider == "huggingface":
            config = {
            "provider": "openai",
            "api_key": provider_key,
            "custom_host": "https://router.huggingface.co/v1"}
        else:
            config = {
            "provider": provider,
            "api_key": provider_key}
    
    return Portkey(
        api_key=PORTKEY_API_KEY,
        config=json.dumps(config),
    )


# ── 1.  Chat completion via Portkey gateway ─────────────────────────────────


def send_prompt(
    system_prompt: str,
    user_prompt: str,
    provider: str,
    model: Optional[str] = None,
) -> str:
    """
    Send a chat-completion request through Portkey and return the
    assistant's reply as a plain string.

    Parameters
    ----------
    system_prompt : str
        The system-level instruction for the LLM.
    user_prompt : str
        The user's message / question.
    provider : str
        One of the keys in config.settings.PROVIDER_API_KEYS
        (e.g. "openai", "anthropic", "huggingface").
    model : str, optional
        Override the default model.  When None the provider's default is used
        (e.g. gpt-4o-mini for OpenAI).

    Returns
    -------
    str
        The text content of the first choice in the completion.
    """
    # Provider-to-default-model mapping.
    default_models = {"huggingface": "meta-llama/Llama-3.2-1B-Instruct",
                      "google":"gemini-2.5-flash"}

    client = _get_client(provider)
    chosen_model = model or default_models.get(provider, "meta-llama/Llama-3.2-1B-Instruct")

    messages = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    completion = client.chat.completions.create(
        model=chosen_model,
        messages=messages,
    )
    return completion.choices[0].message.content


# ── 2.  Push a prompt to the Portkey Prompts section (Admin API) ─────────────
#
# Admin API – Create Prompt
# POST https://api.portkey.ai/v1/prompts
# Header: x-portkey-api-key
# Body:  { name, string, model, parameters, ... }
#
# The "string" field holds the raw prompt text.  For chat-style prompts
# Portkey also accepts a JSON-encoded array of {role, content} messages
# in the "string" field (this is how the dashboard stores them).
#
# Docs: https://portkey.ai/docs/api-reference/admin-api/control-plane/prompts/create-prompt


def _admin_headers() -> dict:
    return {
        "x-portkey-api-key": PORTKEY_API_KEY,
        "Content-Type": "application/json",
    }


def _build_prompt_payload(
    system_prompt: str,
    user_prompt: str,
    provider: str,
    model: str = "",
    name: Optional[str] = None,
    include_name: bool = True,
) -> dict:
    """
    Build the body for POST /v1/prompts or PUT /v1/prompts/{id}.
    `name` is only meaningful on create; on update Portkey ignores it.
    """
    messages = []
    if system_prompt.strip():
        messages.append({
            "role": "system",
            "content": [{"type": "text", "text": system_prompt}],
        })
    messages.append({
        "role": "user",
        "content": [{"type": "text", "text": user_prompt}],
    })

    default_models = {
        "huggingface": "meta-llama/Llama-3.2-1B-Instruct",
        "google": "gemini-2.5-flash",
    }
    resolved_model = model or default_models.get(provider, "gpt-4o-mini")
    virtual_key = PROVIDER_VIRTUAL_KEYS.get(provider, "")

    payload = {
        "string": json.dumps(messages),
        "model": resolved_model,
        "virtual_key": virtual_key,
        "parameters": {
            "temperature": 0.7,
            "max_tokens": 256,
            "model": resolved_model,
        },
        "template_metadata": {
            "template_type": "chat",
            "is_raw_template": 0,
            "provider": provider,
            "source": "streamlit-frontend",
        },
    }
    if include_name:
        payload["name"] = name or f"prompt-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        payload["collection_id"] = PORTKEY_COLLECTION_ID
    return payload


def push_prompt_to_portkey(
    system_prompt: str,
    user_prompt: str,
    provider: str = "",
    model: str = "",
    name: Optional[str] = None,
) -> Optional[dict]:
    """
    Create a NEW prompt on Portkey via POST /v1/prompts.

    Returns a dict {id, version} on success (version is the initial version
    number assigned by Portkey, typically 1), or None on failure.
    """
    payload = _build_prompt_payload(system_prompt, user_prompt, provider, model, name)
    try:
        resp = requests.post(
            f"{PORTKEY_BASE_URL}/prompts",
            headers=_admin_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        portkey_id = result.get("id")
        version = (
            result.get("version")
            or result.get("prompt_version")
            or 1
        )
        print(f"[portkey] Prompt created – id={portkey_id} version={version}")
        return {"id": portkey_id, "version": int(version) if str(version).isdigit() else 1}
    except requests.RequestException as exc:
        body = exc.response.text if getattr(exc, "response", None) is not None else ""
        print(f"[portkey] Failed to create prompt: {exc}  body={body}")
        return None


def update_prompt_on_portkey(
    prompt_id: str,
    system_prompt: str,
    user_prompt: str,
    provider: str = "",
    model: str = "",
    version_description: str = "",
) -> Optional[dict]:
    """
    Update an existing prompt on Portkey via PUT /v1/prompts/{id}.
    Per Portkey docs, any change to template fields (string, model, parameters,
    etc.) automatically creates a new version of the prompt. The previous
    version is preserved.

    Returns {id, version} of the newly created version, or None on failure.
    """
    payload = _build_prompt_payload(
        system_prompt, user_prompt, provider, model, include_name=False
    )
    if version_description:
        payload["version_description"] = version_description

    try:
        resp = requests.put(
            f"{PORTKEY_BASE_URL}/prompts/{prompt_id}",
            headers=_admin_headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        result = resp.json()
        # PUT response may not return the numeric version; re-fetch versions
        # and take the highest prompt_version as the newly created one.
        version = result.get("version") or result.get("prompt_version")
        if not (isinstance(version, int) or (isinstance(version, str) and version.isdigit())):
            versions = fetch_prompt_versions(prompt_id)
            nums = [v.get("prompt_version") for v in versions if v.get("prompt_version")]
            version = max(nums) if nums else 1
        version = int(version)
        print(f"[portkey] Prompt updated – id={prompt_id} new_version={version}")
        return {"id": prompt_id, "version": version}
    except requests.RequestException as exc:
        body = exc.response.text if getattr(exc, "response", None) is not None else ""
        print(f"[portkey] Failed to update prompt {prompt_id}: {exc}  body={body}")
        return None


def delete_prompt_on_portkey(prompt_id: str) -> bool:
    """Delete an entire prompt (all versions). DELETE /v1/prompts/{id}."""
    try:
        resp = requests.delete(
            f"{PORTKEY_BASE_URL}/prompts/{prompt_id}",
            headers=_admin_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[portkey] Deleted prompt id={prompt_id}")
        return True
    except requests.RequestException as exc:
        body = exc.response.text if getattr(exc, "response", None) is not None else ""
        print(f"[portkey] Failed to delete {prompt_id}: {exc}  body={body}")
        return False


def publish_prompt_version(prompt_id: str, version: int) -> bool:
    """Mark a version as the default. PUT /v1/prompts/{id}/makeDefault."""
    try:
        resp = requests.put(
            f"{PORTKEY_BASE_URL}/prompts/{prompt_id}/makeDefault",
            headers=_admin_headers(),
            json={"version": int(version)},
            timeout=15,
        )
        resp.raise_for_status()
        print(f"[portkey] Published id={prompt_id} version={version}")
        return True
    except requests.RequestException as exc:
        body = exc.response.text if getattr(exc, "response", None) is not None else ""
        print(f"[portkey] Failed to publish: {exc}  body={body}")
        return False


def fetch_prompt_versions(prompt_id: str) -> list[dict]:
    """GET /v1/prompts/{id}/versions – returns list of version objects."""
    try:
        resp = requests.get(
            f"{PORTKEY_BASE_URL}/prompts/{prompt_id}/versions",
            headers=_admin_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])
    except requests.RequestException as exc:
        print(f"[portkey] Failed to list versions for {prompt_id}: {exc}")
        return []


# ── 3.  Fetch prompts from the Portkey Prompts section (Admin API) ───────────
#
# Two-step process:
#   a) GET /v1/prompts          → list of summary objects (no "string" field)
#   b) GET /v1/prompts/{id}     → full prompt with "string" (the messages)
#
# The "string" field is a JSON-encoded array of messages where content uses
# Portkey's multi-part format:  [{role, content: [{type:"text", text:"…"}]}]


def _extract_text(content) -> str:
    """
    Extract plain text from Portkey's content field which can be either:
      - a plain string:  "hello"
      - a list of parts: [{"type":"text","text":"hello"}]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)
    return str(content) if content else ""


def _get_prompt_detail(prompt_id: str, headers: dict) -> Optional[dict]:
    """Fetch the full prompt object including the 'string' field."""
    try:
        resp = requests.get(
            f"{PORTKEY_BASE_URL}/prompts/{prompt_id}",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        print(f"[sync] Failed to fetch prompt {prompt_id}: {exc}")
        return None


def fetch_portkey_prompts() -> list[dict]:
    """
    List all prompts from Portkey and normalise them into prompts.json
    schema.  For each prompt in the list, fetches the full detail to get
    the 'string' (messages) field.

    Returns a list of dicts, each with:
        id, timestamp, system_prompt, user_prompt, provider, response, source
    """
    headers = {
        "x-portkey-api-key": PORTKEY_API_KEY,
        "Content-Type": "application/json",
    }

    # Step a) – list all prompts (summary only).
    try:
        resp = requests.get(
            f"{PORTKEY_BASE_URL}/prompts",
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        response_json = resp.json()
        if isinstance(response_json, list):
            prompt_summaries = response_json
        else:
            prompt_summaries = response_json.get("data", [])
    except requests.RequestException as exc:
        body = ""
        if hasattr(exc, "response") and exc.response is not None:
            body = exc.response.text
        print(f"[sync] Failed to list Portkey prompts: {exc}  body={body}")
        return []

    normalised: list[dict] = []
    for summary in prompt_summaries:
        prompt_id = summary.get("id", "")
        if not prompt_id:
            continue

        # Fetch all versions for this prompt so each becomes a local record.
        versions = fetch_prompt_versions(prompt_id)

        # Fetch prompt-level detail once – versions endpoint doesn't carry
        # virtual_key / template_metadata, those live at the prompt level.
        prompt_detail = _get_prompt_detail(prompt_id, headers) or {}

        if not versions:
            if prompt_detail:
                prompt_detail["prompt_version"] = prompt_detail.get("prompt_version") or 1
                versions = [prompt_detail]

        # Default version is the UUID Portkey publishes as the active pointer.
        default_version_id = summary.get("version_id") or prompt_detail.get("version_id")

        # Provider info from the prompt-level object (shared across versions).
        prompt_vk = prompt_detail.get("virtual_key") or summary.get("virtual_key") or ""
        prompt_meta = (
            prompt_detail.get("template_metadata")
            or summary.get("template_metadata")
            or {}
        )

        for v in versions:
            version_num = v.get("prompt_version") or v.get("version") or 1
            try:
                version_num = int(version_num)
            except (TypeError, ValueError):
                version_num = 1

            # Content lives in prompt_template.string for versions list,
            # or directly in v["string"] when falling back to prompt detail.
            template = v.get("prompt_template") or {}
            raw_string = template.get("string") or v.get("string", "")


            system_msg = ""
            user_msg = ""
            try:
                messages = json.loads(raw_string) if raw_string else []
                if isinstance(messages, list):
                    for msg in messages:
                        role = msg.get("role", "")
                        content = _extract_text(msg.get("content", ""))
                        if role == "system":
                            system_msg = content
                        elif role == "user":
                            user_msg = content
            except (json.JSONDecodeError, TypeError):
                user_msg = raw_string

            # Provider is a prompt-level attribute shared across versions.
            from config.settings import PROVIDER_VIRTUAL_KEYS
            provider_from_vk = next(
                (p for p, slug in PROVIDER_VIRTUAL_KEYS.items() if slug and slug == prompt_vk),
                "",
            )
            provider_name = (
                prompt_meta.get("provider")
                or provider_from_vk
                or prompt_vk
                or "portkey"
            )
            name = summary.get("name") or v.get("name") or ""

            normalised.append(
                {
                    "id": str(uuid.uuid4()),
                    "template_id": prompt_id,
                    "version": version_num,
                    "name": name,
                    "is_default": bool(default_version_id and v.get("id") == default_version_id),
                    "timestamp": v.get(
                        "created_at",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                    "system_prompt": system_msg,
                    "user_prompt": user_msg,
                    "provider": provider_name,
                    "response": None,
                    "source": "portkey",
                }
            )

    print(f"[sync] Fetched {len(normalised)} prompt version(s) from Portkey")
    return normalised
