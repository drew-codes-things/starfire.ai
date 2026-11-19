# Arty, Arty CLI and starfire.ai

These three projects are made to work together, and each one links to the other two.

| Project | What it is | What it does for the other two |
|---|---|---|
| [Arty](https://github.com/drew-codes-things/Arty) | The assistant: the brain and its API, tools, memory and voice, plus the desktop, Android, iPhone and Quest apps | Answers for both. Arty CLI and starfire.ai send their chats to Arty's API and use its models and tools. Arty's approve page sends a browser that has no Arty key to starfire.ai |
| [Arty CLI](https://github.com/drew-codes-things/ArtyCode) (ArtyCode) | Arty in your terminal: a coding agent that reads, edits and runs code in a project, asking first | Talks only to Arty. Logs in with an Arty API key, or by approving the computer in the browser. With `/remote` it shares a session through Arty, so starfire.ai and the Arty phone apps can follow and steer it |
| [starfire.ai](https://github.com/drew-codes-things/starfire.ai) | A web chat dashboard for local and hosted models | Lets you chat with Arty in the browser. Its admin-only **Arty devices** page approves a computer that is logging in (Arty CLI's browser login), lists and turns off approved devices, chooses which devices may control Arty CLI, and makes a new key with a QR code for a phone. Its admin-only **Arty CLI** page follows and steers shared Arty CLI sessions |

How to link them:

- **Arty to starfire.ai**: set `BRAIN_STARFIRE_URL` in the brain's `brain.env` to starfire's address, so the approve
  page can send people there.
- **starfire.ai to Arty**: add Arty under **Settings > Connections** with its address and API key, and set
  `ARTY_BASE_URL` in starfire's `.env` to exactly the same address (the **Arty devices** page needs it).
  `ARTY_PHONE_URL`, `ARTY_ACCESS_CLIENT_ID` and `ARTY_ACCESS_CLIENT_SECRET` are optional, for phone setup codes that
  work away from home.
- **Arty CLI to Arty**: set `ARTY_URL` (home network) or `ARTY_REMOTE_URL` (from anywhere, with
  `ARTY_ACCESS_CLIENT_ID` and `ARTY_ACCESS_CLIENT_SECRET` if Cloudflare Access guards it), then run `arty login`.

- **Controlling Arty CLI from elsewhere**: run `/remote` (or start with `arty --remote`) on the computer, then open
  **Arty CLI** in starfire's sidebar (admins only). It lists the computers sharing a session; open one to read what
  Arty is doing, answer its permission questions with **Allow** or **Deny**, send a message, or **Stop** the current
  turn. starfire uses its own Arty connection for this, so the browser never holds Arty's key. For a phone with its
  own device key, switch on **Arty CLI** for it on the **Arty devices** page.
- **Editing Arty's memory**: **Settings > Arty memory** (admins only) lists what Arty remembers, by name with its
  kind and facts, with a search over all three. A fact can be edited in place or removed (after a confirm), a name
  can be removed with all its facts, and a new fact can be added under a new or existing name. starfire passes each
  change to Arty's `GET /v1/memory` and `POST /v1/memory/add`, `/edit` and `/delete` with its own Arty connection
  and shows Arty's answer as it comes (a fact already saved, a name that isn't there, text that is too long, the
  memory store being down). Every chat reads the same memory, so the next chat anywhere sees the change. Other
  accounts get a 403 from `/api/arty-memory` and no tab.

Arty works without the other two. Arty CLI needs Arty. starfire.ai works with other models on its own, but its
**Arty devices**, **Arty CLI** and **Arty memory** pages need Arty.

## Looking at your screen

With the Arty Desktop link on, an admin can ask the chat to "look at my discord": Arty asks you to Allow a screenshot and the model sees it. See [Set up starfire Desktop](../desktop/README.md#set-up-starfire-desktop).
