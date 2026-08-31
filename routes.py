"""API routes — wires config, api_key_manager, model_endpoints, providers,
model_discovery, and chat_stream together. Mirrors odysseus-dev's
routes/model_routes.py + routes/chat_routes.py naming, trimmed to what a
single-user local chat app needs.
"""

import io
import json
import logging
import os
import re
import zipfile

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import builtin_tools
import date_parsing
import email_client
import file_edit_tool
import hardware_probe
import model_capabilities
import pending_edits_store
from agent_loop import run_chat_with_tools
from api_key_manager import APIKeyManager
from chat_session_store import ChatSessionStore
from chat_stream import _sse_line, stream_chat
from config import config
from context_budget import trim_to_budget
from documents_store import DocumentStore
import comfyui_client
import piper_tts
from comfyui_config_store import ComfyUIConfigStore
from custom_workflow_store import CustomWorkflowStore
from generated_files_store import GeneratedFileStore
from piper_config_store import PiperConfigStore
from email_rule_checker import EmailRuleChecker
from email_rule_store import EmailRuleStore
from email_store import EmailAccountStore
from mcp_manager import McpConnectionError, mcp_manager
from mcp_servers_store import MCP_SERVER_REPOSITORY, REFERENCE_SERVERS, McpServerStore
from memory_store import MemoryStore
from model_discovery import detect_ollama, discover_servers
from model_endpoints import ModelEndpointStore
from note_store import NoteStore
from note_template_store import NoteTemplateStore
from preset_store import PresetStore
from providers import build_models_url
from task_scheduler import TaskScheduler, compute_next_run
from task_store import ScheduledTask, TaskRunStore, TaskStore
from usage_store import UsageStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

api_keys = APIKeyManager(config.data_dir)
endpoints = ModelEndpointStore(config.data_dir)
mcp_servers = McpServerStore(config.data_dir)
memory = MemoryStore(config.data_dir)
documents = DocumentStore(config.data_dir)
tasks = TaskStore(config.data_dir)
task_runs = TaskRunStore(config.data_dir)
email_accounts = EmailAccountStore(config.data_dir)
email_rules = EmailRuleStore(config.data_dir)
presets = PresetStore(config.data_dir)
generated_files = GeneratedFileStore(config.data_dir)
comfyui_config = ComfyUIConfigStore(config.data_dir)
piper_config = PiperConfigStore(config.data_dir)
custom_workflows = CustomWorkflowStore(config.data_dir)
usage = UsageStore(config.data_dir)
notes = NoteStore(config.data_dir)
note_templates = NoteTemplateStore(config.data_dir)
chat_sessions = ChatSessionStore(config.data_dir)


def _tool_context(base_url: str = "", api_key: str | None = None, model: str = "",
                   require_edit_approval: bool = False) -> builtin_tools.ToolContext:
    return builtin_tools.ToolContext(
        memory=memory, documents=documents, tasks=tasks, task_runs=task_runs,
        email_accounts=email_accounts, api_keys=api_keys, notes=notes,
        endpoints=endpoints, generated_files=generated_files,
        comfyui_config=comfyui_config, piper_config=piper_config, custom_workflows=custom_workflows,
        base_url=base_url, api_key=api_key, model=model,
        require_edit_approval=require_edit_approval,
    )

# Ported from odysseus-dev's src/memory.py:process_inline_memory_command —
# intercepted before the message ever reaches the LLM.
_INLINE_MEMORY_RE = re.compile(r"^(?:remember|memorize|save|note|store)[:\-]?\s+(.+)$", re.IGNORECASE)


def _api_key_for(endpoint_id: str, provider: str) -> str | None:
    """Keys are saved keyed by endpoint id (falls back to provider name for
    the common single-key-per-provider case, e.g. a pre-existing 'openai' key
    reused for a newly added OpenAI endpoint)."""
    keys = api_keys.load()
    return keys.get(endpoint_id) or keys.get(provider) or None


# ── endpoint management ──────────────────────────────────────────────

class AddEndpointBody(BaseModel):
    base_url: str
    api_key: str | None = None
    kind: str = "api-key"  # "ollama" | "api-key"
    label: str = ""


class TestEndpointBody(BaseModel):
    base_url: str
    api_key: str | None = None


@router.get("/model-endpoints")
async def list_endpoints():
    return {"endpoints": [
        {"id": e.id, "base_url": e.base_url, "kind": e.kind, "provider": e.provider, "label": e.label}
        for e in endpoints.list()
    ]}


async def _probe_endpoint(base_url: str, api_key: str | None) -> tuple[bool, str]:
    from providers import build_headers
    url = build_models_url(base_url)
    headers = build_headers(api_key, base_url)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 200:
            return True, "ok"
        if r.status_code in (401, 403):
            return False, "authentication failed - check the API key"
        return False, f"server returned {r.status_code}"
    except httpx.HTTPError as e:
        return False, f"cannot reach {url}: {e}"


@router.post("/model-endpoints/test")
async def test_endpoint(body: TestEndpointBody):
    ok, message = await _probe_endpoint(body.base_url, body.api_key)
    return {"ok": ok, "message": message}


@router.post("/model-endpoints")
async def add_endpoint(body: AddEndpointBody):
    if not body.base_url.strip():
        raise HTTPException(400, "base_url is required")
    ok, message = await _probe_endpoint(body.base_url, body.api_key)
    if not ok:
        raise HTTPException(502, message)
    endpoint = endpoints.add(body.base_url, kind=body.kind, label=body.label)
    if body.api_key:
        api_keys.save(endpoint.id, body.api_key)
    return {"id": endpoint.id, "base_url": endpoint.base_url, "kind": endpoint.kind,
            "provider": endpoint.provider, "label": endpoint.label}


@router.delete("/model-endpoints/{endpoint_id}")
async def delete_endpoint(endpoint_id: str):
    if not endpoints.delete(endpoint_id):
        raise HTTPException(404, "endpoint not found")
    api_keys.delete(endpoint_id)
    return {"ok": True}


