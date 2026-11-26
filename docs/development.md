# Development

`pip install -r requirements.txt pytest pyflakes`, then `python -m pytest -q` for the server
and the static-file checks, and `node --test tests/js/*.test.js` for the browser logic in
`static/uiLogic.js` and the inline theme script (Node 20 or newer, no packages to install); the desktop
app has its own tests (`cd desktop && npm test`).
The pages and scripts are served as written, with no build step; the server sends static
files gzip-compressed to browsers that accept it and leaves API replies and streams alone.
