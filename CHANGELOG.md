# Changelog

## Unreleased

### Security
- Shell and edit approval are stored per account on the server and start on for every account.
  A chat request can only ask for more approval, never less, so a direct POST to
  `/api/chat_stream` with `require_shell_approval: false` still gets an approval card. Only an
  admin can change the stored setting (`GET`/`PUT /api/approval-policy`, the two toggles in
  Settings → Tools); the old browser-only copies of the toggles are dropped. Scheduled tasks
  follow the owner's stored setting and refuse a command that needs approval at once, since
  nobody is there to answer.
- Once a turn has read untrusted content (web search, deep research, image search, knowledge
  search, reading or summarising email, an attached file or image, any MCP tool result, or an
  earlier reply in the chat that did), every state-changing tool for the rest of that turn waits
  for an approval card even when approval is off: run_shell, edit_file, github_cli, sending,
  replying to, archiving or deleting email, creating or changing scheduled tasks, memory and note
  writes, and MCP tools. The card says untrusted content was read this turn. A scheduled task in
  that state refuses the change. An ordinary account can answer its own card for its own tools.
- run_shell and github_cli start with a scrubbed environment (PATH, HOME, locale and, for gh, its
  own GH_TOKEN/GITHUB_TOKEN only), so starfire's `.env` values never reach them. Output is read up
  to a cap and the command's whole process group is stopped on the cap or the timeout. run_shell
  starts in the account's workspace folder (`data/users/<id>/workspace`) and, when bubblewrap
  (`bwrap`) is installed, runs in a sandbox: read-only system, private /tmp and process list, the
  data folder, the app folder and starfire's home hidden, only the workspace writable. Without
  bubblewrap it logs that it runs unsandboxed. edit_file only writes inside the account's
  workspace or a folder listed in `EDIT_ALLOWED_ROOTS`, never inside the data or app folders,
  resolves links before checking, checks again on approval, and refuses before it reads the
  file, so a refused path never shows up in a diff.
- The C and C++ code check could be made to read any file the server can read (`#/**/include`,
  `/**/#include`, a form feed or vertical tab around `#include`) and gcc's error text quoted it.
  C and C++ are now only compiled inside bubblewrap with no network, a private /tmp and only the
  system program folders (`/usr`, `/bin`, `/lib*`, `/etc/alternatives`) and the snippet in view, so
  no other file on the server can be included; only the snippet's own messages are shown, and
  without bubblewrap they are not compiled at all. The include filter reads string, character and
  raw string literals the way the compiler does and refuses a comment right after `#`. Every
  checker runs with a scrubbed environment.
