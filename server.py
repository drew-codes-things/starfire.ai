import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_configs"))

def _bootstrap_dependencies() -> None:
    need_install = shutil.which("uvx") is None or shutil.which("piper") is None
    if not need_install:
        try:
            import fastapi
        except ImportError:
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
from fastapi.staticfiles import StaticFiles

import routes
from config import config
from mcp_manager import McpConnectionError, mcp_manager
from mcp_servers_store import REFERENCE_SERVERS
import ollama_manager
from model_endpoints import detect_ollama

DEFAULT_MCP_PRESETS = ["fetch", "memory"]

DEFAULT_NOTE_TEMPLATES = [
    {
        "name": "Meeting notes",
        "title": "Meeting - ",
        "content": "Attendees:\n\nAgenda:\n- \n\nNotes:\n\n\nAction items:\n- ",
        "note_type": "note",
    },
    {
        "name": "Daily to-do",
        "title": "To-do - today",
        "note_type": "checklist",
        "repeat": "daily",
        "items": [{"text": "", "done": False}],
    },
    {
        "name": "Weekly review",
        "title": "Weekly review",
        "content": "What went well:\n\nWhat didn't:\n\nFocus for next week:\n",
        "note_type": "note",
        "repeat": "weekly",
    },
    {
        "name": "Shopping list",
        "title": "Shopping list",
        "note_type": "checklist",
        "items": [{"text": "", "done": False}],
    },
    {
        "name": "Project brainstorm",
        "title": "Brainstorm - ",
        "content": "Problem:\n\nIdeas:\n- \n\nOpen questions:\n- ",
        "note_type": "note",
    },
]

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

    if not routes.note_templates.list():
        for template in DEFAULT_NOTE_TEMPLATES:
            routes.note_templates.add(**template)
        print(f"  starfire.ai  ->  added {len(DEFAULT_NOTE_TEMPLATES)} starter note templates")

    for server in routes.mcp_servers.list():
        if not server.enabled:
            continue
        try:
            await mcp_manager.connect(server.id, server.command, server.args, server.env or None)
        except McpConnectionError as e:
            print(f"  starfire.ai  ->  could not start MCP server '{server.name}': {e}")

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
        except (asyncio.CancelledError, Exception):
            pass

    for server in routes.mcp_servers.list():
        await mcp_manager.disconnect(server.id)

app = FastAPI(title="starfire.ai", lifespan=lifespan)

@app.middleware("http")
async def require_client_header(request: Request, call_next):
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
