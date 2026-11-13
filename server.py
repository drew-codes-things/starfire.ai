import importlib.util
import logging
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_configs"))

import logging_setup

logging_setup.configure_logging()

def _bootstrap_dependencies() -> None:
    need_install = shutil.which("uvx") is None or shutil.which("piper") is None
    if not need_install and importlib.util.find_spec("fastapi") is None:
        need_install = True

    if need_install:
        print("  starfire.ai  ->  installing dependencies (first run)...")
        req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", req_file], check=True)
        except subprocess.CalledProcessError:
            sys.exit(
                "starfire.ai: automatic dependency install failed - run "
                "'pip install -r requirements.txt' yourself and try again."
            )

    if shutil.which("npx") is None:
        print("  starfire.ai  ->  Node.js not found - the Filesystem and MCP Memory servers "
              "won't connect. Install it from https://nodejs.org to use them.")

_bootstrap_dependencies()

import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import arty_cli_remote
import arty_devices
import auth
import auth_routes
import routes
import security_headers
import static_files
import upload_limits
import user_data
from config import config
from mcp_manager import McpConnectionError, mcp_manager
from mcp_servers_store import REFERENCE_SERVERS
import ollama_manager
from model_endpoints import detect_ollama
from version import VERSION

logger = logging.getLogger(__name__)

DEFAULT_MCP_PRESETS = ["fetch", "memory"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    detected = await detect_ollama(config.ollama_base_url)
    if not detected and ollama_manager.is_installed():
        started, message = await ollama_manager.start(config.ollama_base_url)
        print(f"  starfire.ai  ->  Ollama installed but not running, tried to start it: {message}")
        if started:
            detected = await detect_ollama(config.ollama_base_url)
    if detected and not routes.endpoints.find_by_url(detected):
        endpoint = routes.endpoints.add(detected, kind="ollama", label="Ollama (auto-detected)")
        print(f"  starfire.ai  ->  detected Ollama at {endpoint.base_url}, added automatically")

    existing = {(s.command, tuple(s.args)) for s in routes.mcp_servers.list()}
    for preset_id in DEFAULT_MCP_PRESETS:
        preset = REFERENCE_SERVERS[preset_id]
        key = (preset["command"], tuple(preset["args"]))
        if key in existing:
            continue
        routes.mcp_servers.add(name=preset["name"], command=preset["command"], args=list(preset["args"]))
        print(f"  starfire.ai  ->  added default MCP server '{preset['name']}'")

    for server in routes.mcp_servers.list():
        if not server.enabled:
            continue
        try:
            await routes.connect_mcp_server(server)
        except McpConnectionError as e:
            print(f"  starfire.ai  ->  could not start MCP server '{server.name}': {e}")

    first_admin = auth_routes.users.first_admin()
    if first_admin:
        moved = routes.registry.migrate_legacy(first_admin.id)
        if moved:
            print(f"  starfire.ai  ->  moved {len(moved)} data file(s) into {first_admin.email}'s account")

    pruned = 0
    for user_id in routes.registry.user_ids():
        with user_data.run_as(user_id):
            pruned += routes.attachment_store.prune()
    if pruned:
        print(f"  starfire.ai  ->  removed {pruned} attachment(s) older than 30 days")

    scheduler_task = asyncio.create_task(routes.scheduler.run_loop())
    email_rule_task = asyncio.create_task(routes.email_rule_checker.run_loop())

    yield

    routes.scheduler.stop()
    scheduler_task.cancel()
    routes.email_rule_checker.stop()
    email_rule_task.cancel()
    for task in (scheduler_task, email_rule_task):
        try:
            await task
        except asyncio.CancelledError:
            logger.debug("background task %s stopped", task.get_name())
        except Exception:
            logger.exception("background task %s failed while starfire was shutting down", task.get_name())

    for server in routes.mcp_servers.list():
        await mcp_manager.disconnect(server.id)

if not auth_routes.users.has_users():
    print(f"starfire.ai: no account yet - open the app and choose your email and password on {auth.SETUP_PAGE} "
          "(everything else stays locked until you do).", file=sys.stderr)

app = FastAPI(title="starfire.ai", lifespan=lifespan)

@app.middleware("http")
async def require_client_header(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        if "x-starfire-client" not in request.headers:
            return JSONResponse({"detail": "missing required client header"}, status_code=403)
    return await call_next(request)

REVALIDATED_SUFFIXES = (".html", ".js", ".css", "/")

@app.middleware("http")
async def revalidate_app_files(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/") and request.url.path.endswith(REVALIDATED_SUFFIXES):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response

upload_limits.install(app, config)
auth.install(app, auth_routes.users, auth_routes.sessions)
security_headers.install(app)

@app.get("/api/health")
async def health():
    return {"ok": True, "version": VERSION}

app.include_router(auth_routes.router)
app.include_router(routes.router)
app.include_router(arty_devices.router)
app.include_router(arty_cli_remote.router)
app.mount("/", static_files.compressed_static("static"), name="static")

if __name__ == "__main__":
    import uvicorn
    print(f"\n  starfire.ai  ->  http://localhost:{config.port}\n")
    uvicorn.run("server:app", host=config.bind_host, port=config.port)