@router.get("/discover")
async def discover():
    return {"found": await discover_servers()}


# ── models ────────────────────────────────────────────────────────────

@router.get("/models")
async def list_models():
    """Merged model list across every configured endpoint."""
    result = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for e in endpoints.list():
            from providers import build_headers
            api_key = _api_key_for(e.id, e.provider)
            url = build_models_url(e.base_url)
            headers = build_headers(api_key, e.base_url)
            try:
                r = await client.get(url, headers=headers)
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError) as ex:
                logger.warning("model list probe failed for %s: %s", e.base_url, ex)
                continue
            if e.provider == "ollama":
                for m in data.get("models", []):
                    result.append({"id": m.get("name"), "endpoint_id": e.id, "provider": "ollama",
                                    "size": m.get("size")})
            else:
                for m in data.get("data", []):
                    result.append({"id": m.get("id"), "endpoint_id": e.id, "provider": e.provider})
    return {"models": result}


@router.post("/warm")
async def warm(body: dict):
    """Keep-alive ping — Ollama-only, no-op for hosted providers."""
    endpoint_id = body.get("endpoint_id")
    model = body.get("model")
    endpoint = endpoints.get(endpoint_id) if endpoint_id else None
    if not endpoint or endpoint.provider != "ollama" or not model:
        return {"ok": True}
    from providers import _ollama_api_root
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(_ollama_api_root(endpoint.base_url) + "/generate",
                                    json={"model": model, "keep_alive": -1})
            await r.aclose()
    except httpx.HTTPError:
        pass
    return {"ok": True}


# ── config ────────────────────────────────────────────────────────────

@router.get("/config")
async def get_config():
    from model_discovery import detect_ollama
    detected = await detect_ollama(config.ollama_base_url)
    return {"ollama_base_url": detected or "http://localhost:11434"}


# ── directory browser (for the filesystem MCP server's path field) ─────
#
# A browser can't hand a backend subprocess a real OS path via any file/
# folder picker API — showDirectoryPicker() and <input webkitdirectory> both
# only ever expose a sandboxed handle or relative paths, by design, for
# privacy reasons that make sense for an arbitrary website but don't apply
# here: starfire's browser tab and its backend are the same machine, at the
# same trust level (the backend already runs a full shell tool). So this is
# a small server-side directory listing instead, browsed by clicking
# folders in the UI — same trust boundary as everything else in this app,
# not a new one.

def _list_dirs(path: str) -> dict:
    base = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(base):
        raise HTTPException(400, f"not a directory: {base}")
    entries = []
    try:
        for name in sorted(os.listdir(base), key=str.lower):
            full = os.path.join(base, name)
            if name.startswith(".") or not os.path.isdir(full):
                continue
            entries.append({"name": name, "path": full})
    except PermissionError:
        pass  # unreadable directory — just show it empty rather than erroring
    parent = os.path.dirname(base)
    return {"path": base, "parent": parent if parent != base else None, "entries": entries}


@router.get("/browse-dir")
async def browse_dir(path: str = ""):
    return _list_dirs(path)


# ── MCP servers ──────────────────────────────────────────────────────

class AddMcpServerBody(BaseModel):
    # either a custom command...
    name: str | None = None
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    # ...or a one-click reference-server preset
    preset: str | None = None
    path: str | None = None  # required by the filesystem preset


@router.get("/mcp/repository")
async def list_mcp_repository():
    return {"repository": [
        {"id": rid, "name": p["name"], "needs_path": p.get("needs_path", False), "env_fields": p.get("env_fields", [])}
        for rid, p in MCP_SERVER_REPOSITORY.items()
    ]}


@router.get("/mcp/servers")
async def list_mcp_servers():
    return {"servers": [
        {"id": s.id, "name": s.name, "command": s.command, "args": s.args, "enabled": s.enabled,
         "connected": mcp_manager.is_connected(s.id), "tool_count": len(mcp_manager.list_tools(s.id))}
        for s in mcp_servers.list()
    ], "presets": [{"id": pid, **{k: v for k, v in p.items() if k != "args"}} for pid, p in REFERENCE_SERVERS.items()]}


@router.post("/mcp/servers")
async def add_mcp_server(body: AddMcpServerBody):
    if body.preset:
        preset = REFERENCE_SERVERS.get(body.preset) or MCP_SERVER_REPOSITORY.get(body.preset)
        if not preset:
            raise HTTPException(400, f"unknown preset '{body.preset}'")
        if preset.get("needs_path") and not body.path:
            raise HTTPException(400, f"the {preset['name']} server needs a directory path")
        missing = [f["key"] for f in preset.get("env_fields", []) if not body.env.get(f["key"])]
        if missing:
            raise HTTPException(400, f"the {preset['name']} server needs: {', '.join(missing)}")
        name, command, args = preset["name"], preset["command"], list(preset["args"])
        if preset.get("needs_path"):
            args.append(body.path)
    else:
        if not body.command:
            raise HTTPException(400, "command is required")
        name, command, args = body.name or body.command, body.command, body.args

    server = mcp_servers.add(name=name, command=command, args=args, env=body.env or None)
    try:
        tools = await mcp_manager.connect(server.id, server.command, server.args, server.env or None)
    except McpConnectionError as e:
        mcp_servers.delete(server.id)  # don't persist a server that can't start
        raise HTTPException(502, str(e))

    return {"id": server.id, "name": server.name, "command": server.command, "args": server.args,
            "enabled": server.enabled, "tool_count": len(tools)}


@router.patch("/mcp/servers/{server_id}")
async def update_mcp_server(server_id: str, body: dict):
    if "enabled" not in body:
        raise HTTPException(400, "enabled is required")
    if not mcp_servers.set_enabled(server_id, bool(body["enabled"])):
        raise HTTPException(404, "server not found")
    return {"ok": True}


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str):
    if not mcp_servers.delete(server_id):
        raise HTTPException(404, "server not found")
    await mcp_manager.disconnect(server_id)
    return {"ok": True}


