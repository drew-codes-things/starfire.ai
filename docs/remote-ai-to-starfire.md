# App keys and the OpenAI-compatible API

starfire can stand between your apps and your model servers. An admin makes an **app key** for an
app that speaks the OpenAI chat API, and the app then uses starfire's models through `/v1` with that
key. Every request acts as one starfire account and follows that account's rules, so outside model
servers added under **Settings → Connections** are reached only through starfire, with starfire's
limits.

## Making a key

**Settings → Accounts → App keys** (admins only). Give the key:

- a **name** (shown in the list and in starfire's log, so name it after the app or device),
- the **account** it acts as,
- what it may use: **every model of a connection**, or single **models on a connection**, picked
  from what that account may use. A key is bound to connections: it never reaches a model of the
  same name on a connection it was not given. A single model is matched by its exact name (case and
  spaces count), and `*` means every model of the connection only as that choice, never as the name of
  one model,
- a **daily reply limit** for the key (filled in as 500; empty for none; counted per UTC day and kept
  across restarts),
- an **expiry** in days (filled in as 90; empty for never).

The key (`sf_` followed by 43 random characters, 32 random bytes) is shown **once**, right after
it is made. starfire keeps only its SHA-256 hash in `data/app_keys.json` (file mode 600), the same
way it keeps sign-in sessions, so a lost key cannot be shown again: revoke it and make a new one.
The list shows each key's name, the first characters of the key, its account, what it may use, its
limit, expiry, when it was made and when it was last used. **Revoke** deletes the key at once; the
next request with it is refused.

### Arty and other models that bring their own tools

Models matching `OWN_TOOLS_MODELS` (Arty by default) act for their owner with their own tools:
memory, mail, files, and they can approve their own actions from the conversation they are sent.
starfire reaches Arty with Arty's own key, so **a key that may use Arty is worth as much as Arty's
own key**. The rule goes by connection, not by model name: a connection is an **Arty connection**
when any model it listed at its last good check matches `OWN_TOOLS_MODELS`, or when its address is
`ARTY_BASE_URL` (with or without `/v1`). Every model of an Arty connection, also names outside
`OWN_TOOLS_MODELS` such as `solo/...`, is left out of every key, "every model of a connection"
included, and out of `GET /v1/models`, and cannot be granted, unless the key was made with **Let this
key use Arty** ticked; without it the form hides the whole connection. That option is offered only for admin accounts, is off by default, and stops working if the
account stops being an admin. For every model of an Arty connection starfire passes on only the standard OpenAI fields
(`model`, `messages`, `stream`, `temperature`, `top_p`, `max_tokens`, `tools`, `tool_choice`, `stop`;
in each message `role`, `content`, `name`, `tool_calls`, `tool_call_id`) and drops everything else,
such as Arty's own `arty_*`, `client` and `features` switches. Apps of your own that need Arty are
better off with a device key from Arty itself.

### When keys stop working

A key is deleted when its account is disabled or deleted, and when an admin resets that account's
password (changing your own password under Settings → Account does not touch keys). Backups leave
`app_keys.json` out, and a restore never writes it, so restoring an older backup cannot bring back a
revoked key.

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
{"object": "list", "data": [
  {"id": "3f2a9c1b7d4e/qwen3:14b", "object": "model", "created": 0, "owned_by": "starfire", "available": true}
]}
```

Every `id` is `<connection id>/<model name>`: the connection's id from Settings → Connections, a
slash, and the model's own name. Ids never change when another connection is added, and two
connections offering the same model name give two different ids. A model is listed when its
connection offered it at the last check, the key may use it and the key's account may use it.
`available` is true while its connection answers starfire's check and false while it does not; a
connection that stops answering keeps its last known models in the list.

### `POST /v1/chat/completions`

Takes an OpenAI chat request (`model`, `messages`, and optionally `stream`, `tools`,
`tool_choice`, `temperature`, `top_p`, `max_tokens` and so on) and answers in OpenAI form, streamed
as server-sent events when `stream` is true. `model` must be an `id` from `GET /v1/models`; the
request goes only to that connection, with the model's own name.

- On an OpenAI-compatible connection (OpenAI, llama.cpp's llama-server, LM Studio, vLLM and other
  "Other (OpenAI-compatible)" servers) the request is passed on as it came and the answer is passed
  back as it came, so `tools`, `tool_calls` and `tool` messages work exactly as the model server
  handles them. The app runs its own tools.
- On an Ollama or Anthropic connection starfire translates the request with its usual provider
  code and answers in OpenAI form, tool calls included.
- `n` other than 1 is refused: one request is one reply.

The API gives a model, nothing else: no starfire host tools, MCP servers, memory, knowledge,
saved chats or system prompt are added, and nothing is saved to the account's chats.

### Errors

Errors use OpenAI's shape: `{"error": {"message": "...", "type": "...", "code": "..."}}`. Messages
are starfire's own: what a model server says in an error is logged as its status only and never
shown.

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request_error` | The body is not a JSON object or is nested more than 64 levels deep, `model` or `messages` is missing or wrong, a message has no role or a `tool_calls` that is not a list of `{function: {name, arguments}}`, `tools` is not a list of objects, `stream` is not true or false, or `n` is not 1 |
| 400, 404, 409, 413, 422, 429 | `upstream_refused` | The model server refused the request with that status |
| 401 | `invalid_api_key` | No key, a wrong key, a revoked or expired key, or the key's account is disabled |
| 403 | `model_not_allowed` | The key, the account's model rules or the Arty rule above do not allow that model |
| 404 | `model_not_found` | The id is not `<connection>/<model>`, there is no such connection, or the connection does not offer that model |
| 404 | `not_found` | Any other path under `/v1/` |
| 413 | | The request body is over 16 MB |
| 429 | `rate_limit_exceeded` | The key's requests a minute, the key's daily limit, the account's reply limits, or too many wrong keys from one address; `Retry-After` says how many seconds to wait |
| 500 | `server_error` | starfire itself failed |
| 502 | `upstream_error` | The model server did not answer, refused starfire's key for it (its own 401 or 403 never shows as a bad app key), answered with a redirect (3xx, never followed or passed on) or failed |
| 503 | `model_unavailable` | The model's connection is not answering starfire's check; answered at once with `Retry-After: 60`; no other connection is tried |

A request starfire refuses (400, 403, 404, 429, 503) does not use a reply, and neither does a 5xx,
timeout, redirect or refused key from the model server. A streamed reply stops with an error event within about a second
when its key is revoked or its account is disabled.

## starfire's rules on the API

- **Account rules.** The key's account limits count every reply: `GENERATIONS_PER_MINUTE` and
  `GENERATIONS_PER_DAY` for ordinary accounts (admins are not limited, as in chat), the account's
  model list from **Settings → Accounts → models**, and restricted models (`RESTRICTED_MODELS`),
  which only admin accounts may use. A key for an ordinary account cannot be given a restricted
  model.
- **The key's own rules** come on top: its connections and models, its daily limit, its expiry,
  and `APP_KEY_REQUESTS_PER_MINUTE` (30 by default) requests a minute for every key, admin accounts
  included, `GET /v1/models` counted.
- **Wrong keys.** Ten wrong keys (an `Authorization: Bearer` with a key that is wrong, revoked or
  expired) from one address within 15 minutes make that address's wrong keys answer 429 for 15
  minutes. A right key is looked up first and always works; a request without an `Authorization`
  header gets 401 and is not counted. The address is the visitor's real address behind the proxies
  in `TRUSTED_PROXIES`, the same as for sign-in.
- **Size.** Request bodies over 16 MB are refused before they are read.
- **Log.** Each request logs the key's name, the model and the status. The key itself, the prompt,
  the reply and model server error text are never logged.

## Connection checks

Every 60 seconds starfire asks each connection under **Settings → Connections** for its model list
(`/v1/models`, or `/api/tags` for Ollama). Each check has 5 seconds for the whole answer, not per
read, and the connections are checked side by side, so a slow or dripping server only ever costs its
own check. The answer is read up to 1 MB and must be a JSON object whose `data` (`models` for Ollama)
is a list of at most 1000 objects, each with a string id (`name` for Ollama) of 1 to 200 characters;
anything else, and any failure inside one check, marks that connection `bad_answer` and unavailable
while keeping its last good model list. No prompt is sent and nothing is
generated. For each connection it keeps whether it answered, when it was last checked, when that
last changed, what went wrong (`timeout`, `unreachable`, `unauthorized` for a 401 or 403,
`server_error` for a 5xx, `http_error` for another error status, `bad_answer` for an answer that is
not a model list; never the key or the answer itself) and the last model list it gave. Settings →
Connections shows each connection as **Available** or **Unavailable** with when it was checked.
The API answers from these checks, so a chat request never waits on a connection that is down, and
a connection added since the last check is checked when a request for it next needs it: a request
only ever waits, at most 5 seconds, on the connections its key was given, and one check at a time
runs per connection however many requests wait on it. The checks start
and stop with starfire and are kept in memory only; a change between available and unavailable is
logged.

## An outside model server behind starfire

A model server on another machine, for example llama.cpp's `llama-server` started with
`--api-key` and reached through a Cloudflare tunnel at `https://friend.example.com/v1`, is added
under **Settings → Connections → Add API provider → Other (OpenAI-compatible)** with that address
and its API key. starfire lists its models from `<address>/models`, keeps the key encrypted like
every provider key, and sends chats to `<address>/chat/completions` with the key as a bearer
token. Such a server is only ever used for chat: being added at a `/v1` address with a key, it is
never filed as Ollama, never used for memory search embeddings and never asked to pull models (see
[Model servers and providers](model-servers.md)). App keys then decide who may use its models, and
since a key names connections, a prompt meant for another connection never goes there.
