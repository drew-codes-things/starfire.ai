# Backup and restore

Settings → Data can download a zip of the whole `data/` directory, or restore one - the same
thing as the manual "back up the whole `data/` directory" step in
[Data and security](data-and-security.md), through the UI. Restoring **overwrites
your current data** and can't be undone; back up first if you're not sure.

The zip carries no credentials: it leaves out `.key`, `api_keys.json`, `sessions.json`, any
`.env` and any temp or backup copy of these files or of `mcp_servers.json` (such as
`.sessions.json.*.tmp` or `mcp_servers.json.bak`), and blanks MCP server tokens and header values. Credentials inside MCP server arguments
and URLs are replaced with `__secret_removed__`: a password in a URL, a query value whose name
looks like a key, token, secret, password or auth, the value after a flag such as `--api-key`,
`--token` or `--password`, and `NAME=value` or `Name: value` arguments with such names. A secret
passed as a bare argument with none of these markers can't be told apart from a file name, so
give tokens to MCP servers as env values. After restoring on a new install, enter
provider API keys, email passwords and MCP tokens again (the settings page marks email accounts
and MCP servers that need it; remove and add them again). Restoring on the same install keeps
the MCP tokens, arguments and URLs of servers that still exist there. Account records keep their bcrypt
password hashes, so everyone can still log in. Restoring an older zip that still holds `.key` or
`api_keys.json` restores everything else and keeps this install's own keys.
