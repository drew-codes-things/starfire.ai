# Logging in and accounts

## Logging in

The first time you open starfire it shows a setup page: enter the one-time setup code, then
choose your name, email and a password (at least 10 characters). While no account exists,
starfire prints the code in its log when it starts, in a line like
`no account yet: open /setup.html and enter the setup code ...` (the terminal for
`python server.py`, `docker compose logs` for Docker, `journalctl -u <service>` for a service).
Only someone who can read that log can make the admin account, so a stranger who reaches a fresh
copy first cannot. The code is random, lives in memory only (a restart makes a new one, and it
is gone once the account exists), and 5 wrong codes from one address lock that address out of
setup for 15 minutes. That makes the admin account, and the setup page closes
for good (a `data/.setup-done` marker keeps it closed: if `data/users.json` ever goes missing,
starfire answers 503 until it is restored, instead of offering setup to whoever comes first).
Until then, every other page and API call is locked, so set it up straight away
on a new install. After that, every page and API call needs a login (kept for 30 days in an
HttpOnly cookie; 5 wrong passwords lock that email from that address for 15 minutes, and
20 wrong passwords for one email within an hour, from anywhere, lock it for an hour except
from an address that has signed in to it before or a browser already signed in to it). The
login and setup pages can load only the stylesheet, the logo and the fonts without a login.
A login only works on the host name it was made on, so a cookie handed to another site behind
the same proxy cannot open this one.
Change your password under **Settings → Account**; that logs out every other browser. Forgot
it? Reset it from the terminal on the machine starfire runs on (this also logs out every
browser that was signed in):

```bash
python reset_password.py you@example.com
```

To copy logins from Open WebUI instead (same email and password - only the bcrypt hash is
copied):

```bash
python import_openwebui_users.py /path/to/open-webui/data/webui.db
```

Accounts are kept in
`data/users.json`. The cookie is `Secure`, so serve it over HTTPS (or `localhost`), or set
`COOKIE_SECURE=false` for plain HTTP on a trusted network. The shell tool runs as whichever
system user starfire runs as, so give it an unprivileged one.

## Accounts

The first account is the admin. Under **Settings → Accounts** an admin adds more people (name,
email, a starting password they can change later, and a role), resets a password, disables or
re-enables an account, and deletes one along with everything it owns.

Each account has its own chats, folders, memory, knowledge documents, notes and templates,
automations, email accounts and rules, prompts, presets, usage figures, attachments and
generated files, stored under `data/users/<id>/`. What's shared is the house's infrastructure:
model connections and their API keys, MCP servers, local image and audio setup, hardware and
backup. Only admins can change those; everyone can use them. Non-admins don't see the
Connections, Audio & Images, Hardware, Data and Accounts tabs, and the tools menu hides the
MCP server management for them.

Upgrading from a single-user install: on the first start your existing data moves into the
first admin's folder automatically. The Open WebUI import script now takes `--user EMAIL`
(default: the first admin).
