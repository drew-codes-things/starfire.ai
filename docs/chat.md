# Chat

## Arty Auto

**Arty Auto** (Auto for short) is what a new account starts with. In the model picker it is the
first entry, above one flat list of every model the account may use, with no headings. A model
that Arty's server (any server that lists an `OWN_TOOLS_MODELS` match) offers again, as
`arty/<name>` or `<name>`, next to `<name>` on another server is shown once, as the direct one.
The same name on two other servers is listed for each of them, marked `server 1`, `server 2` and
so on in the order the servers answered, so the account can choose where its text goes. Picking a model there sets the chat to that model; picking Arty Auto
(or **Arty Auto** at the top of the picker) goes back. An account that may use only remote models
sees no Arty tab and no Arty Auto, since Auto never picks a remote model; the server refuses Auto
for it with its own message. For every
message the server picks a model from simple fixed rules, without asking another model first:

| The message | Goes to |
|---|---|
| has a picture or a video attached | the default model when its engine says it can see pictures, otherwise the vision model (names with `vl`, `vision` or `llava`, never a hidden one) |
| has audio attached, or starts with "Arty" / "hey Arty" | the agent (the first `OWN_TOOLS_MODELS` name without a wildcard, such as `Arty`) |
| has a code block, a stack trace or error name, a source file name, or asks about code | the coding model (the default model until an admin names one) |
| asks for a reminder, timer, the phone, calendar, inbox, lights, music, the web, weather or news | the agent |
| anything else | the default model (`TASK_MODEL`); only when neither is set, the first ordinary model |

An admin can name the model for each role under **Settings → Connections → Arty Auto**: the
**Default model** (everything else, and the main model for the fixed context size
and helper jobs, in place of `TASK_MODEL`), the **Coding model**, pictures and the agent. A role
with no model falls back to the default model. Left on automatic, Auto only picks models on a
connection at a home address: never a remote connection, a connection under Arty's rules, a
`remote/` model or a cloud model. A model the admin names for a role is used wherever it runs. Auto only ever picks from the models the account may
use, so an ordinary account is never sent to a model matched by `RESTRICTED_MODELS` or left out
of its own list, even when an admin named that model for a role; the one exception is a
restricted model an admin put by its full name into that account's own list. A pattern such as
`*` or `own/*`, or a name without its tag, never opens a restricted model. A guess never lands on a
restricted model for anyone. With a default model set, Auto never moves to another model by itself: when the
default model isn't listed the reply says so, and an account that may not use the default model picks one by hand.
Each reply names the model Auto picked and why
(`qwen3:14b · Arty Auto: code`) in its header, in the status line and under **info**.

Every chat keeps its own model (or Auto) on the server, so a refresh, another tab or another
device opens it with the same choice. A new chat starts with the model you picked last (the star
marks it), or Auto.

## Naming a model in a message

