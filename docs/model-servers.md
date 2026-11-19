# Model servers and providers

## Model server detection

If Ollama (port 11434) or ArtyEngine (port 11438) is already running on this machine the first
time starfire starts, it's detected and added as an endpoint, so it appears in the model list
with no setup; when both answer, ArtyEngine is used. Port 11438 only counts as ArtyEngine when
the server there names itself as ArtyEngine (port 11434 has no such check: anything that
answers Ollama's API there on the first start is taken for Ollama). A server only counts as
speaking Ollama's API when its `/api/tags` answers with a model list; a server that answers 200 on
every path is not taken for Ollama. Detection runs only while no Ollama-API server is set up at
all: none added with **add ollama** or **scan network** (on this machine or another) and no Ollama
server on another machine added with an API key (Ollama Cloud does not count, as it is not a server
of your own, and an Ollama on this machine added with a key does not stop detection: at each start
it is filed as Ollama in place when its server answers Ollama's API, whatever its port and however
the address names this machine, so it is used and no second endpoint is added for it, unless
another server on this machine is already the chosen one or `OLLAMA_BASE_URL` names another server;
then it is left as it is). Detection
never adds a second endpoint for a server already stored under another name for this machine
(`localhost` or `127.0.0.1`, with or without `/api`). Once one is, starfire keeps using it and never adds or switches to
another server by itself (Settings → Hardware shows one it finds but has not added as "found, not
added"). Memory and document embeddings and model pulls go to `OLLAMA_BASE_URL` or else to the
first endpoint on this machine filed as Ollama in **Settings → Connections** (one added with
**add ollama** or **scan network**, detected at start, or added with a key and filed as Ollama as
above; its server root, even when it was stored
with an `/api` or `/v1` path), never to whatever happens to answer on a port.
When the only model server set up is on another machine and `OLLAMA_BASE_URL` is not set,
embeddings stay off and search uses keywords; set `OLLAMA_BASE_URL` to that server to turn them
on. A server on this machine without the Ollama API, found by **scan network** or typed into
**add ollama**, is stored as OpenAI-compatible at its `/v1` address when its `/v1/models` lists
models: it is used for chat, not for embeddings or pulls. **add ollama** refuses a server whose
`/api/tags` gives no model list and that is not stored that way. It asks `/api/tags` under the
address as typed (after taking off a `/v1` or `/api` ending, so an Ollama behind a proxy path such
as `https://box.example/ollama` or `https://box.example/ollama/v1` is asked at `/ollama/api/tags`;
an address ending in `/api` gets that far on this machine; elsewhere only when `/api` is the
whole path and the address is on port 11434 or is the `OLLAMA_BASE_URL` address (host and port),
because otherwise the first check asks `<address>/v1/models`), with the same 5 s limit and the
same API key as its first check, reuses the first answer when that check already asked
`/api/tags`, and says whether the server did not answer in time, refused the key, answered with an
error or answered without a model list. At each start a stored server on
this machine is filed by the API it speaks: as OpenAI-compatible only when its Ollama API answers
"not found" or answers without a model list and its `/v1/models` lists models, and back as Ollama
once its Ollama API lists models again, unless another server on this machine is the chosen one
by then; a server that is down or answers with an error keeps its
kind. Each endpoint remembers what it was added as: one added at its `/v1` address as anything
other than Ollama (by the scan, the key form, or **add ollama** finding no Ollama API) is never
filed as Ollama, even when it starts listing Ollama models, so it never becomes the embeddings
server, while one added with **add ollama** as Ollama is filed back as Ollama whatever its address.
An endpoint stored before this was remembered counts as added as the kind it had then. The scan names every server it could not add and why. Clicking **start
Ollama** on Settings → Hardware adds the Ollama it started when no model server on this machine
is set up yet. It only starts Ollama when Ollama's own port here is the server starfire would use:
when `OLLAMA_BASE_URL` or the stored server points somewhere else and does not answer, it says so
instead of starting an Ollama starfire would not use. To add a server later, open
**Settings → Connections** and either click **add ollama** (fills in the default endpoint, then
Test/Add) or **scan network** to probe common local model-server ports (11438, 11434, 1234 and
8080).

## Ollama-compatible servers and ArtyEngine

Any server that speaks Ollama's API can stand in for Ollama. Arty runs on
[ArtyEngine](https://github.com/drew-codes-things/ArtyEngine), its own Ollama-compatible server
built on llama.cpp, and starfire talks to it the same way. starfire tells the two apart from
what the server reports (ArtyEngine's `Server` header, or its "Arty engine is running"
greeting when a proxy drops that header), names the one it found in the endpoint label and on
**Settings → Hardware**, and treats anything else that answers Ollama's API as Ollama. The name
is only a label, not a proof of identity: any local program can send the same header. An
auto-detected endpoint is renamed on startup when the server behind its address now reports the
other name; an endpoint you named yourself keeps your name. On
another machine, set `OLLAMA_BASE_URL` in `.env` to its address and reach it through a reverse
proxy as ArtyEngine's README describes, since it listens on loopback by default. The **Start
Ollama** button only appears when an Ollama binary is installed here; with ArtyEngine or a
remote server, the Hardware tab says the backend runs on its own. ArtyEngine is a separate
project under its own licence, PolyForm Strict 1.0.0, not starfire's Apache 2.0.

## Adding a hosted provider

Settings → Connections → "Add API provider": pick OpenAI, Anthropic, or "Other
(OpenAI-compatible)", paste an API key, Test, then Add. The key is encrypted at rest - see
[Data and security](data-and-security.md).

## Hardware

Settings → Hardware detects this machine's RAM/VRAM (capacity - does a model fit?) and
CPU/GPU/RAM identity and speed (roughly, will it run well?), then suggests which common
Ollama-pullable models should actually work - instead of guessing and hitting an
out-of-memory error or a model that swaps to disk and crawls. Detection works on Linux, macOS,
and Windows; GPU VRAM/name needs `nvidia-smi` (NVIDIA only), and RAM speed/type needs
`dmidecode` **and root** on Linux specifically (macOS/Windows can read it as a normal user).

**Model server status** is also shown here, naming the backend that answered (Ollama or
ArtyEngine). If Ollama is installed but nothing is running, a **Start Ollama**
button launches it (no elevated privileges needed - same as running `ollama serve` yourself);
starfire also tries this automatically on its own startup. If Ollama isn't installed at all,
the tab shows the right install step for your OS (the official installer command on Linux,
a download link for macOS/Windows) - starfire won't run that installer for you, since Ollama's
own Linux installer needs `sudo`.

### Pulling models

Click **pull** next to any suggested model in the Hardware tab (or in Settings → Connections) to
download it through Ollama with a progress bar, same UX as Ollama's own CLI. To pull a model
manually instead - useful for scripting, a headless box, or a model not in the suggested
list - use Ollama's CLI directly:

```bash
ollama pull llama3.1:8b        # any model from https://ollama.com/library
ollama pull qwen2.5-coder:7b
```

A model pulled this way shows up in starfire's model list immediately; no restart needed.
`ollama list` shows what's already pulled, and `ollama rm <model>` removes one.