# ── memory ───────────────────────────────────────────────────────────

class AddMemoryBody(BaseModel):
    text: str
    category: str = "fact"


class UpdateMemoryBody(BaseModel):
    text: str | None = None
    category: str | None = None


class SetPinnedBody(BaseModel):
    pinned: bool


@router.get("/memory")
async def list_memory():
    return {"memories": [
        {"id": e.id, "text": e.text, "category": e.category, "source": e.source,
         "timestamp": e.timestamp, "pinned": e.pinned, "uses": e.uses}
        for e in memory.list()
    ]}


@router.post("/memory")
async def add_memory(body: AddMemoryBody):
    if not body.text.strip():
        raise HTTPException(400, "text is required")
    entry = memory.add(body.text, category=body.category, source="user")
    return {"id": entry.id, "text": entry.text, "category": entry.category}


@router.put("/memory/{memory_id}")
async def update_memory(memory_id: str, body: UpdateMemoryBody):
    if not memory.update(memory_id, text=body.text, category=body.category):
        raise HTTPException(404, "memory not found")
    return {"ok": True}


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str):
    if not memory.delete(memory_id):
        raise HTTPException(404, "memory not found")
    return {"ok": True}


@router.post("/memory/{memory_id}/pin")
async def pin_memory(memory_id: str, body: SetPinnedBody):
    if not memory.set_pinned(memory_id, body.pinned):
        raise HTTPException(404, "memory not found")
    return {"ok": True}


# ── documents ────────────────────────────────────────────────────────

@router.get("/documents")
async def list_documents():
    return {"documents": [
        {"id": d.id, "filename": d.filename, "added": d.added, "chunk_count": d.chunk_count}
        for d in documents.list()
    ]}