- Mail no longer blocks the server. Every IMAP and SMTP call from the email pages, the email
  tool and the email rules runs off the event loop with a 20 second timeout on the connection and
  every command, so a mail server that never answers (or starfire's own port given as one) cannot
  stall every other request.
- Ordinary accounts can only use mail servers on public addresses. The name is looked up once,
  every address it gives must be public, and the connection goes to that checked address while
  still asking for the certificate of the name. Admins can still use a private server. Connection
  failures show ordinary accounts a plain "could not reach the mail server" instead of the raw
  socket error; the mail server's own refusals (wrong password, unknown recipient) still show.
- IMAP, SMTP over SSL and STARTTLS now verify the server's certificate and name. A mail server
  with a self-signed certificate (for example a local bridge) no longer connects.
- An edit_file call that waits for approval no longer hands the diff (and so the file's current
  lines) back to the model; the model is told only that the edit waits. The page still shows the
  diff on the approval card, taken from the pending edit of the same account.
- github_cli only runs issue, pr, repo, release, run, workflow, label, gist, search, status, api,
  org, project, ruleset, cache and `auth status`. `gh alias`, `gh extension`, `gh config`, other
  `gh auth` commands, secrets and unknown commands (which could be aliases) are refused, since
  `gh alias set --shell` ran any program with no card. Read-only commands (list, view, status,
  diff, checks, search) run straight away; anything else follows the shell approval setting and
  always asks after untrusted content, with an approval card showing the full `gh` line.
- Chat markdown is sanitized harder: no `<style>`, forms, inputs, buttons, `<meta>`, `<base>` or
  `<link>`, no `style`, `id`, `name` or `srcset` attributes, and `class` only for code language
  names, so a reply can no longer restyle or imitate an approval card or replace the email reply
  box. Images and media from other sites lose their address, so a reply cannot send chat text
  out through an image URL with no click.
- Every response now carries a full Content-Security-Policy: scripts only from starfire (plus the
  exact hash of the inline theme script), images and media only from starfire, `data:` and
  `blob:`, connections only to starfire, no plugins, no `<base>`, forms only to starfire.
- Model names in Settings > Usage are escaped.
- The chat no longer prints "» edit_file done" (or done for any tool) when the tool returned an
  error. A refusal (not an admin, outside the workspace, rejected, not approved in time) shows as
  "» tool blocked: reason" and any other error as "» tool failed: reason"; a refused shell command's
  card says "shell command blocked". A blocked or failed tool still counts as a step in the status
  line.
- Ordinary accounts no longer see or use the uncensored models. `RESTRICTED_MODELS` (default
  `*-abl,*-abl-*,*abliterated*,*uncensored*`, matched with and without a `:tag` or
  namespace) is applied to the model list and to every
  route that sends to a model: chats, compare, quick-complete, email AI actions, new automations
  and email rules (and again when they run), warm-up and server voices. An admin can give an
  account its own list of models under Settings > Accounts > models.
- Ordinary accounts have a reply limit: `GENERATIONS_PER_MINUTE` (20) and `GENERATIONS_PER_DAY`
  (400, counted per UTC day, automations and email rules included). Over the limit the request
  gets a 429 with `Retry-After` and says when it can try again. Admins are not limited. The counts
  are kept in memory, so a restart starts them again.
- `/api/config` no longer sends the local model server's address or the own-tools patterns. Each
  model in `/api/models` says itself whether it brings its own tools, and the Settings field that
  showed the local server's address reads it from the admin-only `/api/hardware/local-server`.
  `/api/model-endpoints`, `/api/comfyui/config` and `/api/piper/config` are admin-only to read as
  well, since they hold server addresses and folders.
- Every chat now ends its system prompt with a short note from the server: keep your instructions
  private when asked to repeat or summarise them, and treat web pages, emails, documents, files and
  tool results as information, not instructions. A model with its own tools (Arty) keeps its own
  instructions untouched. No address, setting or secret is ever put into a prompt.
- Automations and email rules of a disabled account, or of a deleted account whose folder is still
  there, no longer run in the background.
- Every request body now has a size cap: 64 KB for sign-in and account routes, 256 KB for memory,
  1 MB for code checks, 8 MB for notes, 128 MB for a chat import and 32 MB for the rest, checked
  before the body is read. A too-large request gets 413.
- One address gets 10 sign-in tries a minute, whatever emails it uses, and a good sign-in clears the
  count. Five wrong current passwords on the change-password form lock it for 15 minutes. Both
  answer 429 with Retry-After.
- The session cookie is now `__Host-starfire_session` (Secure, path /, no domain), so a sibling
  subdomain cannot set or replace it. A browser still holding the old `starfire_session` cookie stays
  signed in and is moved to the new name on its next request.
- Chat settings are checked: only temperature, top p, top k, max tokens, context length, repeat
  penalty and seed are accepted, each within its range, and anything else gets a 400 that names
  the setting instead of a server error.
- At most two code checks run at once; a third gets "the code checker is busy".
- A knowledge document is read in a worker thread, a PDF over 2000 pages is refused, and one
  account's documents are limited to `MAX_DOCUMENTS_MB` (512 MB by default).
- Deep research and image downloads only connect to addresses on the public internet (shared
  carrier ranges such as 100.64.0.0/10 are now refused too), and each connection goes to the
  address that was checked, so a name that changes its answer between the check and the fetch
  cannot reach the local network.
- Code checks run every checker under `prlimit` (512 MB, 1 GB for node, 10 s CPU, 8 MB files, no
  core files, 32 processes inside bubblewrap) and inside bubblewrap with no network when it can
  start; without `prlimit` every check is skipped. Parsers (Python, JSON, YAML, TOML, XML) run in a
  child process with no fork. The server reads at most 64 KB of a checker's output, gcc and clang
  stop after 20 errors, one account runs one check at a time and at most 20 seconds of checks a
  minute, and the C include filter now also sees includes after a form feed, a vertical tab or a
  bare carriage return and removes comments with a plain scan, so a hostile snippet cannot stall
  the server.
- Mail calls run on four threads of their own, each cut off after 60 seconds; an account may run
  two at once and make `MAIL_REQUESTS_PER_MINUTE` (60) mail requests a minute. An IMAP server may
  send at most 32 MB per request, each line counted with 256 bytes more for what it costs to keep,
  and a message over 25 MB is fetched only up to 25 MB. An SMTP reply may be at most 64 KB, each of
  its lines at most 2 KB, so an endless multi-line reply is stopped at once.
- github_cli refuses flags that can print the token and the commands that run programs or change
  the sign-in, and asks first for everything but plain reads, also after untrusted content.
- Arty Auto reads only the first and last 4,000 characters of a message to pick a role, with
  patterns that take time in step with the text, so a long paste cannot stall the server.
- An account's own model list no longer opens restricted models through a pattern (`*`,
  `own/*`) or a name without its tag; only a restricted model's full name does.
