# starfire.ai

A terminal-themed AI chat interface. Runs local models through [Ollama](https://ollama.com)
(auto-detected on startup - and started automatically if it's installed but not already
running - or add one manually) and optionally talks to hosted providers
(OpenAI, Anthropic, or any OpenAI-compatible API - Groq, OpenRouter, LM Studio, llama.cpp,
vLLM, ...) via API keys you add in Settings. Nothing is sent anywhere unless you add a
hosted provider yourself.

Built on FastAPI, sharing its provider architecture and encrypted key-storage pattern with
[odysseus-dev](https://github.com), scaled down to a single-user local chat app.

## Requirements

- Python 3.12+
- [Ollama](https://ollama.com/download), installed and running, for local models (optional
  if you only plan to use hosted providers)

## Setup

```bash
cd starfire.ai-main
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn server:app --reload
```

Then open [http://localhost:8080](http://localhost:8080).

If Ollama is already running on its default port, it's detected automatically on startup
and appears in the model list with no setup. Otherwise, open **Settings → Providers** and
either click **add ollama** (fills in the default endpoint, then Test/Add) or **scan
network** to probe common local model-server ports.

## Adding a hosted provider

Settings → Providers → "Add API provider": pick OpenAI, Anthropic, or "Other
(OpenAI-compatible)", paste an API key, Test, then Add. The key is encrypted at rest - see
below.

## Chat

Every message has a small hover toolbar: **copy** (assistant replies, your own messages, and
each individual code block), **edit**, and **regenerate**. Both edit and regenerate branch the
conversation - rather than mutating history in place, they create a new chat session pointing
back at the original up to that point, so you can follow either version afterward (see Chats
below). A **stop** button appears while a reply is streaming. Every conversation autosaves as
you go, and the history sent to the model is trimmed to a fixed token budget automatically -
there's no per-chat context-size control to configure.

If a model likely doesn't support tool-calling (or is small enough - under ~3B parameters -
to be unreliable about *when* to use a tool rather than just answering directly), a banner
warns you before you enable tools for it, and the backend withholds the tools list from that
model's requests either way.

## Chats

Settings → Chats lists every saved conversation (autosaved after each exchange - nothing to
click to save). Click one to reopen it; "new chat" in the header starts a fresh one. A
conversation you haven't sent a message in yet is also kept as a local draft (browser
`localStorage`) so a crash or an offline moment before the first backend save doesn't lose it.

## Tools (MCP servers)

Settings → Tools lets the model call external tools via the
[Model Context Protocol](https://modelcontextprotocol.io). **Fetch** and **Memory** (two of the
three official reference servers) are installed and connected automatically on first run - just
enable their checkbox to use them in a chat. **Filesystem** (read/write within a directory you
choose) stays a manual "Quick add" since it needs that directory choice first.

Filesystem and MCP Memory need [Node.js](https://nodejs.org) (`npx`) installed locally; Fetch
needs [uv](https://docs.astral.sh/uv/) (`uvx`). Beyond those three, a **server repository**
dropdown lists other common MCP servers (Git, GitHub, GitLab, SQLite, Brave Search, Slack,
Puppeteer) with a per-server configure form for whatever it needs (an API key/token, a local
path, etc.) - or add any other stdio MCP server yourself by command + args. Enable a server's
checkbox to include its tools in the next chat message - supported for Ollama (models with
tool-calling support), OpenAI, and Anthropic.

The same Tools tab also has several built-in (non-MCP) tools - see Memory, Documents, Notes,
Automations, and Email below for most of them, plus:

- **Search the web** - a single no-API-key backend (DuckDuckGo's HTML results page); see
  `web_search.py`. Swapping in a proper provider later is a one-function change.
- **⚠️ Run shell commands** - off by default. One general-purpose shell tool the model can call
  directly, the same way Claude Code itself uses one Bash tool rather than a curated command
  set. **There is no sandbox under it** - it runs as your own user on your own machine, with
  only a timeout (120s) and an output-size cap as guardrails. Only enable this in a chat (or an
  automation, see below) that won't also be reading untrusted content (a webpage, an email) in
  the same conversation - a tool the agent can call is reachable by prompt injection, and this
  one has no floor under it if that happens. `ssh host 'command'` through this same tool covers
  SSH; there's no separate SSH-specific tool.

## Memory

Settings → Memory is a persistent fact store - "user prefers dark mode", "user's dog is named
Rex" - that survives across chats. Add facts manually there, or just type `remember: ...` in
the chat (handled instantly, without calling the model at all). Enable "Remember & recall
facts" in Settings → Tools to let the model itself list, add, edit, delete, or search your
memory during a conversation. Search is keyword/token overlap by default - fast,
dependency-free, no setup - **optionally blended with semantic similarity** when a local Ollama
with an embedding model (`nomic-embed-text` by default) is reachable, so a paraphrase with no
shared words can still surface. No Ollama/embedding model pulled → falls back to exactly the
old keyword-only behavior, automatically, with no configuration needed either way.

## Documents

Settings → Documents lets you upload `.txt`, `.md`, or `.pdf` files, which are chunked and
indexed for the model to search. Enable "Search my documents" in Settings → Tools so the model
can pull in relevant passages while answering. Same keyword-search-by-default,
semantic-when-available behavior as Memory above.

## Notes

Settings → Notes is a to-do list / notes app - plain notes or checklists, with due dates,
repeat (daily/weekly/monthly/yearly), pinning, labels, and color. Due dates accept natural
language - "tomorrow", "next friday", "in 3 days", each optionally with "at 9am" / "at 14:30" -
as well as a plain `YYYY-MM-DD` or full ISO datetime; anything not recognized as one of those
phrases is assumed to already be an ISO date and passed through unchanged. Enable "Manage notes
& to-dos" in Settings → Tools to let the model add/update/delete/toggle items itself (using the
same natural-language dates).

**Templates** - save a note/checklist's shape (title, content or checklist items, type, label,
color, repeat) and instantiate it with one click instead of rebuilding a recurring structure by
hand. Five starter templates (Meeting notes, Daily to-do, Weekly review, Shopping list, Project
brainstorm) are seeded on first run.

Two things worth knowing:
- **Recurring due dates only advance while the Notes tab is open** - a lightweight timer
  checks for overdue repeating notes and pushes their due date forward one period at a time.
  This matches odysseus's own actual behavior (its backend never rewrites a due date either;
  only a browser tab open on its Notes page does) - not a shortcut invented for this port.
- **No reminder popups, emails, or push notifications** - odysseus's equivalent is a genuinely
  large subsystem (four delivery channels, SSRF-guarded webhooks, a background poller, a dedup
  cache). This version shows overdue/due-today notes with a highlighted badge in the list
  instead. If you want to be notified outside the app, that's not built.

## Local generation (image/audio/video)

Settings → Local Gen configures fully local image, audio, and video generation - no API key,
no hosted service, no content moderation layer other than whatever model you load:

- **Image** - via [ComfyUI](https://github.com/comfyanonymous/ComfyUI), a plain local server
  (same trust model as Ollama). Point starfire at your ComfyUI base URL and checkpoints
  directory; a generic URL-based downloader can pull any checkpoint file you give it a link to
  - starfire doesn't curate or recommend which ones. Low/medium/high quality presets trade off
  steps and resolution.
- **Audio** - via [Piper](https://github.com/rhasspy/piper), a one-shot local CLI (not a
  server) with low/medium/high quality voice variants you download per-voice.
- **Video** - no fixed built-in workflow (the ecosystem is too fragmented for one); export a
  working ComfyUI workflow graph as JSON, tell starfire which node/input holds the prompt text,
  and save it under a name. The `generate_video` tool then queues it by name.

Enable the corresponding tool (Generate image / Generate audio / Generate document) in Settings
→ Tools. If local generation isn't configured, image/audio generation fall back to OpenAI's API
(DALL-E/TTS) when a hosted OpenAI endpoint is configured - otherwise they're unavailable.

## Automations

Settings → Automations schedules a prompt to be sent to a model on a repeating basis (once,
daily, weekly, or a cron expression) and records what came back. Pick which tools that run may
use from the same list as chat (memory, documents, tasks, email, notes, web search, and - if you
choose to enable it - shell). There's no separate "run a shell command" task type the way
odysseus has one; a task is "send a prompt, with these tools available" and reuses the model's
normal tool-calling for that, one mechanism rather than two. The same warning as the chat
toggle applies: only give an automation the shell tool if it isn't also going to encounter
untrusted content.

## Email

Settings → Email connects a mailbox via IMAP/SMTP with an **app password** (Gmail, Outlook,
iCloud, Fastmail, or any provider that issues one - no OAuth, no Google Cloud project setup).
Once connected you get a simple inbox: browse folders, read, reply, send, mark read, archive,
delete, plus three on-demand AI actions per message - Summarize, Check Urgency, and AI Draft
Reply. Enable "Read & send email" in Settings → Tools to let the model do the same during a
conversation.

Every action opens a live IMAP/SMTP connection - there's no background inbox sync or caching,
so listing a folder takes a moment each time rather than being instant from a local cache.
That's the deliberate tradeoff for not needing a database or a background sync service.

## Compare

The **compare** button (header) opens a side-by-side view: one prompt, sent to as many
model/endpoint columns as you add, streamed in parallel. Useful for actually seeing how two
models answer the same thing rather than guessing.

## Deep Research & GitHub

Two more builtin tools in Settings → Tools:

- **Deep research** - searches the web, reads the top results, and returns a synthesized,
  cited report in one tool call. Slower and more thorough than the plain "Search the web" tool;
  use it for open-ended questions, not quick lookups.
- **GitHub (gh CLI)** - runs the `gh` binary only (not arbitrary shell), so the model can list
  issues, open PRs, check CI, etc. Needs `gh` installed and already authenticated
  (`gh auth login`) - this tool doesn't manage credentials itself.

## Presets

Settings → Presets saves the current (model + system prompt + enabled tools) as a named preset
you can reapply with one click, instead of reconfiguring the header and toggles every time.

## Usage

Settings → Usage shows estimated token counts and cost, today and all-time, broken down by
model. Token counts are estimated (~4 chars/token) and cost is looked up from a small static
price table for known hosted models - not a substitute for your provider's own billing page,
but enough to see where usage is trending. Ollama/local models always show $0.

## Hardware

Settings → Hardware detects this machine's RAM/VRAM (capacity - does a model fit?) and
CPU/GPU/RAM identity and speed (roughly, will it run well?), then suggests which common
Ollama-pullable models should actually work - instead of guessing and hitting an
out-of-memory error or a model that swaps to disk and crawls. Detection works on Linux, macOS,
and Windows; GPU VRAM/name needs `nvidia-smi` (NVIDIA only), and RAM speed/type needs
`dmidecode` **and root** on Linux specifically (macOS/Windows can read it as a normal user).

**Ollama status** is also shown here: if it's installed but not running, a **Start Ollama**
button launches it (no elevated privileges needed - same as running `ollama serve` yourself);
starfire also tries this automatically on its own startup. If Ollama isn't installed at all,
the tab shows the right install step for your OS (the official installer command on Linux,
a download link for macOS/Windows) - starfire won't run that installer for you, since Ollama's
own Linux installer needs `sudo`.

### Pulling models

Click **pull** next to any suggested model in the Hardware tab (or in Settings → Providers) to
download it through Ollama with a progress bar, same UX as Ollama's own CLI. To pull a model
manually instead - useful for scripting, a headless box, or a model not in the suggested
list - use Ollama's CLI directly:

```bash
ollama pull llama3.1:8b        # any model from https://ollama.com/library
ollama pull qwen2.5-coder:7b
```

A model pulled this way shows up in starfire's model list immediately; no restart needed.
`ollama list` shows what's already pulled, and `ollama rm <model>` removes one.

## Backup & restore

Settings → Data can download a zip of the whole `data/` directory, or restore one - the same
thing the manual "copy the data folder" step below does, through the UI. Restoring **overwrites
your current data** and can't be undone; back up first if you're not sure.

## Command palette

`Ctrl+K` / `Cmd+K` opens a fuzzy-filterable list of actions - new chat, export, open any
settings tab, compare models, toggle theme. Arrow keys to navigate, Enter to run, Escape to
close.

## Docker

```bash
cp .env.example .env
docker compose up -d --build
```

A host-run Ollama is reachable from inside the container via `host.docker.internal`.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8080` | Port the server listens on |
| `OLLAMA_BASE_URL` | _(auto-detect)_ | Override auto-detection with a specific Ollama URL |
| `DATA_DIR` | `./data` | Where endpoint config and encrypted API keys are stored |

Hosted-provider API keys are **not** set via environment variables - add them through the
Settings UI so they're encrypted at rest rather than sitting in plaintext in `.env`.

## Data & security

- `data/endpoints.json` - configured model endpoints (base URLs, non-secret).
- `data/api_keys.json` - API keys, encrypted with [Fernet](https://cryptography.io/en/latest/fernet/).
- `data/.key` - the per-install encryption key, `chmod 600` (owner-only).
- `data/mcp_servers.json` - configured MCP servers (command/args, non-secret).
- `data/memory.json` - your remembered facts.
- `data/documents.json` - uploaded documents, chunked and indexed for search.
- `data/tasks.json` / `data/task_runs.json` - scheduled automations and their run history.
- `data/email_accounts.json` / `data/email_rules.json` - email account metadata and rules
  (host/port/username, match/action config - all non-secret); the app password itself is
  encrypted in `data/api_keys.json` alongside provider API keys.
- `data/notes.json` / `data/note_templates.json` - your notes/checklists and saved templates.
- `data/chat_sessions.json` - your saved conversations (including branches).
- `data/presets.json` - saved (model + system prompt + tools) bundles.
- `data/usage.json` - estimated token/cost history (see Usage above).
- `data/comfyui_config.json` / `data/piper_config.json` / `data/custom_workflows.json` - local
  image/audio/video generation config (see Local generation above); non-secret, no API keys
  involved.
- `data/generated_files.json` + `data/generated/` - metadata and file bytes for anything
  generated (images/audio/documents).

Back up or delete the whole `data/` directory to reset the app (or use Settings → Data's
one-click backup/restore); deleting `data/.key` makes any stored keys unrecoverable.

## Features

- Multi-provider chat: Ollama (local or Ollama Cloud), OpenAI, Anthropic, and any
  OpenAI-compatible server, over one unified streaming UI
- Ollama auto-detected and auto-started on startup; one-click install guidance, manual add,
  or network-scan for anything else
- Conversation branching (edit/regenerate fork into a new session rather than overwriting
  history), autosaved chats with search
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
- Incremental markdown rendering while responses stream
- Light/dark theme toggle
- Optional system prompt (saved in browser), export chat as markdown
- Runs 100% locally unless you opt in to a hosted provider - no telemetry, no accounts

## License

MIT