@router.post("/documents")
async def add_document(file: UploadFile):
    raw = await file.read()
    try:
        doc = documents.add(file.filename or "untitled", raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": doc.id, "filename": doc.filename, "chunk_count": doc.chunk_count}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    if not documents.delete(document_id):
        raise HTTPException(404, "document not found")
    return {"ok": True}


# ── scheduled tasks ──────────────────────────────────────────────────

class CreateTaskBody(BaseModel):
    name: str = ""
    prompt: str
    schedule: str  # once | daily | weekly | cron
    scheduled_time: str = ""
    scheduled_day: str = ""
    cron_expression: str = ""
    endpoint_id: str = ""
    model: str = ""
    enabled_mcp_servers: list[str] = []
    enabled_builtin_tools: list[str] = []


def _task_chat_fn(endpoint_id, model, prompt, enabled_mcp_servers, enabled_builtin_tools):
    from agent_loop import run_chat_collected

    endpoint = endpoints.get(endpoint_id) if endpoint_id else None
    if not endpoint:
        raise RuntimeError("task has no valid endpoint configured")
    api_key = _api_key_for(endpoint.id, endpoint.provider)
    ctx = _tool_context(base_url=endpoint.base_url, api_key=api_key, model=model)
    return run_chat_collected(
        endpoint.base_url, api_key, model, [{"role": "user", "content": prompt}],
        enabled_server_ids=set(enabled_mcp_servers), enabled_builtin_tools=set(enabled_builtin_tools),
        ctx=ctx,
    )


scheduler = TaskScheduler(tasks, task_runs, _task_chat_fn)


def _email_rule_password(account_id: str) -> str | None:
    return api_keys.load().get(account_id)


async def _email_rule_chat_fn(endpoint_id: str, model: str, prompt: str) -> str:
    from agent_loop import run_chat_collected

    endpoint = endpoints.get(endpoint_id) if endpoint_id else None
    if not endpoint:
        raise RuntimeError("this rule's ai_summarize_note action has no valid endpoint configured")
    api_key = _api_key_for(endpoint.id, endpoint.provider)
    return await run_chat_collected(endpoint.base_url, api_key, model, [{"role": "user", "content": prompt}])


email_rule_checker = EmailRuleChecker(email_rules, email_accounts, _email_rule_password, _email_rule_chat_fn, notes)


@router.get("/tasks")
async def list_tasks():
    return {"tasks": [
        {"id": t.id, "name": t.name, "prompt": t.prompt, "schedule": t.schedule,
         "scheduled_time": t.scheduled_time, "scheduled_day": t.scheduled_day,
         "cron_expression": t.cron_expression, "status": t.status, "endpoint_id": t.endpoint_id,
         "model": t.model, "next_run": t.next_run, "last_run": t.last_run, "run_count": t.run_count}
        for t in tasks.list()
    ]}


@router.post("/tasks")
async def create_task(body: CreateTaskBody):
    if not body.prompt.strip():
        raise HTTPException(400, "prompt is required")
    try:
        next_run = compute_next_run(body.schedule, body.scheduled_time, body.scheduled_day, body.cron_expression)
    except ValueError as e:
        raise HTTPException(400, str(e))
    task = tasks.add(ScheduledTask(
        id="", name=body.name or body.prompt[:40], prompt=body.prompt, schedule=body.schedule,
        scheduled_time=body.scheduled_time, scheduled_day=body.scheduled_day,
        cron_expression=body.cron_expression, endpoint_id=body.endpoint_id, model=body.model,
        enabled_mcp_servers=body.enabled_mcp_servers, enabled_builtin_tools=body.enabled_builtin_tools,
        next_run=next_run,
    ))
    return {"id": task.id, "next_run": task.next_run}


@router.post("/tasks/{task_id}/pause")
async def pause_task(task_id: str):
    if not tasks.update(task_id, status="paused"):
        raise HTTPException(404, "task not found")
    return {"ok": True}


@router.post("/tasks/{task_id}/resume")
async def resume_task(task_id: str):
    if not tasks.update(task_id, status="active"):
        raise HTTPException(404, "task not found")
    return {"ok": True}


@router.post("/tasks/{task_id}/run")
async def run_task_now(task_id: str):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    await scheduler.run_now(task)
    return {"ok": True}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    if not tasks.delete(task_id):
        raise HTTPException(404, "task not found")
    return {"ok": True}


@router.get("/tasks/{task_id}/runs")
async def get_task_runs(task_id: str):
    return {"runs": [
        {"id": r.id, "started": r.started, "finished": r.finished, "status": r.status, "output": r.output}
        for r in task_runs.for_task(task_id)
    ]}


@router.get("/tasks/runs/recent")
async def get_recent_runs():
    return {"runs": [
        {"id": r.id, "task_id": r.task_id, "started": r.started, "finished": r.finished,
         "status": r.status, "output": r.output}
        for r in task_runs.recent()
    ]}


# ── email ────────────────────────────────────────────────────────────

class AddEmailAccountBody(BaseModel):
    label: str = ""
    email_address: str
    password: str
    imap_host: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""


class SendEmailBody(BaseModel):
    to: str
    subject: str = ""
    body: str


class ReplyEmailBody(BaseModel):
    body: str


def _email_password(account_id: str) -> str | None:
    return api_keys.load().get(account_id)


@router.get("/email/accounts")
async def list_email_accounts():
    return {"accounts": [
        {"id": a.id, "label": a.label, "email_address": a.email_address,
         "imap_host": a.imap_host, "imap_port": a.imap_port}
        for a in email_accounts.list()
    ]}


@router.post("/email/accounts")
async def add_email_account(body: AddEmailAccountBody):
    account = email_accounts.add(
        label=body.label, email_address=body.email_address, imap_host=body.imap_host,
        imap_port=body.imap_port, smtp_host=body.smtp_host or body.imap_host.replace("imap", "smtp", 1),
        smtp_port=body.smtp_port, username=body.username,
    )
    api_keys.save(account.id, body.password)
    try:
        email_client.list_folders(account, body.password)
    except Exception as e:
        email_accounts.delete(account.id)
        api_keys.delete(account.id)
        raise HTTPException(502, f"could not connect: {e}")
    return {"id": account.id, "label": account.label, "email_address": account.email_address}


@router.delete("/email/accounts/{account_id}")
async def delete_email_account(account_id: str):
    if not email_accounts.delete(account_id):
        raise HTTPException(404, "account not found")
    api_keys.delete(account_id)
    return {"ok": True}


def _get_account_or_404(account_id: str):
    account = email_accounts.get(account_id)
    if not account:
        raise HTTPException(404, "account not found")
    password = _email_password(account_id)
    if not password:
        raise HTTPException(500, "no password on file for this account")
    return account, password


@router.get("/email/{account_id}/folders")
async def list_email_folders(account_id: str):
    account, password = _get_account_or_404(account_id)
    try:
        return {"folders": email_client.list_folders(account, password)}
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get("/email/{account_id}/messages")
async def list_email_messages(account_id: str, folder: str = "INBOX"):
    account, password = _get_account_or_404(account_id)
    try:
        return {"messages": email_client.list_messages(account, password, folder)}
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get("/email/{account_id}/message/{uid}")
async def read_email_message(account_id: str, uid: str, folder: str = "INBOX"):
    account, password = _get_account_or_404(account_id)
    try:
        return email_client.read_message(account, password, folder, uid)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get("/email/{account_id}/search")
async def search_email(account_id: str, query: str, folder: str = "INBOX"):
    account, password = _get_account_or_404(account_id)
    try:
        return {"results": email_client.search_messages(account, password, query, folder)}
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post("/email/{account_id}/send")
async def send_email(account_id: str, body: SendEmailBody):
    account, password = _get_account_or_404(account_id)
    try:
        email_client.send_message(account, password, body.to, body.subject, body.body)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@router.post("/email/{account_id}/message/{uid}/reply")
async def reply_email(account_id: str, uid: str, body: ReplyEmailBody, folder: str = "INBOX"):
    account, password = _get_account_or_404(account_id)
    try:
        email_client.reply_message(account, password, folder, uid, body.body)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@router.post("/email/{account_id}/message/{uid}/mark-read")
async def mark_email_read(account_id: str, uid: str, folder: str = "INBOX"):
    account, password = _get_account_or_404(account_id)
    try:
        email_client.mark_read(account, password, folder, uid)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@router.post("/email/{account_id}/message/{uid}/archive")
async def archive_email(account_id: str, uid: str, folder: str = "INBOX"):
    account, password = _get_account_or_404(account_id)
    try:
        email_client.archive_message(account, password, folder, uid)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


@router.delete("/email/{account_id}/message/{uid}")
async def delete_email(account_id: str, uid: str, folder: str = "INBOX"):
    account, password = _get_account_or_404(account_id)
    try:
        email_client.delete_message(account, password, folder, uid)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True}


class AiActionBody(BaseModel):
    endpoint_id: str
    model: str
    instruction: str = ""


@router.post("/email/{account_id}/message/{uid}/ai")
async def email_ai_action(account_id: str, uid: str, action: str, body: AiActionBody, folder: str = "INBOX"):
    if action not in ("summarize", "draft_reply", "check_urgency"):
        raise HTTPException(400, "invalid action")
    account, password = _get_account_or_404(account_id)
    endpoint = endpoints.get(body.endpoint_id)
    if not endpoint:
        raise HTTPException(404, "endpoint not found")
    api_key = _api_key_for(endpoint.id, endpoint.provider)
    ctx = _tool_context(base_url=endpoint.base_url, api_key=api_key, model=body.model)
    try:
        message = email_client.read_message(account, password, folder, uid, mark_seen=False)
        result = await builtin_tools.email_ai_action(action, message, body.instruction, ctx)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"result": result}


# ── email rules ──────────────────────────────────────────────────────

