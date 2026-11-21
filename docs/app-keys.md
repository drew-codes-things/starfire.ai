# App keys and the OpenAI-compatible API

starfire can stand between your apps and your model servers. An admin makes an **app key** for an
app such as Arty, ArtyCode or any program that speaks the OpenAI chat API, and the app then uses
starfire's models through `/v1` with that key. Every request acts as one starfire account and
follows that account's rules, so outside model servers added under **Settings → Connections** are
reached only through starfire, with starfire's limits.

## Making a key

**Settings → Accounts → App keys** (admins only). Give the key:

- a **name** (shown in the list and in starfire's log, so name it after the app or device),
- the **account** it acts as,
- the **models** it may use: every model that account may use, or ticked models from that
  account's list,
- an optional **daily reply limit** for the key (counted per UTC day, kept across restarts),
- an optional **expiry** in days.

The key (`sf_` followed by 43 random characters, 32 random bytes) is shown **once**, right after
it is made. starfire keeps only its SHA-256 hash in `data/app_keys.json` (file mode 600), the same
way it keeps sign-in sessions, so a lost key cannot be shown again: revoke it and make a new one.
The list shows each key's name, the first characters of the key, its account, models, limit,
expiry, when it was made and when it was last used. **Revoke** deletes the key at once; the next
request with it is refused.

## Using a key

Point the app at starfire's address with `/v1` as the base URL, for example
`https://starfire.example.com/v1`, and send the key as a bearer token:

```
Authorization: Bearer sf_...
```

Only this header opens the API. A signed-in browser's session cookie, an account password or any
other header is never accepted there.

### `GET /v1/models`

Lists the models the key may use, in OpenAI form:

```json
{"object": "list", "data": [{"id": "qwen3:14b", "object": "model", "created": 0, "owned_by": "starfire"}]}
```

A model is listed when a connection offers it, the key's own model list allows it and the key's
account may use it.

### `POST /v1/chat/completions`

Takes an OpenAI chat request (`model`, `messages`, and optionally `stream`, `tools`,
`tool_choice`, `temperature`, `top_p`, `max_tokens` and so on) and answers in OpenAI form, streamed
as server-sent events when `stream` is true. `model` must be an `id` from `GET /v1/models`.

- On an OpenAI-compatible connection (OpenAI, Arty, llama.cpp's llama-server, LM Studio, vLLM and
  other "Other (OpenAI-compatible)" servers) the request is passed on as it came and the answer
  is passed back as it came, so `tools`, `tool_calls` and `tool` messages work exactly as the
  model server handles them. The app runs its own tools.
- On an Ollama or Anthropic connection starfire translates the request with its usual provider
  code and answers in OpenAI form, tool calls included.
- `n` other than 1 is refused: one request is one reply.

The API gives a model, nothing else: no starfire host tools, MCP servers, memory, knowledge,
saved chats or system prompt are added, and nothing is saved to the account's chats.

### Errors

Errors use OpenAI's shape: `{"error": {"message": "...", "type": "...", "code": "..."}}`.

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request_error` | The body is not a JSON object, `model` or `messages` is missing or wrong, `stream` is not true or false, or `n` is not 1 |
| 401 | `invalid_api_key` | No key, a wrong key, a revoked or expired key, or the key's account is disabled or deleted |
| 403 | `model_not_allowed` | The key's model list or the account's model rules do not allow that model |
| 404 | `model_not_found` | No connection offers that model |
| 404 | `not_found` | Any other path under `/v1/` |
| 413 | | The request body is over 16 MB |
| 429 | `rate_limit_exceeded` | The key's daily limit, the account's reply limits, or too many wrong keys from one address; `Retry-After` says how many seconds to wait |
| 502 | `upstream_error` | The model server did not answer, refused starfire's key for it (its own 401 or 403 never shows as a bad app key) or failed |

Other errors from the model server (400, 404, 409, 413, 422, 429) are passed on with its message.
A streamed reply stops with an error event within about a second when its key is revoked or its
account is disabled.

## starfire's rules on the API

- **Account rules.** The key's account limits count every reply: `GENERATIONS_PER_MINUTE` and
  `GENERATIONS_PER_DAY` for ordinary accounts (admins are not limited, as in chat), the account's
  model list from **Settings → Accounts → models**, and restricted models (`RESTRICTED_MODELS`),
  which only admin accounts may use. A key for an ordinary account cannot be given a restricted
  model.
- **The key's own rules** come on top: its model list, its daily limit and its expiry.
- **Wrong keys.** Ten wrong keys from one address within 15 minutes lock that address out of the
  API for 15 minutes (429), even for a right key. The address is the visitor's real address behind
  the proxies in `TRUSTED_PROXIES`, the same as for sign-in.
- **Size.** Request bodies over 16 MB are refused before they are read.
- **Log.** Each request logs the key's name, the model and the status. The key itself, the prompt
  and the reply are never logged.

## An outside model server behind starfire

A model server on another machine, for example llama.cpp's `llama-server` started with
`--api-key` and reached through a Cloudflare tunnel at `https://friend.example.com/v1`, is added
under **Settings → Connections → Add API provider → Other (OpenAI-compatible)** with that address
and its API key. starfire lists its models from `<address>/models`, keeps the key encrypted like
every provider key, and sends chats to `<address>/chat/completions` with the key as a bearer
token. Such a server is only ever used for chat: being added at a `/v1` address with a key, it is
never filed as Ollama, never used for memory search embeddings and never asked to pull models (see
[Model servers and providers](model-servers.md)). App keys then decide who may use its models.
