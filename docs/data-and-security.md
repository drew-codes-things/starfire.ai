# Data and security

- `data/endpoints.json` - configured model endpoints (base URLs, non-secret).
- `data/api_keys.json` - API keys, encrypted with [Fernet](https://cryptography.io/en/latest/fernet/),
  `chmod 600` (owner-only).
- `data/.key` - the per-install encryption key, `chmod 600` (owner-only).
- `data/mcp_servers.json` - configured MCP servers; env and header values (tokens), arguments,
  URLs and browser sign-in tokens are encrypted with the same key as `api_keys.json`.
- `data/users.json` / `data/sessions.json` - accounts (bcrypt hashes) and login sessions.
- `data/users/<id>/` - one folder per account holding that person's `memory.json`,
  `documents.json`, `tasks.json` / `task_runs.json`, `email_accounts.json` / `email_rules.json`
  (host/port/username, match/action config - all non-secret; the app password itself is
  encrypted in `data/api_keys.json` alongside provider API keys), `notes.json` /
  `note_templates.json`, `chat_sessions.json`, `chat_folders.json`, `presets.json`,
  `prompts.json`, `usage.json`, `generated_files.json` + `generated/`, and `attachments/`.
- `data/comfyui_config.json` / `data/piper_config.json` / `data/custom_workflows.json` - local
  image/audio/video generation config (see [local generation](media-generation.md)); non-secret, no API keys
  involved.

Back up or delete the whole `data/` directory to reset the app (or use Settings → Data's
one-click backup/restore, which leaves keys and passwords out); deleting `data/.key` makes any
stored keys unrecoverable.