class EmailRuleBody(BaseModel):
    account_id: str
    folder: str = "INBOX"
    match_field: str = "from"
    match_value: str
    action: str = "add_note"
    endpoint_id: str = ""
    model: str = ""


def _rule_dict(r) -> dict:
    return {"id": r.id, "account_id": r.account_id, "folder": r.folder, "match_field": r.match_field,
            "match_value": r.match_value, "action": r.action, "endpoint_id": r.endpoint_id,
            "model": r.model, "enabled": r.enabled}


@router.get("/email/rules")
async def list_email_rules():
    return {"rules": [_rule_dict(r) for r in email_rules.list()]}


@router.post("/email/rules")
async def create_email_rule(body: EmailRuleBody):
    if not body.match_value.strip():
        raise HTTPException(400, "match_value is required")
    if body.action == "ai_summarize_note" and not (body.endpoint_id and body.model):
        raise HTTPException(400, "ai_summarize_note needs an endpoint and model")
    rule = email_rules.add(account_id=body.account_id, folder=body.folder, match_field=body.match_field,
                             match_value=body.match_value, action=body.action,
                             endpoint_id=body.endpoint_id, model=body.model)
    return _rule_dict(rule)


@router.patch("/email/rules/{rule_id}")
async def update_email_rule(rule_id: str, body: dict):
    if "enabled" not in body:
        raise HTTPException(400, "enabled is required")
    if not email_rules.update(rule_id, enabled=bool(body["enabled"])):
        raise HTTPException(404, "rule not found")
    return {"ok": True}


@router.delete("/email/rules/{rule_id}")
async def delete_email_rule(rule_id: str):
    if not email_rules.delete(rule_id):
        raise HTTPException(404, "rule not found")
    return {"ok": True}


# ── notes ────────────────────────────────────────────────────────────

class NoteItemBody(BaseModel):
    text: str
    done: bool = False


class CreateNoteBody(BaseModel):
    title: str = ""
    content: str = ""
    items: list[NoteItemBody] = []
    note_type: str = "note"
    color: str = ""
    label: str = ""
    due_date: str = ""
    repeat: str = "none"


class UpdateNoteBody(BaseModel):
    title: str | None = None
    content: str | None = None
    items: list[NoteItemBody] | None = None
    note_type: str | None = None
    color: str | None = None
    label: str | None = None
    due_date: str | None = None
    repeat: str | None = None


def _note_dict(n) -> dict:
    return {
        "id": n.id, "title": n.title, "content": n.content,
        "items": [{"text": i.text, "done": i.done} for i in n.items],
        "note_type": n.note_type, "color": n.color, "label": n.label,
        "pinned": n.pinned, "archived": n.archived, "due_date": n.due_date,
        "repeat": n.repeat, "source": n.source, "sort_order": n.sort_order, "created": n.created,
    }


@router.get("/notes")
async def list_notes(archived: bool | None = None, label: str | None = None):
    return {"notes": [_note_dict(n) for n in notes.list(archived=archived, label=label)]}


@router.post("/notes")
async def create_note(body: CreateNoteBody):
    note = notes.add(
        title=body.title, content=body.content, items=[i.model_dump() for i in body.items],
        note_type=body.note_type, color=body.color, label=body.label,
        due_date=date_parsing.parse_due_date(body.due_date), repeat=body.repeat,
    )
    return _note_dict(note)


@router.get("/notes/{note_id}")
async def get_note(note_id: str):
    note = notes.get(note_id)
    if not note:
        raise HTTPException(404, "note not found")
    return _note_dict(note)


@router.put("/notes/{note_id}")
async def update_note(note_id: str, body: UpdateNoteBody):
    fields = body.model_dump(exclude_unset=True)
    if "items" in fields and fields["items"] is not None:
        fields["items"] = [dict(i) for i in fields["items"]]
    if "due_date" in fields and fields["due_date"] is not None:
        fields["due_date"] = date_parsing.parse_due_date(fields["due_date"])
    if not notes.update(note_id, **fields):
        raise HTTPException(404, "note not found")
    return {"ok": True}


@router.delete("/notes/{note_id}")
async def delete_note(note_id: str):
    if not notes.delete(note_id):
        raise HTTPException(404, "note not found")
    return {"ok": True}


@router.post("/notes/{note_id}/pin")
async def pin_note(note_id: str, body: SetPinnedBody):
    if not notes.set_pinned(note_id, body.pinned):
        raise HTTPException(404, "note not found")
    return {"ok": True}


class SetArchivedBody(BaseModel):
    archived: bool


@router.post("/notes/{note_id}/archive")
async def archive_note(note_id: str, body: SetArchivedBody):
    if not notes.set_archived(note_id, body.archived):
        raise HTTPException(404, "note not found")
    return {"ok": True}


@router.post("/notes/{note_id}/items/{index}/toggle")
async def toggle_note_item(note_id: str, index: int):
    if not notes.toggle_item(note_id, index):
        raise HTTPException(404, "note or item not found")
    return {"ok": True}


# ── note templates ───────────────────────────────────────────────────

class NoteTemplateBody(BaseModel):
    name: str
    title: str = ""
    content: str = ""
    items: list[NoteItemBody] = []
    note_type: str = "note"
    label: str = ""
    color: str = ""
    repeat: str = "none"


def _template_dict(t) -> dict:
    return {"id": t.id, "name": t.name, "title": t.title, "content": t.content,
            "items": t.items, "note_type": t.note_type, "label": t.label,
            "color": t.color, "repeat": t.repeat}


@router.get("/note-templates")
async def list_note_templates():
    return {"templates": [_template_dict(t) for t in note_templates.list()]}


@router.post("/note-templates")
async def create_note_template(body: NoteTemplateBody):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    template = note_templates.add(
        name=body.name, title=body.title, content=body.content,
        items=[i.model_dump() for i in body.items], note_type=body.note_type,
        label=body.label, color=body.color, repeat=body.repeat,
    )
    return _template_dict(template)


