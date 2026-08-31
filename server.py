"""starfire.ai — FastAPI entrypoint.

One linear script, matching odysseus-dev's app.py shape at a fraction of the
size: config → app → lifespan (Ollama auto-detect) → routes → static files.
"""

import os
import shutil
import subprocess
import sys


def _bootstrap_dependencies() -> None:
    """First-run bootstrap: if fastapi or the uv/uvx toolchain isn't present
    yet, install requirements.txt automatically — so `python server.py` (or
    `uvicorn server:app`) works right after a fresh clone with no separate
    manual `pip install` step. Must run before any third-party import below,
    using only the standard library, or a missing fastapi would crash this
    file before ever reaching this function.

    Node.js (npx, needed for the Filesystem and MCP Memory servers) is
    deliberately NOT auto-installed — it's a system runtime, not a Python
    package, and installing one automatically means invoking a
    platform-specific package manager (apt/brew/choco) or a downloaded
    installer, which this doesn't do without you choosing to run that
    yourself. A missing npx just gets a clear one-line hint instead.
    """
    need_install = shutil.which("uvx") is None
    if not need_install:
        try:
            import fastapi  # noqa: F401
        except ImportError:
            need_install = True

    if need_install:
        print("  starfire.ai  ->  installing dependencies (first run)...")
        req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req_file], check=True)
        except subprocess.CalledProcessError:
            sys.exit(
                "starfire.ai: automatic dependency install failed — run "
                "'pip install -r requirements.txt' yourself and try again."
            )

    if shutil.which("npx") is None:
        print("  starfire.ai  ->  Node.js not found — the Filesystem and MCP Memory servers "
              "won't connect. Install it from https://nodejs.org to use them.")


_bootstrap_dependencies()

import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import routes
from config import config
from mcp_manager import McpConnectionError, mcp_manager
from mcp_servers_store import REFERENCE_SERVERS
from model_discovery import detect_ollama

# Pre-installed on first run, same spirit as Ollama auto-detection below —
# zero-setup tool-calling for the two reference servers with no configuration
# of their own. Filesystem is deliberately NOT included here: it needs a
# directory choice from the user, so it stays a manual "Quick add" action.
DEFAULT_MCP_PRESETS = ["fetch", "memory"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    detected = await detect_ollama(config.ollama_base_url)
    if detected and not routes.endpoints.find_by_url(detected):
        endpoint = routes.endpoints.add(detected, kind="ollama", label="Ollama (auto-detected)")
        print(f"  starfire.ai  ->  detected Ollama at {endpoint.base_url}, added automatically")

    # Register the default presets once — matched by (command, args) so this
    # is idempotent across restarts and doesn't re-add one the user deleted
    # on purpose... except it does, the same way Ollama auto-detection above
    # re-adds a deleted-but-still-running Ollama: "on" is the resting state
    # this app returns to unless the underlying thing (server/Ollama) is
    # actually gone. Disable via each server's own checkbox instead of
    # deleting it if you don't want it coming back.
    existing = {(s.command, tuple(s.args)) for s in routes.mcp_servers.list()}
    for preset_id in DEFAULT_MCP_PRESETS:
        preset = REFERENCE_SERVERS[preset_id]
        key = (preset["command"], tuple(preset["args"]))
        if key in existing:
            continue
        routes.mcp_servers.add(name=preset["name"], command=preset["command"], args=list(preset["args"]))
        print(f"  starfire.ai  ->  added default MCP server '{preset['name']}'")

    # MCP sessions are subprocess-backed and don't survive a restart — every
    # server marked enabled in the store gets reconnected here, same as
    # odysseus's register_builtin_servers() does for its own builtins.
    for server in routes.mcp_servers.list():
        if not server.enabled:
            continue
        try:
            await mcp_manager.connect(server.id, server.command, server.args, server.env or None)
        except McpConnectionError as e:
            print(f"  starfire.ai  ->  could not start MCP server '{server.name}': {e}")

    scheduler_task = asyncio.create_task(routes.scheduler.run_loop())

    yield

    routes.scheduler.stop()
    scheduler_task.cancel()
    try:
        await scheduler_task
    except (asyncio.CancelledError, Exception):
        pass

    for server in routes.mcp_servers.list():
        await mcp_manager.disconnect(server.id)


app = FastAPI(title="starfire.ai", lifespan=lifespan)


@app.middleware("http")
async def require_client_header(request: Request, call_next):
    """CSRF guard for a no-auth app: any state-changing request to /api/*
    must carry a custom header this app's own frontend always sends (see
    app.js's fetch wrapper). This app sets no CORS headers anywhere, so a
    cross-origin request that needs a preflight (any custom header, or a
    non-"simple" method/content-type) already fails before it's ever sent —
    that already covered every JSON POST/PUT/DELETE here. It did NOT cover
    multipart/form-data POSTs (/api/documents, /api/restore): browsers treat
    that content type as CORS-"simple" and skip preflight entirely, so a
    malicious page the user has open in another tab could otherwise
    auto-submit a <form> straight to /api/restore and silently overwrite
    the whole data/ directory. Requiring this header closes that gap
    uniformly (forces a preflight regardless of content type) rather than
    patching those two routes individually — and covers anything mutating
    added here later without needing to remember to do it again.
    """
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        if "x-starfire-client" not in request.headers:
            return JSONResponse({"detail": "missing required client header"}, status_code=403)
    return await call_next(request)


app.include_router(routes.router)
app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    print(f"\n  starfire.ai  ->  http://localhost:{config.port}\n")
    uvicorn.run("server:app", host=config.bind_host, port=config.port)