- Approval cards show the action, recipient and mailbox of the call in their heading and keep the
  call text in its own scroll area, so the recipient and the run button are on screen together.
  Wide and blank-looking spaces are shown as `\u` escapes on the card.
- When bubblewrap is missing or cannot start, the run_shell approval card says the command runs
  without a sandbox, and a failed start is tried again after five minutes instead of being kept
  until the server restarts.
- Finished background replies are kept within 64 MB in total, and the reply of an account that is
  disabled or removed stops at its next chunk without being saved. A branch, a clone or an import
  of a chat that read untrusted content keeps that mark, and so does a chat whose turn carried a
  file.

### Added
- **starfire Desktop** (`desktop/`): an Electron app that opens your own starfire in its own window on Debian (AppImage;
  the Windows installer is configured but not built yet). The first run asks for the address and only saves one that
  answers `/api/health` like starfire; `http` is accepted only for localhost and local network addresses with an explicit
  tick box. It stays signed in, has a tray icon (Open, Quick ask, Settings, Quit), an optional start with the PC, a global
  quick-ask shortcut (Ctrl+Shift+Space, changeable in its settings) that sends a question as a new chat, and a notification
  when a reply finishes while the window is hidden. The window runs sandboxed with context isolation, navigation and new
  windows limited to your starfire's origin (other links open in the system browser), and its own pages carry a strict CSP.
  It has its own red logo so it is not mixed up with Arty Desktop. Unit tests run in CI (`node --test desktop/tests/*.test.js`).
- **starfire Desktop: link to Arty Desktop.** A switch in Settings with a light (Off, Not paired, Arty Desktop is not
  running, Connected). It pairs once with a 6 digit code Arty Desktop shows (single use, two minutes), talks to
  `127.0.0.1` only, keeps the token in its private config file and can ask Arty Desktop's computer control to take a
  step; Arty Desktop still puts every step to you as its Allow / Not now card. Needs Arty Desktop 1.5.5 or newer.
- **Settings > Arty memory** for admins: list and search what Arty remembers, edit a fact in place, remove a
  fact or a whole name after a confirm, and add a fact. starfire relays each change to Arty's memory API with its
  own Arty connection (`GET /api/arty-memory`, `POST /api/arty-memory/add`, `/edit`, `/delete`) and shows Arty's
  own error when it refuses. Ordinary accounts get a 403 and no tab.
- **Arty Auto** in the model picker, the default for new accounts: the server picks the vision model
  for pictures and video, the code model for code, the agent for reminders, phone, home and web
  tasks or anything addressed to Arty, and the chat model otherwise, from fixed rules with no
  extra model call. Admins set each role under Settings → Connections; Auto keeps to each
  account's allowed models. Each reply names the model Auto picked (`qwen3:14b · Arty Auto: code`).
  Arty Auto is the first entry of the model picker, followed by one flat list of every model the
  account may use (a model Arty's server offers again, such as `arty/<name>`, is shown once as the
  direct one; the same name on two other servers is listed for each, marked `server N`) and no
  headings; a small **Arty Auto** / **Choose a
  model** switch at the top shows how the chat's model is picked. Presets are no longer listed in
  the picker (apply them from Workspace → Models). Chats store Arty Auto as `auto`.
- Each chat keeps its model (or Auto) on the server and the picker shows it after a refresh, in
  another tab or on another device, instead of the first model in the list. A new chat starts
  with the account's last choice (`GET`/`PUT /api/model-choice`), or Auto.
- Replies in saved chats keep going on the server when the page is refreshed or closed; the page
  rejoins a running reply (`GET /api/chat/replies/{id}/events`), the server saves the finished
  reply into the chat, and the stop button stops it on the server (`POST .../stop`).
- A status line under the message box for the open chat: turns, tool steps, reply speed
  (tok/s), context tokens, prompt cache hit and a ring for the share of the context window used,
  updating while a reply streams. Speed, tokens and cache come from the model server's own usage
  and timing, which the chat stream now passes on as a `usage` event and each reply keeps;
  figures a server does not report are hidden, and items drop off on narrow screens.
- Sign in to online MCP services from Settings → Tools: pick Notion, Linear, Atlassian, Sentry,
  Cloudflare, Stripe, PayPal, Canva or Webflow (or type any MCP server URL) and starfire sends
  you to the service's own sign-in page, then connects the server when you come back. Follows the
  MCP authorization spec (metadata discovery, dynamic registration, PKCE S256, a one-time state
  tied to the session); tokens are encrypted, kept out of backups and renewed before they run out
  or on a 401, and a refused renewal shows "Needs sign-in again" with a button to redo it.
  `PUBLIC_BASE_URL` sets the return address when starfire has one public address.
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
- ArtyEngine-aware model server detection: startup auto-detection tries ArtyEngine's port 11438
  before Ollama's 11434 and prefers it when both answer, **scan network** probes 11438 too, and
  endpoints, the scan result and Settings → Hardware name the backend that answered (ArtyEngine
  from its `Server` header or greeting, Ollama otherwise). The **Start Ollama** button only
  appears when Ollama is installed; otherwise the Hardware tab says the backend runs on its own.
