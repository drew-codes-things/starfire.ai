# Changelog

## Unreleased

### Added
- Approval cards (Arty CLI Allow/Deny, a shell command's run it/reject, a proposed edit's
  approve/reject) show a short "New question" flash and ignore clicks, taps, Enter and Space on
  their buttons until they have been on screen for one second, so a tap aimed at something that
  just moved, or keys meant for something else, cannot answer them.
- Arty CLI page: a shared terminal opens with its whole conversation so far (sent by Arty CLI
  when sharing starts, marked where sharing began), a compacted conversation shows as a marker
  that opens to the summary Arty carries on from, and each terminal is listed under its own
  title (folder name, numbered when two share a folder) with the computer beside it. The page
  keeps up to 2000 transcript items.
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
- The licence is now Apache-2.0, with a NOTICE file that credits the author.
- Toasts show at the top centre, so they no longer cover the message box.
- On phones the chat controls dim the chat behind them and close with a tap outside, and the
  Arty CLI live view uses the whole screen with a one-line header.
- Compare starts each column on a different model and shows where the reply will land.
- Enter adds a memory, a checklist item or a preset from its one-line field.
- In the sidebar a chat's title gets the room first and its tags take what is left.
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
- `static/app.js` (4,318 lines) is split into thirteen smaller scripts that load just before
  it: `appState.js`, `chatMessages.js`, `codeBlocks.js`, `chatCards.js`, `chatSend.js`,
  `helperTasks.js`, `settings.js`, `settingsMemoryTasks.js`, `settingsEmail.js`,
  `settingsNotes.js`, `settingsSystem.js`, `compare.js` and `palette.js`. `app.js` keeps the
  keyboard and focus handling, the microphone, the theme and startup. Nothing a user sees
  changes.
- `static/workspace.js` (1,553 lines) is split the same way into eleven scripts that load
  after `app.js`: `workspaceCore.js`, `sidebar.js`, `sidebarMenus.js`, `chatManagement.js`,
  `prompts.js`, `modelPicker.js`, `chatControls.js`, `composerTools.js`, `attachments.js`,
  `composerPopup.js` and `voiceCall.js`. `workspace.js` keeps the scroll button, the keyboard
  shortcuts, the account menu, the shortcuts sheet and startup. Nothing a user sees changes.
- More server tests (394 to 628): one account reaching another's items by id, admin-only
  routes, backup restore with unsafe paths, API key storage and which endpoint gets which key,
  edit and shell approvals, scheduled tasks, email rules, the model's built-in tools and chat
  provider routing. Server line coverage goes from 64% to 78%. Nothing a user sees changes.
- More server tests (689 to 803) on the parts that were still thin: the model's tool loop
  (tool errors, model errors, stopped and cancelled replies), semantic search with and without
  an embedding model, finding Ollama and local model servers, picture, speech, video and
  document generation through ComfyUI, OpenAI and Piper, the hardware page on Linux, macOS and
  Windows, and IMAP and SMTP email, all against fakes. Server line coverage goes from 79% to
  89%. Four bugs they found are listed under Fixed.

### Fixed
- MCP server arguments and URLs are encrypted in `mcp_servers.json` like env and header values,
  so a connection string with a password or a URL with `?api_key=` is no longer in plain text
  on disk; a plain file is encrypted the first time it is read. Backups keep the arguments and
  URL with their credentials replaced by `__secret_removed__` (URL passwords, key, token,
  secret, password and auth query values, the value after flags such as `--api-key`, and
  `NAME=value` or `Name: value` pairs with such names), and Settings marks those servers.
  Servers added with the URL as their name keep it without the credentials.
- Restoring a backup on the install that made it keeps the MCP tokens, header values,
  arguments and URLs of the servers that still exist there, instead of blanking them.
- Restore refuses an archive member with a `.` or `..` part or an absolute path, and checks
  credential files by the name zipfile will write, ignoring case, so `.key/.` or
  `sessions.json/..` can no longer replace this install's key or sessions.
- Backups leave out temp and backup copies of `.key`, `.env`, `sessions.json`, `api_keys.json`
  and `mcp_servers.json` (`.sessions.json.*.tmp`, `mcp_servers.json.bak`, `.key.bak` and so
  on), and restores skip them.
- A rollback followed by an upgrade no longer encrypts MCP secrets a second time: each value is
  checked on its own, and a token this install's key cannot open marks the server as needing
  its secret again instead of being passed on.
- Info lines from the app (the MCP encryption notice, what a restore left out, tightened file
  permissions, an SMTP hang-up at quit) now reach stderr and the journal; libraries stay at
  warnings. MCP connection errors and log lines show server URLs without their credentials.
- A memory or document search with Ollama running but no embedding model makes one embedding
  request instead of one per entry or chunk before falling back to keywords.
- Errors the server used to swallow in silence are now logged with what failed: a store file
  that is not JSON (read as empty, and replaced by the next save), a temp file that could not
  be removed, a scheduled time that is not HH:MM, a failed web search or embedding, a key file
  whose permissions could not be tightened, a background task failing at shutdown. Nothing a
  user sees changes.
- On a phone the actions under the last reply stay on one row: the buttons narrow a little to
  fit, and the regenerate button opens the model list itself (picking a model regenerates with
  it). Wider screens keep the model select beside the button, now headed "Regenerate with…".