@router.delete("/note-templates/{template_id}")
async def delete_note_template(template_id: str):
    if not note_templates.delete(template_id):
        raise HTTPException(404, "template not found")
    return {"ok": True}


@router.post("/note-templates/{template_id}/use")
async def use_note_template(template_id: str):
    template = note_templates.get(template_id)
    if not template:
        raise HTTPException(404, "template not found")
    note = notes.add(
        title=template.title, content=template.content,
        items=[dict(i) for i in template.items], note_type=template.note_type,
        label=template.label, color=template.color, repeat=template.repeat,
    )
    return _note_dict(note)


# ── chat ──────────────────────────────────────────────────────────────

class ChatBody(BaseModel):
    endpoint_id: str
    model: str
    messages: list[dict]
    system: str | None = None
    options: dict | None = None
    enabled_mcp_servers: list[str] = []
    enabled_builtin_tools: list[str] = []
    require_edit_approval: bool = False


async def _track_usage(gen, provider: str, model: str, input_text: str):
    """Passes SSE chunks through unchanged while accumulating the delta text,
    then records one usage_store entry once the stream ends. Wraps every
    chat_stream call (tool-calling or not) in one place rather than
    instrumenting run_chat_with_tools/stream_chat separately."""
    output_parts: list[str] = []
    async for chunk in gen:
        yield chunk
        try:
            line = chunk.decode("utf-8", errors="replace").strip()
            if line.startswith("data:"):
                obj = json.loads(line[len("data:"):].strip())
                if "delta" in obj:
                    output_parts.append(obj["delta"])
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    usage.record(provider, model, input_text, "".join(output_parts))


async def _synthetic_reply(text: str):
    yield _sse_line({"delta": text})
    yield _sse_line({"done": True})


@router.post("/chat_stream")
async def chat_stream_endpoint(body: ChatBody):
    # Inline "remember: X" is intercepted before any LLM call, mirroring
    # odysseus's handle_memory_command — cheap, instant, and doesn't spend
    # the user's API budget for something that's just a local write.
    if body.messages:
        last = body.messages[-1]
        if last.get("role") == "user":
            m = _INLINE_MEMORY_RE.match((last.get("content") or "").strip())
            if m:
                entry = memory.add(m.group(1).strip(), source="inline_command")
                return StreamingResponse(_synthetic_reply(f"Saved to memory: “{entry.text}”"),
                                          media_type="text/event-stream")

    endpoint = endpoints.get(body.endpoint_id)
    if not endpoint:
        raise HTTPException(404, "endpoint not found")
    api_key = _api_key_for(endpoint.id, endpoint.provider)

    # The frontend sends the session's full message list rather than
    # pre-slicing to a fixed count — trimming to an actual token budget
    # happens once, here, so it applies identically whether or not tools
    # are enabled below. No per-chat control over this budget anymore (the
    # header's ctx-size selector was removed — it only ever affected Ollama
    # requests directly, and confusingly meant something different, or
    # nothing at all, for every other provider), so this fixed default has
    # to be generous enough on its own: most current models comfortably
    # support context windows far larger than the old default of 2048.
    DEFAULT_CONTEXT_BUDGET_TOKENS = 8192
    num_ctx = (body.options or {}).get("num_ctx", DEFAULT_CONTEXT_BUDGET_TOKENS)
    messages = trim_to_budget(body.messages, num_ctx)

    if body.enabled_mcp_servers or body.enabled_builtin_tools:
        # Make sure every requested server is actually connected — a server
        # added earlier in this process lifetime already is (connect() below
        # is then a cheap no-op); one added in a previous process run needs
        # reconnecting since MCP sessions aren't persisted across restarts.
        for server_id in body.enabled_mcp_servers:
            if mcp_manager.is_connected(server_id):
                continue
            server = mcp_servers.get(server_id)
            if not server or not server.enabled:
                continue
            try:
                await mcp_manager.connect(server.id, server.command, server.args, server.env or None)
            except McpConnectionError as e:
                logger.warning("could not reconnect MCP server %s: %s", server_id, e)
        ctx = _tool_context(base_url=endpoint.base_url, api_key=api_key, model=body.model,
                            require_edit_approval=body.require_edit_approval)
        generator = run_chat_with_tools(endpoint.base_url, api_key, body.model, messages,
                                          body.system, body.options, set(body.enabled_mcp_servers),
                                          set(body.enabled_builtin_tools), ctx)
    else:
        generator = stream_chat(endpoint.base_url, api_key, body.model, messages, body.system, body.options)

    input_text = "\n".join(m.get("content") or "" for m in messages)
    generator = _track_usage(generator, endpoint.provider, body.model, input_text)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# ── chat sessions (persisted conversations) ─────────────────────────────

class CreateChatSessionBody(BaseModel):
    title: str = "New chat"
    messages: list[dict] = []
    parent_session_id: str = ""
    branch_point: int = -1


class UpdateChatSessionBody(BaseModel):
    title: str | None = None
    messages: list[dict] | None = None
    endpoint_id: str | None = None
    model: str | None = None
    pinned: bool | None = None


def _session_summary(s) -> dict:
    return {"id": s.id, "title": s.title, "endpoint_id": s.endpoint_id, "model": s.model,
            "pinned": s.pinned, "parent_session_id": s.parent_session_id, "branch_point": s.branch_point,
            "created": s.created, "updated": s.updated, "message_count": len(s.messages)}


@router.get("/chat/sessions")
async def list_chat_sessions(q: str = ""):
    return {"sessions": [_session_summary(s) for s in chat_sessions.list(query=q)]}


@router.post("/chat/sessions")
async def create_chat_session(body: CreateChatSessionBody):
    session = chat_sessions.add(title=body.title, messages=body.messages,
                                  parent_session_id=body.parent_session_id, branch_point=body.branch_point)
    return {**_session_summary(session), "messages": session.messages}


