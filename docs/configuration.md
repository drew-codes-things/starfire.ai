# Configuration

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8080` | Port the server listens on |
| `BIND_HOST` | `127.0.0.1` | Address to listen on; keep it on loopback unless an account exists, since the shell tool can run commands |
| `COOKIE_SECURE` | `true` | Set to `false` to allow the login cookie over plain HTTP on a trusted network |
| `OLLAMA_BASE_URL` | _(auto-detect)_ | Override auto-detection with a specific Ollama URL (or an Ollama-compatible server such as ArtyEngine) |
| `DATA_DIR` | `./data` | Where endpoint config and encrypted API keys are stored |
| `TASK_MODEL` | _(chat's model)_ | Ollama model for titles, tags and follow-up suggestions |
| `TASK_NUM_CTX` | `24576` | Context size for every request sent straight to Ollama (chats, `TASK_MODEL` jobs, warm-up); match the loaded model's to avoid a reload, `0` leaves it to Ollama |
| `ARTY_BASE_URL` | _(unset)_ | Arty's API address (for example `http://<arty-host>:8081/v1`). Must match an Arty connection under Settings > Connections that has Arty's API key; turns on the Arty devices page |
| `ARTY_PHONE_URL` | _(Arty's address)_ | The address a phone should use in the setup QR code made on the Arty devices page (for example Arty's public address) |
| `ARTY_ACCESS_CLIENT_ID` / `ARTY_ACCESS_CLIENT_SECRET` | _(unset)_ | Cloudflare Access service token put in the phone setup QR code, only when both are set |
| `OWN_TOOLS_MODELS` | `Arty,assistant-core,arty/*` | Comma-separated model ids (with `*` wildcards) that bring their own tools: the tools menu hides for them and starfire sends them none (`assistant-core` is Arty's old name, kept for saved chats). Add the pattern of any other brain model that runs with Arty's tools |
| `INLINE_MEDIA_PREFIXES` | `/arty-files/` | Comma-separated path prefixes whose image and video links render inline in replies; links to another host with one of these paths load through this server instead |
| `MODEL_VOICES` | (empty) | Comma-separated `pattern=voice.onnx@speed` rules giving models a server voice (Piper, `*` wildcards, speed 0.5 to 2, default 1). With read-aloud or a voice call on, a matching model's reply is spoken sentence by sentence while it streams, instead of by the browser at the end |
| `MODEL_VOICES_DIR` | `<DATA_DIR>/voices` | Folder holding the `.onnx` voices (and their `.onnx.json`) named in `MODEL_VOICES` |
| `TRUSTED_PROXIES` | `127.0.0.1,::1` | Comma-separated addresses or networks of proxies in front of starfire. The login lockout follows `X-Forwarded-For` back through these hops to the visitor; when every hop is trusted (a Cloudflare tunnel's `cloudflared` included), the visitor's address is read from `CLIENT_IP_HEADER`. List only machines that set those headers themselves |
| `CLIENT_IP_HEADER` | `CF-Connecting-IP` | Header holding the visitor's address, read only when the request came through trusted proxies alone |
| `MAX_DOCUMENT_MB` | `20` | Largest knowledge document one upload may be |
| `MAX_ATTACHMENTS_MB` | `1024` | Total size of one account's chat attachments (each kept 30 days) |
| `MAX_DOCUMENTS_MB` | `512` | Total size of one account's knowledge documents once their text is stored |
| `MAX_RESTORE_MB` | `1024` | Largest backup zip Settings → Data will restore |
| `MIN_FREE_DISK_MB` | `512` | Uploads, restores and backups are refused when they would leave less free disk than this next to `DATA_DIR` |
| `PUBLIC_BASE_URL` | _(unset)_ | The one address everyone opens starfire at (for example `https://starfire.example.com`). When set, MCP browser sign-ins return there; when unset they return to the address the browser used. Leave it unset if people use more than one address, since a sign-in must finish on the host it started on |
| `EDIT_ALLOWED_ROOTS` | _(none)_ | Comma-separated folders an admin's `edit_file` may write besides the account's own workspace (`<DATA_DIR>/users/<id>/workspace`). The data and app folders are never editable |
| `RESTRICTED_MODELS` | `*-abl,*-abl-*,*abliterated*,*uncensored*` | Comma-separated model ids (`*` wildcards, any case) that ordinary accounts can neither see nor use. A model matches when its name does with or without its `:tag` or `@digest` and with any leading `namespace/` parts removed. Admins use every model; under Settings > Accounts > models an admin can instead give an account its own list. In that list, patterns and names without a tag still leave out every restricted model; only a restricted model's full name (with or without `:latest`) lets that account use it |
| `GENERATIONS_PER_MINUTE` | `20` | Model replies an ordinary account may ask for in a minute (chats, helper calls, email AI actions); `0` turns the limit off. Admins are not limited |
| `MAIL_REQUESTS_PER_MINUTE` | `60` | Mail requests (email pages and the email tool) one account may make in a minute, admins included; `0` turns the limit off. Two mail calls per account run at once, each cut off after 60 seconds |
| `APP_KEY_REQUESTS_PER_MINUTE` | `30` | Requests a minute each app key may make to `/v1`, admin accounts included; `0` turns the limit off. See [App keys and the API](app-keys.md) |
| `GENERATIONS_PER_DAY` | `400` | Model replies an ordinary account may use in a UTC day, scheduled automations and email rules included; `0` turns the limit off |
| `SESSION_CHECK_HOSTS` | _(none)_ | Extra `host:port` names, comma-separated, on which `GET /api/auth/me` answers for a session made on any host. `127.0.0.1:<PORT>`, `localhost:<PORT>` and `[::1]:<PORT>` are always included, so a service on the same machine can ask who a browser's cookie belongs to. Every other request only accepts a session on the host it was signed in on |

Hosted-provider API keys are **not** set via environment variables - add them through the
Settings UI so they're encrypted at rest rather than sitting in plaintext in `.env`.
