# Changelog

## 1.0.0 - 2026-11-03

### Added
- Shell commands proposed by the model wait in the chat for "run it" or "reject" (on by
  default, five-minute timeout); the model only sees the output after approval.
  `GET /api/pending-shell` and approve/reject routes. Automations still run unattended.
- `find_image` tool: DuckDuckGo image search, server-side download with type, size and
  private-address checks, shown inline with its source link.
- MCP servers over streamable HTTP and SSE, with an optional Authorization header, from the
  custom-server form. Stdio servers are unchanged.
- Tool levels for local models: none under 3B, a basic set capped at eight tools up to 12B,
  everything from 12B; the banner above the chat says which applies.
- `reset_password.py` to set a new password from the terminal and log every browser out.
- A version number (`/api/health`, `/api/config`, Settings → Interface → about).
- `OWN_TOOLS_MODELS` and `INLINE_MEDIA_PREFIXES` settings replace the hard-coded Arty model
  ids and media path.
- IBM Plex fonts bundled locally; no request leaves the machine for fonts.
- API tests for chat sessions, import, folders, notes, tasks, prompts, memory, the code check,
  shell approval, image search, MCP validation and the reset script.
- Code blocks render in a box with the language, a copy button and a check result. Every
  block in a finished reply is parsed or compiled on the server (Python, JavaScript,
  TypeScript, shell, JSON, YAML, TOML, XML, Go, C, C++, Ruby, PHP; never run); a failing block
  is sent back to the model for a fix, up to twice, before it is offered for copying.
  `POST /api/code-check` and a Settings → Interface toggle.
- Escape closes the settings, compare and archived windows and the controls panel.
- First-time setup page: a new install asks for your name, email and password and creates the
  admin account; nothing else is reachable until then.
- Change your password under Settings → account (logs out other browsers).
- Login with bcrypt accounts, 30-day sessions and a lockout after 5 wrong passwords.
- Images, audio and video attachments.
- Auto-tags for new chats and clickable follow-up suggestions after each reply.
- `TASK_MODEL` / `TASK_NUM_CTX`: run titles, tags and follow-ups on a local Ollama model.
- Import chats, notes and logins from Open WebUI.
- `GET /api/health` for uptime monitors (no login needed).
- GitHub Actions workflow running the tests, pyflakes and a JavaScript syntax check.
- The Arty look: dark red theme with IBM Plex Sans.

### Changed
- marked, DOMPurify and highlight.js are bundled under `static/vendor/` instead of loaded
  from a CDN, so replies render markdown and highlighted code offline.
- The licence is now proprietary (use only; no copying, modification or redistribution).
- Adding a connection whose URL is already configured is refused instead of duplicating it.

### Fixed
- Replies rendered as raw markdown whenever the CDN was unreachable.
- Light mode kept the dark background behind the chat, hiding reply text.
- On phones the sidebar and controls panel were see-through over the chat, and the sidebar
  stayed open when the window became narrow; settings tabs now scroll in a row.
- Compare view: markdown output, styled column buttons.
- Native file pickers and the compare column buttons were unstyled; preset and workflow name
  placeholders showed stray backslashes.
- Settings, menus, the command palette and the slash menu were see-through over the chat.
- Browsers kept old app files after an update; HTML, JS and CSS are now revalidated on load.
- Helper requests to Qwen3 on Ollama came back empty (they spent the whole budget thinking).
- Docker images could include `.env` and `data/`; they're now excluded by `.dockerignore`.