In the chat page, a sentence that starts with an instruction naming a model ("use qwen3:14b to summarise this",
"use the 14B for this", "with the coder one, write ...", "ask the remote model to ...") runs that reply on the named
model, as if it had been picked in the model list and with the same access rules: an account that may not use it is
told "That model isn't available to you." Only what was typed counts: quoted text, code blocks and attachments never
switch the model, and API callers (app keys, Arty's apps) never do. A name has to be the model's whole id, its label
(`MODEL_LABELS`) or a short form of the label: the size ("the 14B"), the size with the rest of the label, or the rest
of the label with "model" or "one". "From now on use X", "switch to X" or "go back to X" also moves the model picker
to X, so it stays until another model is picked. When two models match, one on the chat's own connection is taken,
otherwise the reply asks "Which one did you mean: A or B?". Anything else is answered normally, and hidden models
(`HIDDEN_MODELS`) can't be named. The reply is labelled and saved as the model that wrote it.

## Replies

A reply in a saved chat is written on the server: refreshing or closing the page while it streams
does not stop it. Open the chat again and the page picks the reply up where it is (or shows it
finished), and the server saves it into the chat when it ends. The stop button stops it on the
server too. Temporary and side chats still stop when the page goes away.

Every reply has a toolbar: **edit** (in place), **copy**, **listen**, **info** (model, time,
estimated size), **+1 / -1** rating, **continue** (extends the reply), **regenerate** (with any
model) and **delete**.

## Code checks

Code in a reply lands in its own box with the language, a **copy** button and a check result.
When a reply finishes, every fenced code block whose language starfire knows is parsed or
compiled on the server - Python (`compile`), JavaScript and TypeScript (`node --check`),
shell (`bash -n`), JSON, YAML, TOML and XML (parsers), Go (`gofmt -e`), C and C++
(`cc -fsyntax-only`), Ruby (`ruby -c`) and PHP (`php -l`) - and never executed. A block that
passes shows **✓ syntax ok**; a block that fails is sent back to the same model with the error,
up to twice, and the corrected version replaces it in the reply (**✓ fixed · syntax ok**, with
the original error in the tooltip). If it still fails, the original stays with **✗ syntax
error** and the line number, so you know before you paste. The copy button is disabled while a
block is being checked. Languages whose checker isn't installed on this machine are left
unmarked. Switch the whole thing off under **Settings → General**.

Every checker runs in its own process under `prlimit`: 512 MB of memory (1 GB for node), 10 s of
CPU, files up to 8 MB, no core files, and inside bubblewrap at most 32 processes and no network.
A check is stopped after 20 s. The server reads at most 64 KB of a checker's output, and the C
compilers stop after 20 errors. C and C++ only compile code whose `#include` lines name system
headers such as `<stdio.h>` (comments and string literals are read the way the compiler reads
them, and a comment right after `#` is refused), and the compiler's sandbox holds only the system
program folders (`/usr`, `/bin`, `/lib*`, `/etc/alternatives`), a private `/tmp` and the snippet,
so an include that gets past the filter finds no other file. Two checks run at once on the whole
server and one per account, and one account may use 20 seconds of checking in a minute, so two
accounts cannot keep the checker busy for everyone else; a check beyond that is reported as busy.

## Editing and branching

Your own messages have **edit**, **copy** and **delete**. Editing your
message or regenerating branches the conversation - rather than mutating history in place,
they create a new chat pointing back at the original up to that point (marked `↪` in the
sidebar). The send button becomes a stop button while a reply is streaming (`Esc` stops it too). Every conversation autosaves
as you go, and the history sent to the model is trimmed to the context length (8192 tokens
unless set in controls).

## Status line

A status line under the message box shows the open chat at a glance and updates while a reply
streams, for example `3 turns 10 steps · 27 tok/s | 108K tok · Cache hit 99% | ◔ 10%`:

- **turns**: your messages in this chat.
- **steps**: tool calls starfire ran for replies in this chat (counted from this version on; hidden
  for models that run their own tools, such as Arty, because starfire cannot see those).
- **tok/s**: speed of the latest reply. It is the model server's own timing when it reports one
  (Ollama and ArtyEngine `eval_duration`, llama.cpp `timings`); otherwise it is measured from the
  stream, leaving out time spent running tools. A `~` marks a figure estimated from characters
  (about four per token) while the reply is still arriving.
- **tok**: prompt plus reply tokens of the latest model call, as the server reported them.
- **Cache hit**: the share of that prompt the server took from its prompt cache. Shown only when
  the server reports cached prompt tokens (ArtyEngine `prompt_eval_cached_count`, OpenAI-style
  `usage.prompt_tokens_details.cached_tokens`, llama.cpp `timings.cache_n`, Anthropic
  `cache_read_input_tokens`); stock Ollama and Arty's brain do not, so it stays hidden there.
- **ring**: how much of the context window those tokens fill. Shown only when the window is known,
  which today means requests starfire sends straight to Ollama or ArtyEngine (`TASK_NUM_CTX` or the
  chat's own context length).

Anything the server did not report is left out rather than guessed. On a narrow screen the
items drop off in this order: cache, steps, turns, speed, tokens; the ring stays. The figures are
kept with each reply, so an old chat shows them again when opened. To get usage from
OpenAI-style servers starfire asks for it (`stream_options.include_usage`); a server that
refuses that option is asked again without it.

## Tool support warning

If a model likely doesn't support tool-calling (or is small enough - under ~3B parameters -
to be unreliable about *when* to use a tool rather than just answering directly), a banner
warns you before you enable tools for it, and the backend withholds the tools list from that
model's requests either way.

## Titles, tags and follow-ups

New chats get a title and up to three tags, and each reply can be followed by three suggested
follow-up questions you can click to send (all three can be switched off in Settings → General).
These small jobs go to the chat's own model unless `TASK_MODEL` is set, in which case they go
straight to that Ollama model instead. That keeps them out of an assistant's history and tool
loop (Arty's brain, for example): point it at the model the assistant already has loaded, with
`TASK_NUM_CTX` matching its context size, so Ollama doesn't load a second copy. Qwen3 models
are asked not to think for these (Ollama's `think: false`). Every request starfire sends
straight to Ollama (chats, these small jobs and the model warm-up) asks for `TASK_NUM_CTX` as
its context size, unless the chat sets its own context length, and turns context shift off
(`shift: false`), as Arty's brain does, so Ollama keeps the model it already has loaded.

## Saved chats

Chats are saved automatically after each exchange. Settings → Chats exports every chat as
JSON, imports a starfire export or an Open WebUI chat export, archives all chats, lists tags,
and has a delete-all (you type "delete all" to confirm). A conversation you haven't sent a
message in yet is also kept as a local draft (browser `localStorage`) so a crash or an
offline moment before the first backend save doesn't lose it.

## Prompts

Workspace → Prompts stores reusable prompts under a `/command`. Typing `/command` in the chat
box inserts it, filling in `{{CURRENT_DATE}}`, `{{CURRENT_TIME}}`, `{{CURRENT_WEEKDAY}}` and
`{{CLIPBOARD}}`.

## Compare

The **compare** button (header) opens a side-by-side view: one prompt, sent to as many
model/endpoint columns as you add, streamed in parallel. Useful for actually seeing how two
models answer the same thing rather than guessing.

## Presets

Workspace → Models (presets) saves the current (model + system prompt + enabled tools) as a named preset
you can reapply with one click, instead of reconfiguring the header and toggles every time.

## Usage

Settings → Usage shows estimated token counts and cost, today and all-time, broken down by
model. Token counts are estimated (~4 chars/token) and cost is looked up from a small static
price table for known hosted models - not a substitute for your provider's own billing page,
but enough to see where usage is trending. Ollama/local models always show $0.