- The model server is chosen once and kept: startup detection runs only while no Ollama-API
  server is set up (added with **add ollama** or **scan network** on this machine or another, or
  an Ollama server on another machine added with an API key; Ollama Cloud and an Ollama on this
  machine added with a key do not count, and the latter is filed as Ollama in place at each start
  when its server answers Ollama's API, whatever its port and however its address names this
  machine, so it is used without a second endpoint or a launched Ollama, unless another server on
  this machine is already the chosen one or `OLLAMA_BASE_URL` names another server), a server only counts as Ollama when its `/api/tags`
  answers with a model list, port 11438 is only taken when the server
  there names itself as ArtyEngine, and another server is never added or switched to by itself
  (Settings → Hardware shows it as found, not added). Memory and document embeddings, model
  pulls and the **add ollama** default use `OLLAMA_BASE_URL` or the stored endpoint on this
  machine (its server root, also when stored with an `/api` or `/v1` path) instead of probing ports on
  every search, so once a model server is set up nothing else that answers on a port receives
  memory or document text. `127.0.0.1`, `localhost`, `0.0.0.0` and `::1` all count as this
  machine, detection never adds a second endpoint for a server stored under another of these
  names (with or without `/api`), and a damaged stored address no longer stops startup. Auto-detected endpoints are
  renamed when the backend behind them changes, and the startup detection probes give up after
  0.5 s to connect.
- A server on this machine without the Ollama API, found by **scan network** or typed into **add
  ollama**, is stored as OpenAI-compatible at its `/v1` address when `/v1/models` lists models
  (chat only, no embeddings or pulls) instead of being refused, **add ollama** refuses a server
  whose `/api/tags` gives no model list (asked under the typed address without a `/v1` or `/api`
  ending, so a proxy path works; an address ending in `/api` gets that far on this machine; elsewhere
  only when `/api` is the whole path and the address is on port 11434 or is the `OLLAMA_BASE_URL`
  address (host and port), as the first check otherwise asks `<address>/v1/models`;
  with the same 5 s limit and API key as its first check, and the
  message says whether it timed out, refused the key, answered with an error or gave no list), and
  the scan names every server it could not add and why.
  At each start a stored server on this machine is filed by the API it speaks: as
  OpenAI-compatible only when its Ollama API answers "not found" or without a model list and
  `/v1/models` lists models, and back as Ollama once its Ollama API lists models again unless another server on this
  machine is the chosen one by then, except a
  server added at its `/v1` address as anything other than Ollama, which is never filed as Ollama
  (each endpoint now remembers what it was added as, so an Ollama added with **add ollama** at its
  `/v1` address is filed back after a start without a model list); one failed answer changes
  nothing.
- **start Ollama** on Settings → Hardware asks only Ollama's own port whether it already runs (an
  ArtyEngine on 11438 no longer stops it), and adds the Ollama it started when no model server on
  this machine is set up yet. When `OLLAMA_BASE_URL` or the stored server points somewhere other
  than Ollama's own port here and does not answer, neither the button nor startup launches a local
  Ollama that starfire would not use; the button says which server is down instead.
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
- MCP browser sign-in hardening: a token address that is briefly unreachable fails one tool call
  and the next call renews the sign-in, instead of ending the session for good; a session whose
  connection ended (a refused renewal, a server that went away, a stdio server that exited) is
  dropped, so the list stops saying Connected and the next chat reconnects it, for plain URL
  servers too.
- Sign-in addresses learned from an online MCP server (metadata, registration and token
  addresses) must be public https addresses, also after the name is looked up; addresses on this
  computer or a private network are only used when the MCP server itself is on this computer.
  Each sign-in step has an overall time limit and answers are capped at 256 KiB.
- "Sign in again" registers starfire afresh when the service rejected the old registration
  (`invalid_client`), when the registration needs a client secret that is no longer stored, or
  when the previous sign-in was never finished.
- The sign-in code and state no longer reach the uvicorn access log; the callback is logged
  without its query.
- The sign-in result toast only appears in the tab that started the sign-in, and only enables a
  server that is really in the list.
- Non-admins see a generic problem text for MCP servers instead of the full error with the
  server address.
- A damaged saved sign-in asks for a new sign-in instead of raising an error at startup, a
  restore that changes a server's address does not keep the live tokens, and a saved sign-in is
  never sent to an address it was not issued for.
- When a server signs in through another site, starfire names that site and asks before sending
  the browser there, and a sign-in server that promises the `iss` answer parameter must send it.
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