@router.get("/chat/sessions/{session_id}/branches")
async def list_session_branches(session_id: str):
    return {"branches": [_session_summary(s) for s in chat_sessions.branches_of(session_id)]}


@router.get("/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    session = chat_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return {**_session_summary(session), "messages": session.messages}


@router.put("/chat/sessions/{session_id}")
async def update_chat_session(session_id: str, body: UpdateChatSessionBody):
    fields = body.model_dump(exclude_unset=True)
    if not chat_sessions.update(session_id, **fields):
        raise HTTPException(404, "session not found")
    return {"ok": True}


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    if not chat_sessions.delete(session_id):
        raise HTTPException(404, "session not found")
    return {"ok": True}


# ── usage / cost tracking ────────────────────────────────────────────

class QuickCompleteBody(BaseModel):
    endpoint_id: str
    model: str
    prompt: str


@router.post("/quick-complete")
async def quick_complete(body: QuickCompleteBody):
    """One-shot, non-streaming completion — used by the Notes "improve with
    AI" button and anywhere else that needs "send a prompt, get text back"
    without a client-side SSE reader (agent_loop.run_chat_collected already
    does exactly this internally; this just exposes it over HTTP)."""
    from agent_loop import run_chat_collected

    endpoint = endpoints.get(body.endpoint_id)
    if not endpoint:
        raise HTTPException(404, "endpoint not found")
    api_key = _api_key_for(endpoint.id, endpoint.provider)
    try:
        text = await run_chat_collected(endpoint.base_url, api_key, body.model,
                                          [{"role": "user", "content": body.prompt}])
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    usage.record(endpoint.provider, body.model, body.prompt, text)
    return {"text": text}


@router.get("/usage")
async def get_usage():
    return usage.summary()


# ── hardware-aware model suggestions ────────────────────────────────

@router.get("/model-capabilities")
async def get_model_capabilities(provider: str, model: str):
    return {"supports_tools": model_capabilities.supports_tools(provider, model)}


@router.get("/hardware")
async def get_hardware():
    return await hardware_probe.probe()


@router.post("/hardware/pull")
async def pull_model(body: dict):
    """Streams Ollama's own /api/pull progress straight through as SSE, one
    frame per line Ollama emits — same 'forward the upstream stream' shape
    chat_stream.py already uses for chat, just a different upstream
    endpoint. Requires Ollama actually running; a model already present is a
    fast no-op on Ollama's end, not an error here."""
    model = body.get("model")
    if not model:
        raise HTTPException(400, "model is required")
    ollama_url = await detect_ollama(config.ollama_base_url)
    if not ollama_url:
        raise HTTPException(502, "Ollama not detected — is it running?")

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10.0)) as client:
                async with client.stream("POST", ollama_url.rstrip("/") + "/api/pull",
                                           json={"model": model, "stream": True}) as r:
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        yield _sse_line(obj)
        except httpx.HTTPError as e:
            yield _sse_line({"error": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


# ── pending file edits (edit_file tool's approval mode) ─────────────

@router.get("/pending-edits")
async def list_pending_edits():
    return {"edits": pending_edits_store.list_pending()}


@router.post("/pending-edits/{edit_id}/approve")
async def approve_pending_edit(edit_id: str):
    edit = pending_edits_store.get(edit_id)
    if not edit:
        raise HTTPException(404, "no pending edit with that id (already approved/rejected, or the server restarted)")
    result = file_edit_tool.apply_edit(edit["path"], edit["content"])
    pending_edits_store.remove(edit_id)
    if not result["ok"]:
        raise HTTPException(500, result["error"])
    return {"ok": True, "path": edit["path"]}


@router.post("/pending-edits/{edit_id}/reject")
async def reject_pending_edit(edit_id: str):
    if not pending_edits_store.remove(edit_id):
        raise HTTPException(404, "no pending edit with that id")
    return {"ok": True}


# ── presets ──────────────────────────────────────────────────────────

class PresetBody(BaseModel):
    name: str
    system_prompt: str = ""
    endpoint_id: str = ""
    model: str = ""
    enabled_mcp_servers: list[str] = []
    enabled_builtin_tools: list[str] = []


# ── local image generation (ComfyUI) ────────────────────────────────

class ComfyUIConfigBody(BaseModel):
    base_url: str | None = None
    checkpoints_dir: str | None = None
    default_checkpoint: str | None = None
    default_negative_prompt: str | None = None


@router.get("/comfyui/config")
async def get_comfyui_config():
    return asdict_comfyui(comfyui_config.get())


@router.put("/comfyui/config")
async def update_comfyui_config(body: ComfyUIConfigBody):
    return asdict_comfyui(comfyui_config.update(**body.model_dump(exclude_unset=True)))


def asdict_comfyui(cfg) -> dict:
    return {"base_url": cfg.base_url, "checkpoints_dir": cfg.checkpoints_dir,
            "default_checkpoint": cfg.default_checkpoint, "default_negative_prompt": cfg.default_negative_prompt}


@router.get("/comfyui/status")
async def comfyui_status():
    cfg = comfyui_config.get()
    connected = await comfyui_client.detect(cfg.base_url)
    checkpoints = await comfyui_client.list_checkpoints(cfg.base_url) if connected else []
    return {"connected": connected, "checkpoints": checkpoints}


@router.post("/comfyui/pull-checkpoint")
async def pull_comfyui_checkpoint(body: dict):
    url = body.get("url")
    filename = body.get("filename")
    if not url or not filename:
        raise HTTPException(400, "url and filename are required")
    cfg = comfyui_config.get()
    if not cfg.checkpoints_dir:
        raise HTTPException(400, "set a checkpoints directory in ComfyUI settings first")

    async def stream():
        try:
            async for downloaded, total in comfyui_client.pull_checkpoint(url, cfg.checkpoints_dir, filename):
                yield _sse_line({"downloaded": downloaded, "total": total})
            yield _sse_line({"done": True})
        except (httpx.HTTPError, RuntimeError, OSError) as e:
            yield _sse_line({"error": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


# ── local audio generation (Piper) ──────────────────────────────────

class PiperConfigBody(BaseModel):
    voice_model_path: str | None = None
    voices_dir: str | None = None


@router.get("/piper/config")
async def get_piper_config():
    cfg = piper_config.get()
    return {"voice_model_path": cfg.voice_model_path, "voices_dir": cfg.voices_dir,
            "installed": piper_tts.is_installed()}


@router.put("/piper/config")
async def update_piper_config(body: PiperConfigBody):
    cfg = piper_config.update(**body.model_dump(exclude_unset=True))
    return {"voice_model_path": cfg.voice_model_path, "voices_dir": cfg.voices_dir}


@router.post("/piper/pull-voice")
async def pull_piper_voice(body: dict):
    voice_name = body.get("voice_name")
    quality = body.get("quality", "medium")
    if not voice_name:
        raise HTTPException(400, "voice_name is required, e.g. 'en_US-lessac'")
    cfg = piper_config.get()
    if not cfg.voices_dir:
        raise HTTPException(400, "set a voices directory in Piper settings first")

    async def stream():
        try:
            async for downloaded, total in piper_tts.pull_voice(voice_name, quality, cfg.voices_dir):
                yield _sse_line({"downloaded": downloaded, "total": total})
            onnx_path = piper_tts.voice_url(voice_name, quality)[0]
            filename = onnx_path.rsplit("/", 1)[-1]
            yield _sse_line({"done": True, "path": os.path.join(cfg.voices_dir, filename)})
        except (httpx.HTTPError, RuntimeError, OSError, ValueError) as e:
            yield _sse_line({"error": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


# ── custom ComfyUI workflows (video generation) ─────────────────────

class CustomWorkflowBody(BaseModel):
    name: str
    workflow: dict
    prompt_node_id: str
    prompt_input_key: str = "text"


@router.get("/custom-workflows")
async def list_custom_workflows():
    return {"workflows": [
        {"id": w.id, "name": w.name, "prompt_node_id": w.prompt_node_id, "prompt_input_key": w.prompt_input_key}
        for w in custom_workflows.list()
    ]}


@router.post("/custom-workflows")
async def create_custom_workflow(body: CustomWorkflowBody):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    if body.prompt_node_id not in body.workflow:
        raise HTTPException(400, f"workflow has no node id '{body.prompt_node_id}'")
    w = custom_workflows.add(name=body.name, workflow=body.workflow,
                               prompt_node_id=body.prompt_node_id, prompt_input_key=body.prompt_input_key)
    return {"id": w.id, "name": w.name}


@router.delete("/custom-workflows/{workflow_id}")
async def delete_custom_workflow(workflow_id: str):
    if not custom_workflows.delete(workflow_id):
        raise HTTPException(404, "workflow not found")
    return {"ok": True}


# ── generated files (image / audio / document generation) ──────────

@router.get("/generated")
async def list_generated_files():
    return {"files": [
        {"id": f.id, "kind": f.kind, "filename": f.filename, "content_type": f.content_type,
         "source": f.source, "created": f.created}
        for f in generated_files.list()
    ]}


@router.get("/generated/{file_id}")
async def get_generated_file(file_id: str):
    entry = generated_files.get(file_id)
    if not entry:
        raise HTTPException(404, "file not found")
    path = generated_files.path_for(entry)
    if not os.path.exists(path):
        raise HTTPException(404, "file data missing on disk")
    with open(path, "rb") as f:
        data = f.read()
    return StreamingResponse(
        iter([data]), media_type=entry.content_type,
        headers={"Content-Disposition": f'inline; filename="{entry.filename}"'},
    )


@router.delete("/generated/{file_id}")
async def delete_generated_file(file_id: str):
    if not generated_files.delete(file_id):
        raise HTTPException(404, "file not found")
    return {"ok": True}


@router.get("/presets")
async def list_presets():
    return {"presets": [
        {"id": p.id, "name": p.name, "system_prompt": p.system_prompt, "endpoint_id": p.endpoint_id,
         "model": p.model, "enabled_mcp_servers": p.enabled_mcp_servers,
         "enabled_builtin_tools": p.enabled_builtin_tools}
        for p in presets.list()
    ]}


@router.post("/presets")
async def create_preset(body: PresetBody):
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    preset = presets.add(**body.model_dump())
    return {"id": preset.id, "name": preset.name}


@router.delete("/presets/{preset_id}")
async def delete_preset(preset_id: str):
    if not presets.delete(preset_id):
        raise HTTPException(404, "preset not found")
    return {"ok": True}


# ── backup / restore ─────────────────────────────────────────────────
#
# A local zip of the whole data/ directory — the same thing the README
# already documents as a manual "copy the data folder" step, just done
# through the UI. No new trust boundary: whoever can reach these endpoints
# can already read/delete every file in data/ via the app's normal features
# (memory, documents, API keys' encrypted blobs, etc.).

@router.get("/backup")
async def backup_data():
    buf = io.BytesIO()
    data_dir = os.path.abspath(config.data_dir)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(data_dir):
            for name in files:
                full = os.path.join(root, name)
                zf.write(full, arcname=os.path.relpath(full, data_dir))
    buf.seek(0)
    from datetime import datetime, timezone
    filename = f"starfire-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/restore")
async def restore_data(file: UploadFile):
    raw = await file.read()
    data_dir = os.path.abspath(config.data_dir)
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "not a valid zip file")

    # Reject anything that would extract outside data_dir ("zip slip") before
    # writing a single file — a restore is already a full-overwrite action,
    # it must not also be a path-traversal one.
    for member in zf.namelist():
        target = os.path.abspath(os.path.join(data_dir, member))
        if not target.startswith(data_dir + os.sep) and target != data_dir:
            raise HTTPException(400, f"refusing to restore: unsafe path in archive ({member})")

    zf.extractall(data_dir)
    return {"ok": True}
