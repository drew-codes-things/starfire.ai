# Features

- Multi-provider chat: Ollama (local or Ollama Cloud), OpenAI, Anthropic, and any
  OpenAI-compatible server, over one unified streaming UI
- Ollama auto-detected and auto-started on startup; one-click install guidance, manual add,
  or network-scan for anything else
- Light, dark and system themes from one token set, with no flash on load; toasts; a
  keyboard shortcuts sheet; an account menu; phone layout with a drawer and bottom sheets
- Chat-app layout: chat sidebar with folders, pins, tags, archive and date groups;
  searchable model picker with a default model; `/` prompts, `#` knowledge, `@` model
  mentions; file attachments; chat controls (system prompt + sampling params); temporary
  chats; `/side` and `/btw` side chats; hands-free voice call; reply rating, info, continue
  and in-place edit
- Conversation branching (edit/regenerate fork into a new session rather than overwriting
  history), autosaved chats with search, JSON export/import (including Open WebUI exports)
- Encrypted local API-key storage, no plaintext secrets on disk
- Tool-calling via MCP servers (filesystem, fetch, memory, a repository of common servers with
  per-server configure forms, or any custom stdio server), with a warning - and server-side
  enforcement - when the selected model likely can't use tools reliably
- Persistent memory (keyword-searchable facts) and document RAG (upload & search .txt/.md/.pdf),
  both usable by the model as tools or managed directly in Settings
- Scheduled task automation (once/daily/weekly/cron prompts, run history) and email
  (IMAP/SMTP with an app password: browse/read/send/reply/archive, rules that act on new mail
  automatically, plus on-demand AI summarize/urgency-check/draft-reply), both usable by the
  model as tools too
- Notes and checklists with due dates, repeat, pinning, labels, and reusable templates -
  usable by the model as a tool too
- Fully local image/audio/video generation (ComfyUI/Piper, no API key, no hosted moderation)
  with a hosted-API fallback when configured
- Hardware detection (RAM/VRAM capacity, CPU/GPU/RAM identity and speed, cross-platform) with
  model-fit suggestions and one-click pulls with progress bars
- Incremental markdown rendering while responses stream; markdown, sanitiser, syntax
  highlighting and the IBM Plex fonts are bundled in `static/vendor/`, so the UI makes no
  request to any CDN or font service and works offline
- Shell commands wait for your approval in the chat; find real images on the web; connect MCP
  servers over streamable HTTP or SSE as well as stdio; tool levels sized to the local model
- Code blocks are syntax-checked server-side when a reply finishes, with an automatic fix
  round-trip to the model and a copy button on every block
- Optional system prompt (saved in browser), export chat as JSON, markdown or text
- Multiple accounts, each with its own chats, memory, knowledge, notes, automations and email,
  managed by the admin from Settings → Accounts
- Runs 100% locally unless you opt in to a hosted provider - no telemetry, no cloud accounts
