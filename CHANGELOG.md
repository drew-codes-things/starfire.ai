# Changelog

## Unreleased

### Added
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

### Fixed
- Settings, menus, the command palette and the slash menu were see-through over the chat.
- Browsers kept old app files after an update; HTML, JS and CSS are now revalidated on load.
- Helper requests to Qwen3 on Ollama came back empty (they spent the whole budget thinking).
- Docker images could include `.env` and `data/`; they're now excluded by `.dockerignore`.
