# Tools

## MCP servers

Settings → Tools lets the model call external tools via the
[Model Context Protocol](https://modelcontextprotocol.io). **Fetch** and **Memory** (two of the
three official reference servers) are installed and connected automatically on first run - just
enable their checkbox to use them in a chat. **Filesystem** (read/write within a directory you
choose) stays a manual "Quick add" since it needs that directory choice first.

An MCP server that is already running at a URL - the newer **streamable HTTP** transport or
the older **SSE** one - is added from the same **Custom MCP server** form: pick the transport,
paste the URL (for example `http://localhost:3001/mcp`) and, if the server wants one, an
`Authorization` header value. Local servers stay stdio: a command plus arguments.

Filesystem and MCP Memory need [Node.js](https://nodejs.org) (`npx`) installed locally; Fetch
needs [uv](https://docs.astral.sh/uv/) (`uvx`). Beyond those three, a **server repository**
dropdown lists other common MCP servers (Git, GitHub, GitLab, SQLite, Brave Search, Slack,
Puppeteer) with a per-server configure form for whatever it needs (an API key/token, a local
path, etc.) - or add any other stdio MCP server yourself by command + args. Enable a server's
checkbox to include its tools in the next chat message - supported for Ollama (models with
tool-calling support), OpenAI, and Anthropic.

MCP servers run on the server with the settings and tokens the admin gave them, so only admin
accounts can use them, the same rule as the shell, file edit and GitHub tools. Other accounts'
chats run without them.

## Signing in to online services

**Sign in to an online service** at the top of the Tools tab lists hosted MCP servers that let
starfire sign in through the browser, with nothing to paste: Notion, Linear, Atlassian, Sentry,
Cloudflare, Stripe, PayPal, Canva and Webflow. Click one and starfire sends you to that
service's own sign-in page; approve there and you come back to starfire with the server
connected and its tools listed. The URL box under the list does the same for any other MCP
server address that supports browser sign-in.

How it works (the [MCP authorization spec](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)):
starfire asks the server, reads its protected resource metadata and its sign-in server's
metadata, registers itself as an app there (dynamic client registration), and sends you to the
authorization page with PKCE (S256) and a one-time `state` tied to your starfire session. The
sign-in must come back to the same session within 10 minutes and can be used once. The access
and refresh tokens are encrypted like the other MCP secrets, never logged, and left out of
backups. starfire renews the access token shortly before it runs out and again whenever the
server answers 401; when the service refuses a renewal, the server shows **Needs sign-in
again** with a button that runs the sign-in once more. Only admins can sign in to services,
the same rule as adding any MCP server.

A sign-in address the server names (its metadata, registration and token addresses) must be a
public https address, checked again after its name is looked up; addresses on this computer or
a private network are only used when the MCP server itself is on this computer. When the
server signs in through a different site than its own address, starfire names that site and
asks before sending you there. If the service is briefly unreachable when a token needs
renewing, that one tool call fails and the next one tries again.

The address the service sends you back to is
`<the address you opened starfire at>/api/mcp/servers/sign-in/callback`, taken from your
browser's own origin (it must match the host the request came to). If everyone opens starfire
at one public address, set `PUBLIC_BASE_URL` to it and that address is used instead.

Not in the list, with the reason found when they were checked on 13 Nov 2026: GitHub's hosted
server and Asana's v2 server need an OAuth app registered by hand (no dynamic registration, so
GitHub keeps the token form in the repository list); Asana's older server and Vercel only
register apps whose return address is on the same computer (`localhost`), which a starfire
on another machine is not; Hugging Face's server answers without a sign-in, so add it as a
plain URL.

## Built-in tools

The same Tools tab also has several built-in (non-MCP) tools - see [Memory, knowledge and notes](memory-knowledge-notes.md),
[Automations](automations.md) and [Email](email.md) for most of them, plus:

- **Search the web** - a single no-API-key backend (DuckDuckGo's HTML results page); see
  `server_configs/web_search.py`. Swapping in a proper provider later is a one-function change.
- **Find images on the web** - DuckDuckGo image search (no API key). The model asks for a
  picture of something, the top usable result is downloaded on the server (images only, 15 MB
  cap, no private addresses) and stored with the other generated files, then shown in the chat
  with a link to the page it came from. Nothing is hot-linked from the model's own guesses.
- **⚠️ Run shell commands** - off by default. One general-purpose shell tool the model can call
  directly, the same way coding agents use one shell tool rather than a curated command
  set. It starts in the account's workspace
  folder with a scrubbed environment (starfire's `.env` values never reach it), and has a timeout (120s) and an
  output-size cap. When bubblewrap (`bwrap`) is installed and can start, it runs in a sandbox: read-only system,
  private `/tmp` and process list, the data folder, the app folder and starfire's home hidden, only the workspace
  writable. **Without bubblewrap there is no sandbox under it**: it runs as the user starfire runs as, and the
  approval card says so. By default every command the
  model proposes **waits in the chat for you to press "run it" or "reject"** (five minutes,
  then it is dropped); the model only sees the output once you approve. You can switch that
  off under the shell toggle if you really want it unattended. Only enable this in a chat (or
  an automation, see [Automations](automations.md)) that won't also be reading untrusted content (a webpage, an email)
  in the same conversation - a tool the agent can call is reachable by prompt injection, and
  once a turn has read untrusted content, shell commands wait for an approval card even when approval is off. `ssh host 'command'` through this same tool
  covers SSH; there's no separate SSH-specific tool.

## Deep research and GitHub

Two more builtin tools in Settings → Tools:

- **Deep research** - searches the web, reads the top results, and returns a synthesized,
  cited report in one tool call. Slower and more thorough than the plain "Search the web" tool;
  use it for open-ended questions, not quick lookups.
- **GitHub (gh CLI)** - runs the `gh` binary only (not arbitrary shell), so the model can list
  issues, open PRs, check CI, etc. Needs `gh` installed and already authenticated
  (`gh auth login`) - this tool doesn't manage credentials itself. It only runs issue, pr, repo,
  release, run, workflow, label, gist, search, status, api, org, project, ruleset, cache, browse and
  `auth status`; reads with known flags run straight away, everything else waits for an approval
  card. Flags that can print the token (`--show-token`, `--jq`, `-q`, `--template`, `-t`) are
  always refused, and `alias`, `extension`, `config`, `secret`, `codespace` and `auth token` never
  run.

## Tool levels for local models

Not every model can juggle fifteen tool schemas. starfire picks a level from the Ollama model's
parameter count (the `7b`, `14b` in its name) and tells you in a banner above the chat:

| Level | Models | What the model is given |
|---|---|---|
| none | under 3B, or known non-tool models | no tools at all, whatever you ticked |
| basic | 3B up to 12B | web search, knowledge, memory, notes, find/generate images, scheduled tasks, plus MCP servers, capped at 8 tools in total |
| full | 12B and up, and every hosted provider | everything you enabled |

The thresholds are two constants at the top of `server_configs/model_capabilities.py`.