- The default-model star is a plain filled star instead of a red disc.
- The Arty CLI message box grows with its text, up to 200 px, and shrinks back after sending.
- Settings lists show "loading…" while they load the first time instead of an empty space.
- Adding a memory that fails says why under the field and keeps the text.
- The account select in Email rules says there are no email accounts yet, with Add rule
  switched off, instead of being blank; it also follows an account being removed.
- The plus menu works from the keyboard: it focuses its first item when opened, Up and Down
  move (and wrap), Home and End jump to the ends, and Escape closes it and returns to the
  button.
- Requests sent straight to Ollama (chats, titles, tags, follow-ups and the model warm-up) ask
  for `TASK_NUM_CTX` as the context size (now 24576 by default, was the model default) and turn
  context shift off, as Arty's brain does, so they no longer make Ollama reload a model the
  brain already has loaded. A context length set on a chat is still used as set.
- Enter in a dialog field no longer reopens the New folder dialog or leaves a new line in the
  message box, and Escape on a dialog, a chat row menu or the model menu no longer also closes
  Settings behind it or stops a reply that is streaming.
- A branch made by editing the first message or regenerating the first reply is named after
  that message instead of "New chat".
- A request the model server refuses shows the server's message instead of raw JSON.
- Port, time and weekday fields in Settings are narrow again.
- The login and setup pages now load the IBM Plex fonts: `/vendor/fonts.css` and
  `/vendor/fonts/` are reachable without a login.
- Non-admins no longer open Settings on the hidden Connections tab.
- Text that was below WCAG AA contrast (hints, placeholders, group labels, and success and
  warning text in the light theme).
- Sign-in lockouts behind a proxy count the visitor's real address (`TRUSTED_PROXIES`,
  `CLIENT_IP_HEADER`), so a stranger can no longer lock the owner out of the sign-in pages
  reached through a Cloudflare tunnel. 20 wrong passwords for one email in an hour lock it for
  an hour, except from addresses that signed in before or a browser already signed in. Failed
  sign-ins are logged, and the lockout table no longer grows without limit.
- A login only works on the host it was made on (`/api/auth/me` still answers local services
  on `127.0.0.1:<PORT>` and `SESSION_CHECK_HOSTS`). Logins from before this change must sign in
  once more.
- Only `remember: ...` (with the colon) in the main chat saves a memory. Messages starting with
  save, note, store or remember go to the model, and temporary and side chats never save.
- Uploads have limits: documents (`MAX_DOCUMENT_MB`), attachments per account
  (`MAX_ATTACHMENTS_MB`), restores (`MAX_RESTORE_MB`) and a free disk reserve
  (`MIN_FREE_DISK_MB`). Backup and restore no longer hold the whole zip in memory, and text
  without full stops is split into normal-sized pieces.
- Generated files whose names hold non-Latin characters download instead of failing.
- Attachments and generated files are sent with `nosniff` and a sandbox policy, and only PNG,
  JPEG, GIF and WebP images can be attached.
- Every response carries `nosniff`, no-framing and same-origin referrer headers.
- `/assets/../app.js` no longer serves app code without a login.
- A chat message whose content is not text gets a 422 instead of a server error.
- Setup stays closed if `data/users.json` goes missing after setup.
- An unreadable `api_keys.json` or `documents.json` is moved aside instead of being
  overwritten with an empty one.
- Normal accounts no longer see MCP server commands, arguments or full URLs, and cannot start
  a network scan or read the hardware page.
- Approving a waiting shell command or file edit checks that the account is still an active
  admin, so an admin demoted after the command or edit was proposed gets a 403 and it stays
  waiting.
- `api_keys.json` is written readable by its owner only (600), like `.key`, and an existing
  file left readable by others is tightened when it is next read, with an info log line.
- MCP server environment values (tokens) and header values are stored encrypted in
  `mcp_servers.json` with the same key as `api_keys.json`. A file saved in plain text is
  encrypted the first time it is read; values saved by another install are left out with a
  warning naming the server and key, and kept on disk. The API still never returns them.
- Two requests changing the same saved file at once no longer lose one of the changes: every
  store holds a lock on its file while it reads, changes and rewrites it. Searching documents
  or memories no longer writes back an older copy over something added meanwhile, and two
  first uses of the encryption key no longer race to create `.key`.
- Backups carry no credentials: the zip leaves out `.key`, `api_keys.json` (provider keys and
  email passwords), `sessions.json` and any `.env`, and blanks MCP server env and header values.
  Account records keep their bcrypt hashes so people can still log in. Restoring keeps this
  install's own keys and sessions, also from an older zip that still holds them, and Settings
  marks email accounts and MCP servers whose password or token must be entered again.
- Documents the model makes as PDF are made again: any PDF with more than one line failed with
  "not enough horizontal space" (each line started at the right edge of the one before), and a
  heading with a character outside Latin-1 (an emoji, a tick) failed too; such characters now
  print as "?" in headings as they already did in body text.
- With Ollama running but no embedding model pulled (or embeddings failing), searching memory
  or knowledge gives exactly the keyword results, as the README promises, instead of every
  memory or document ranked by nothing: a question about something you never saved no longer
  pulls unrelated memories and passages into the chat.
- Sending email no longer reports a failure when the mail server hangs up at the very end: a
  message the server had accepted was shown as not sent (so it could be sent twice), and a
  wrong password could show up as "Connection unexpectedly closed" instead of the server's
  reason.
- A command that hangs past its time limit is stopped instead of left running: a stuck
  `nvidia-smi`, `lscpu`, `dmidecode` or PowerShell query behind the hardware page (one more each
  time the page opened, now logged as a warning), and a stuck Piper voice.

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
