# Changelog

## Unreleased

### Added
- Arty devices page (`/arty-devices.html`, admins only, linked from Settings > Accounts):
  approve a computer waiting to join Arty (opened from the QR code or link Arty shows, with the
  code typed to match), list approved devices and turn one off, and make a named key with a
  one-time copy button and a phone setup QR code. Set `ARTY_BASE_URL` to use it.
- Logging in returns you to the page you asked for, so an approve link still works after the
  login page.
- Light, dark and system themes (Settings → Appearance). The stored theme is applied before
  the page paints, the browser theme colour follows it, and the login and setup pages use it
  too. The old `sf_light` setting is still read and kept in step.
- Account menu at the bottom of the sidebar: Settings, Archived chats, Keyboard shortcuts,
  Log out.
- Keyboard shortcuts sheet (`Ctrl+/`, or from the account menu). `Esc` now stops a reply that
  is streaming.
- Toasts for copy, save and error feedback. Sidebar errors no longer print into the chat.
- Typing indicator until the first words of a reply arrive; an offline banner when the server
  cannot be reached; loading placeholders in the sidebar.
- A centred greeting with the message box in the middle and suggestion chips on a new chat.
- Confirmation before removing a connection, MCP server, document, automation, email account,
  note, memory, email rule, note template, preset, workflow or single message.
- Chat controls are kept per chat. A chat stores its own `system_prompt` and `params`
  (`PUT /api/chat/sessions/{id}` accepts them, `GET` returns them, clone, export and import
  carry them). What you set on a new chat stays your default, and chats saved earlier use the
  defaults. "Use my defaults" and "Save as my defaults" in the controls panel.
- Shell commands and file edits waiting for approval are drawn again when the page loads,
  from `GET /api/pending-shell` and `GET /api/pending-edits`, and stay on screen through a
  rerender.
- JavaScript tests run with `node --test tests/js/` (no new dependencies): date groups, theme
  choice, the inline theme script on all three pages, toasts, the remove confirmation,
  approval restore, per-chat controls. CI runs them.
- `tests/test_static_ui.py`: unique ids, labelled controls and buttons, script lookups that
  match the page, icon sprite coverage, no browser dialogs, colours only from tokens, and
  WCAG AA contrast for every text colour on every surface in both themes.

### Changed
- New visual design from one set of tokens in `static/style.css` (colours, radii, spacing,
  shadows, type sizes), with a softer dark background, neutral greys, and red kept for the
  send button, active states and danger. One outline icon set replaces the text glyphs.
- Every native control is themed: checkboxes, switch toggles, radios, selects, number, search
  and file inputs, range sliders and progress bars, with focus rings and disabled states.
- Settings tabs: Interface is split into General, Appearance and Account; admin tabs sit in
  their own Admin group. Settings opens on General and each tab loads its data when opened,
  instead of every tab's data on every open.
- The send button turns into a stop button while a reply streams. A message that fails to
  send is put back in the message box.
- Phone layout: safe-area insets, 16px inputs, 44px touch targets, menus as bottom sheets,
  message and chat actions always visible on touch screens.
- Dialogs trap focus and hand it back when they close; form controls have accessible names.
- Rating, deleting or editing a message keeps the scroll position.
- The message box hint and the shortcuts sheet follow the "Ctrl+Enter sends" setting.
- Vendor scripts load at the end of the page instead of blocking the first paint.
- Regenerate, with its model chooser, is offered on the newest reply only.
- Static files are sent gzip-compressed to browsers that accept it (about 548 KB of page,
  style and script down to about 138 KB). API replies and streams are not compressed.
- Testable browser logic moved into `static/uiLogic.js`.

### Fixed
- The login and setup pages now load the IBM Plex fonts: `/vendor/fonts.css` and
  `/vendor/fonts/` are reachable without a login.
- Non-admins no longer open Settings on the hidden Connections tab.
- Text that was below WCAG AA contrast (hints, placeholders, group labels, and success and
  warning text in the light theme).

## 1.1.0 - 2026-11-03

### Added
- Multiple accounts. Each account owns its chats, folders, memory, knowledge, notes,
  automations, email, prompts, presets, usage, attachments and generated files under
  `data/users/<id>/`. Connections, MCP servers, local generation, hardware and backup stay
  shared and admin-only.
- Settings → Accounts for admins: add an account, change its role, reset its password,
  disable or re-enable it, delete it with its data. `GET/POST /api/auth/users`,
  `PATCH/DELETE /api/auth/users/{id}`, `POST /api/auth/users/{id}/password`.
- Non-admins get a 403 on shared settings and don't see those tabs.

### Changed
- A single-user install's data moves into the first admin's folder on the first start.
- `import_openwebui_data.py` takes `--user EMAIL` (default: the first admin).
- `/api/auth/me` and the login reply include the account id, `disabled` and `created`.

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
