import asyncio
import fnmatch
import json
import logging
import os
import re
import tempfile
import zipfile
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

import auth_routes
import builtin_tools
import code_check
import date_parsing
import email_client
import file_edit_tool
import hardware_probe
import helper_tasks
import model_capabilities
import model_voices
import ollama_manager
import shell_tool
from agent_loop import run_chat_with_tools
from attachments import AttachmentError, AttachmentNoRoom, expand_attachments
from api_key_manager import APIKeyManager
from chat_stream import _sse_line, stream_chat
from config import config
from context_budget import trim_to_budget
import comfyui_client
import piper_tts
from local_gen_store import ComfyUIConfigStore, CustomWorkflowStore, PiperConfigStore
from email_rule_checker import EmailRuleChecker
from mcp_manager import McpConnectionError, mcp_manager
from mcp_servers_store import MCP_SERVER_REPOSITORY, REFERENCE_SERVERS, McpServerStore, has_missing_secrets
from model_endpoints import ModelEndpointStore, detect_ollama, discover_servers
from ollama_runner import for_runner
from providers import build_models_url, is_embedding_model
from task_scheduler import TaskScheduler, compute_next_run
from upload_limits import MIB, require_room, room_on_disk, too_large
from task_store import ScheduledTask
import user_data
from version import VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

api_keys = APIKeyManager(config.data_dir)
endpoints = ModelEndpointStore(config.data_dir)
mcp_servers = McpServerStore(config.data_dir)
comfyui_config = ComfyUIConfigStore(config.data_dir)
piper_config = PiperConfigStore(config.data_dir)
custom_workflows = CustomWorkflowStore(config.data_dir)

registry = user_data.configure(config.data_dir)
memory = user_data.PerUser("memory")
documents = user_data.PerUser("documents")
attachment_store = user_data.PerUser("attachment_store")
tasks = user_data.PerUser("tasks")
task_runs = user_data.PerUser("task_runs")
email_accounts = user_data.PerUser("email_accounts")
email_rules = user_data.PerUser("email_rules")
presets = user_data.PerUser("presets")
generated_files = user_data.PerUser("generated_files")
usage = user_data.PerUser("usage")
notes = user_data.PerUser("notes")
note_templates = user_data.PerUser("note_templates")
chat_sessions = user_data.PerUser("chat_sessions")
chat_folders = user_data.PerUser("chat_folders")
prompts = user_data.PerUser("prompts")

def _current_user_is_active_admin() -> bool:
    user_id = user_data.current_user_id.get()
    user = auth_routes.users.find_by_id(user_id) if user_id else None
    return bool(user) and user.role == "admin" and not user.disabled

def _tool_context(base_url: str = "", api_key: str | None = None, model: str = "", endpoint_id: str = "",
                   require_edit_approval: bool = False, require_shell_approval: bool = False,
                   interactive: bool = False) -> builtin_tools.ToolContext:
    return builtin_tools.ToolContext(
        memory=memory, documents=documents, tasks=tasks, task_runs=task_runs,
        email_accounts=email_accounts, api_keys=api_keys, notes=notes,
        endpoints=endpoints, generated_files=generated_files,
        comfyui_config=comfyui_config, piper_config=piper_config, custom_workflows=custom_workflows,
        base_url=base_url, api_key=api_key, model=model, endpoint_id=endpoint_id,
        require_edit_approval=require_edit_approval,
        require_shell_approval=require_shell_approval,
        may_use_host_tools=_current_user_is_active_admin(),
        events=asyncio.Queue() if interactive else None,
    )

_INLINE_MEMORY_RE = re.compile(r"^remember\s*:\s*(.+)$", re.IGNORECASE)

def _api_key_for(endpoint_id: str, provider: str) -> str | None:
    keys = api_keys.load()
    return keys.get(endpoint_id) or keys.get(provider) or None

class AddEndpointBody(BaseModel):
    base_url: str
    api_key: str | None = None
    kind: str = "api-key"
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
    if endpoints.find_by_url(body.base_url.strip()):
        raise HTTPException(409, "that endpoint is already configured")
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

@router.get("/models")
async def list_models():
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
                    if is_embedding_model(m.get("name")):
                        continue
                    result.append({"id": m.get("name"), "endpoint_id": e.id, "provider": "ollama",
                                    "size": m.get("size")})
            else:
                for m in data.get("data", []):
                    result.append({"id": m.get("id"), "endpoint_id": e.id, "provider": e.provider})
    return {"models": result}

def _brings_own_tools(model: str) -> bool:
    return any(fnmatch.fnmatchcase(model, pattern) for pattern in config.own_tools_patterns())


async def _warm_brain_model(endpoint, model: str) -> None:
    key = _api_key_for(endpoint.id, endpoint.provider)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(endpoint.base_url.rstrip("/") + "/warm", json={"model": model}, headers=headers)
            await r.aclose()
    except httpx.HTTPError as error:
        logging.getLogger(__name__).info("couldn't ask %s to warm %s: %s", endpoint.base_url, model, error)


@router.post("/warm")
async def warm(body: dict):
    endpoint_id = body.get("endpoint_id")
    model = body.get("model")
    endpoint = endpoints.get(endpoint_id) if endpoint_id else None
    if endpoint and endpoint.provider == "openai" and isinstance(model, str) and _brings_own_tools(model):
        await _warm_brain_model(endpoint, model)
        return {"ok": True}
    if not endpoint or endpoint.provider != "ollama" or not model:
        return {"ok": True}
    from providers import _ollama_api_root
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(_ollama_api_root(endpoint.base_url) + "/generate",
                                    json=for_runner({"model": model, "keep_alive": -1}))
            await r.aclose()
    except httpx.HTTPError as error:
        logger.info("couldn't ask %s to warm %s: %s", endpoint.base_url, model, error)
    return {"ok": True}

@router.get("/config")
async def get_config():
    detected = await detect_ollama(config.ollama_base_url)
    return {
        "ollama_base_url": detected or "http://localhost:11434",
        "version": VERSION,
        "own_tools_models": config.own_tools_patterns(),
        "inline_media_prefixes": config.media_prefixes(),
        "voice_models": [rule.pattern for rule in _voice_rules()],
    }


def _voice_rules() -> list[model_voices.VoiceRule]:
    try:
        return model_voices.parse_rules(config.model_voices)
    except model_voices.VoiceError as error:
        logging.getLogger(__name__).error("MODEL_VOICES is ignored until it is fixed: %s", error)
        return []


class SpeakBody(BaseModel):
    model: str
    text: str


@router.post("/speak")
async def speak(body: SpeakBody):
    rule = model_voices.rule_for(body.model, _voice_rules())
    if rule is None:
        raise HTTPException(404, "this model has no server voice")
    try:
        audio = await model_voices.speak(body.text, rule, config.voices_folder())
    except model_voices.VoiceError as error:
        raise HTTPException(400, str(error)) from error
    except model_voices.VoiceUnavailable as error:
        raise HTTPException(503, str(error)) from error
    return Response(audio, media_type="audio/wav")

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
    except PermissionError as error:
        logger.debug("cannot list folders in %s: %s", base, error)
    parent = os.path.dirname(base)
    return {"path": base, "parent": parent if parent != base else None, "entries": entries}

@router.get("/browse-dir")
async def browse_dir(path: str = ""):
    return _list_dirs(path)

class AddMcpServerBody(BaseModel):
    name: str | None = None
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    preset: str | None = None
    path: str | None = None
    transport: str = "stdio"
    url: str = ""
    headers: dict[str, str] = {}

@router.get("/mcp/repository")
async def list_mcp_repository():
    return {"repository": [
        {"id": rid, "name": p["name"], "needs_path": p.get("needs_path", False), "env_fields": p.get("env_fields", [])}
        for rid, p in MCP_SERVER_REPOSITORY.items()
    ]}

def _site_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" if parts.scheme and parts.hostname else ""

def _mcp_server_view(server, full: bool) -> dict:
    return {"id": server.id, "name": server.name, "enabled": server.enabled, "transport": server.transport,
            "command": server.command if full else "", "args": server.args if full else [],
            "url": server.url if full else _site_of(server.url),
            "connected": mcp_manager.is_connected(server.id), "tool_count": len(mcp_manager.list_tools(server.id)),
            "secrets_missing": has_missing_secrets(server)}

@router.get("/mcp/servers")
async def list_mcp_servers():
    full = _current_user_is_active_admin()
    return {"servers": [_mcp_server_view(s, full) for s in mcp_servers.list()],
            "presets": [{"id": pid, **{k: v for k, v in p.items() if k != "args"}} for pid, p in REFERENCE_SERVERS.items()]}

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
    elif body.transport in ("http", "sse"):
        url = body.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "the server URL must start with http:// or https://")
        name, command, args = body.name or url, "", []
    elif body.transport == "stdio":
        if not body.command:
            raise HTTPException(400, "command is required")
        name, command, args = body.name or body.command, body.command, body.args
    else:
        raise HTTPException(400, "transport must be stdio, http or sse")

    server = mcp_servers.add(name=name, command=command, args=args, env=body.env or None,
                             transport="stdio" if body.preset else body.transport,
                             url="" if body.preset else body.url.strip(),
                             headers=None if body.preset else (body.headers or None))
    try:
        tools = await mcp_manager.connect_server(server)
    except McpConnectionError as e:
        mcp_servers.delete(server.id)
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

@router.get("/documents")
async def list_documents():
    return {"documents": [
        {"id": d.id, "filename": d.filename, "added": d.added, "chunk_count": d.chunk_count}
        for d in documents.list()
    ]}

DOCUMENT_STORAGE_FACTOR = 2

@router.post("/documents")
async def add_document(file: UploadFile):
    limit = config.max_document_mb * MIB
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise too_large(file.filename or "that document", limit)
    require_room(config.data_dir, len(raw) * DOCUMENT_STORAGE_FACTOR, config.min_free_disk_mb, "that document")
    try:
        doc = documents.add(file.filename or "untitled", raw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": doc.id, "filename": doc.filename, "chunk_count": doc.chunk_count}

async def _upload_chunks(file: UploadFile):
    while chunk := await file.read(1024 * 1024):
        yield chunk

def _attachment_refusal(quota_left: int, disk_left: int) -> HTTPException:
    if disk_left <= quota_left:
        return HTTPException(507, "there isn't enough free disk space for that attachment - free some space and try again")
    return HTTPException(413, f"attachments for one account are limited to {config.max_attachments_mb} MB - "
                              "they expire after 30 days")

@router.post("/attachments")
async def add_attachment(file: UploadFile):
    quota_left = config.max_attachments_mb * MIB - attachment_store.used_bytes()
    disk_left = room_on_disk(config.data_dir, config.min_free_disk_mb)
    if min(quota_left, disk_left) <= 0:
        raise _attachment_refusal(quota_left, disk_left)
    try:
        attachment = await attachment_store.save(file.filename or "attachment", file.content_type or "",
                                                 _upload_chunks(file), room_bytes=min(quota_left, disk_left))
    except AttachmentNoRoom:
        raise _attachment_refusal(quota_left, disk_left)
    except AttachmentError as e:
        raise HTTPException(400, str(e))
    return attachment.public()

VIEWER_TYPES_THAT_BREAK_IN_A_SANDBOX = frozenset({"application/pdf"})

def _stored_file_headers(content_type: str) -> dict:
    headers = {"X-Content-Type-Options": "nosniff"}
    if content_type.lower() not in VIEWER_TYPES_THAT_BREAK_IN_A_SANDBOX:
        headers["Content-Security-Policy"] = "sandbox"
    return headers

@router.get("/attachments/{attachment_id}")
async def get_attachment(attachment_id: str):
    try:
        attachment = attachment_store.get(attachment_id)
    except AttachmentError as e:
        raise HTTPException(404, str(e))
    return FileResponse(attachment_store.data_path(attachment.id), media_type=attachment.mime,
                        filename=attachment.name, content_disposition_type="inline",
                        headers=_stored_file_headers(attachment.mime))

MAX_DOCUMENT_CONTEXT_CHARS = 60_000

@router.get("/documents/{document_id}/text")
async def get_document_text(document_id: str):
    found = documents.full_text(document_id)
    if not found:
        raise HTTPException(404, "document not found")
    filename, text = found
    truncated = len(text) > MAX_DOCUMENT_CONTEXT_CHARS
    return {"filename": filename, "text": text[:MAX_DOCUMENT_CONTEXT_CHARS], "truncated": truncated}

@router.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    if not documents.delete(document_id):
        raise HTTPException(404, "document not found")
    return {"ok": True}

class CreateTaskBody(BaseModel):
    name: str = ""
    prompt: str
    schedule: str
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
    ctx = _tool_context(base_url=endpoint.base_url, api_key=api_key, model=model, endpoint_id=endpoint.id)
    tier = model_capabilities.tool_tier(endpoint.provider, model)
    return run_chat_collected(
        endpoint.base_url, api_key, model, [{"role": "user", "content": prompt}],
        enabled_server_ids=set(enabled_mcp_servers),
        enabled_builtin_tools=model_capabilities.tools_for_tier(tier, set(enabled_builtin_tools)),
        ctx=ctx, max_tools=model_capabilities.tool_cap_for_tier(tier),
    )

scheduler = TaskScheduler(tasks, task_runs, _task_chat_fn, user_ids_fn=registry.user_ids)

def _email_rule_password(account_id: str) -> str | None:
    return api_keys.load().get(account_id)

async def _email_rule_chat_fn(endpoint_id: str, model: str, prompt: str) -> str:
    from agent_loop import run_chat_collected

    endpoint = endpoints.get(endpoint_id) if endpoint_id else None
    if not endpoint:
        raise RuntimeError("this rule's ai_summarize_note action has no valid endpoint configured")
    api_key = _api_key_for(endpoint.id, endpoint.provider)
    return await run_chat_collected(endpoint.base_url, api_key, model, [{"role": "user", "content": prompt}])

email_rule_checker = EmailRuleChecker(email_rules, email_accounts, _email_rule_password, _email_rule_chat_fn, notes,
                                      user_ids_fn=registry.user_ids)

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
    stored_keys = api_keys.load()
    return {"accounts": [
        {"id": a.id, "label": a.label, "email_address": a.email_address,
         "imap_host": a.imap_host, "imap_port": a.imap_port, "password_missing": not stored_keys.get(a.id)}
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
    ctx = _tool_context(base_url=endpoint.base_url, api_key=api_key, model=body.model, endpoint_id=endpoint.id)
    try:
        message = email_client.read_message(account, password, folder, uid, mark_seen=False)
        result = await builtin_tools.email_ai_action(action, message, body.instruction, ctx)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"result": result}

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

class ChatBody(BaseModel):
    endpoint_id: str
    model: str
    messages: list[dict]
    system: str | None = None
    options: dict | None = None
    enabled_mcp_servers: list[str] = []
    enabled_builtin_tools: list[str] = []
    require_edit_approval: bool = False
    require_shell_approval: bool = True
    memory_commands: bool = False

    @field_validator("messages")
    @classmethod
    def contents_are_text(cls, messages: list[dict]) -> list[dict]:
        for index, message in enumerate(messages):
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError(f"message {index}: content must be text")
        return messages

def _remember_command_text(body: ChatBody) -> str | None:
    if not body.memory_commands or not body.messages:
        return None
    last = body.messages[-1]
    content = last.get("content")
    if last.get("role") != "user" or not isinstance(content, str):
        return None
    match = _INLINE_MEMORY_RE.match(content.strip())
    return match.group(1).strip() if match else None

async def _track_usage(gen, provider: str, model: str, input_text: str):
    output_parts: list[str] = []
    async for chunk in gen:
        yield chunk
        try:
            line = chunk.decode("utf-8", errors="replace").strip()
            if line.startswith("data:"):
                obj = json.loads(line[len("data:"):].strip())
                if "delta" in obj:
                    output_parts.append(obj["delta"])
        except json.JSONDecodeError:
            logger.debug("a %s reply chunk was not JSON, so usage leaves it out", model)
    usage.record(provider, model, input_text, "".join(output_parts))

async def _synthetic_reply(text: str):
    yield _sse_line({"delta": text})
    yield _sse_line({"done": True})

@router.post("/chat_stream")
async def chat_stream_endpoint(body: ChatBody):
    remembered = _remember_command_text(body)
    if remembered:
        entry = memory.add(remembered, source="inline_command")
        return StreamingResponse(_synthetic_reply(f"Saved to memory: “{entry.text}”"),
                                 media_type="text/event-stream")

    endpoint = endpoints.get(body.endpoint_id)
    if not endpoint:
        raise HTTPException(404, "endpoint not found")
    api_key = _api_key_for(endpoint.id, endpoint.provider)

    DEFAULT_CONTEXT_BUDGET_TOKENS = 8192
    num_ctx = (body.options or {}).get("num_ctx", DEFAULT_CONTEXT_BUDGET_TOKENS)
    messages = trim_to_budget(body.messages, num_ctx)
    input_text = "\n".join(m.get("content") or "" for m in messages)
    try:
        messages = await expand_attachments(messages, attachment_store, endpoint.base_url, api_key,
                                            endpoint.label or endpoint.base_url)
    except AttachmentError as e:
        raise HTTPException(400, str(e))

    tools_requested = bool(body.enabled_mcp_servers or body.enabled_builtin_tools)
    tools_usable = tools_requested and model_capabilities.supports_tools(endpoint.provider, body.model)
    if tools_usable:
        for server_id in body.enabled_mcp_servers:
            if mcp_manager.is_connected(server_id):
                continue
            server = mcp_servers.get(server_id)
            if not server or not server.enabled:
                continue
            try:
                await mcp_manager.connect_server(server)
            except McpConnectionError as e:
                logger.warning("could not reconnect MCP server %s: %s", server_id, e)
        ctx = _tool_context(base_url=endpoint.base_url, api_key=api_key, model=body.model, endpoint_id=endpoint.id,
                            require_edit_approval=body.require_edit_approval,
                            require_shell_approval=body.require_shell_approval, interactive=True)
        tier = model_capabilities.tool_tier(endpoint.provider, body.model)
        generator = run_chat_with_tools(endpoint.base_url, api_key, body.model, messages,
                                          body.system, body.options, set(body.enabled_mcp_servers),
                                          model_capabilities.tools_for_tier(tier, set(body.enabled_builtin_tools)), ctx,
                                          max_tools=model_capabilities.tool_cap_for_tier(tier))
    else:
        generator = stream_chat(endpoint.base_url, api_key, body.model, messages, body.system, body.options)

    generator = _track_usage(generator, endpoint.provider, body.model, input_text)

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )

class CreateChatSessionBody(BaseModel):
    title: str = "New chat"
    messages: list[dict] = []
    parent_session_id: str = ""
    branch_point: int = -1

MAX_SYSTEM_PROMPT_CHARS = 32000
MAX_CHAT_PARAMS = 16
MAX_CHAT_PARAM_NAME_CHARS = 40

ChatParamName = Annotated[str, Field(min_length=1, max_length=MAX_CHAT_PARAM_NAME_CHARS)]

class UpdateChatSessionBody(BaseModel):
    title: str | None = None
    messages: list[dict] | None = None
    endpoint_id: str | None = None
    model: str | None = None
    pinned: bool | None = None
    folder_id: str | None = None
    archived: bool | None = None
    tags: list[str] | None = None
    system_prompt: str | None = Field(default=None, max_length=MAX_SYSTEM_PROMPT_CHARS)
    params: dict[ChatParamName, float] | None = Field(default=None, max_length=MAX_CHAT_PARAMS)

def _session_detail(s) -> dict:
    return {**_session_summary(s), "messages": s.messages, "system_prompt": s.system_prompt, "params": s.params}

def _imported_controls(item: dict) -> dict:
    controls = {}
    prompt = item.get("system_prompt")
    if isinstance(prompt, str):
        controls["system_prompt"] = prompt[:MAX_SYSTEM_PROMPT_CHARS]
    params = item.get("params")
    if isinstance(params, dict):
        numeric = {name: value for name, value in params.items()
                   if isinstance(name, str) and 0 < len(name) <= MAX_CHAT_PARAM_NAME_CHARS
                   and isinstance(value, (int, float)) and not isinstance(value, bool)}
        controls["params"] = dict(list(numeric.items())[:MAX_CHAT_PARAMS])
    return controls

class ImportChatsBody(BaseModel):
    chats: list[dict]

MAX_IMPORT_CHATS = 5000

def _session_summary(s) -> dict:
    return {"id": s.id, "title": s.title, "endpoint_id": s.endpoint_id, "model": s.model,
            "pinned": s.pinned, "parent_session_id": s.parent_session_id, "branch_point": s.branch_point,
            "folder_id": s.folder_id, "archived": s.archived, "tags": s.tags,
            "created": s.created, "updated": s.updated, "message_count": len(s.messages)}

def _iso_timestamp(value) -> str:
    from datetime import datetime, timezone
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return value if isinstance(value, str) else ""

def _imported_chat(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    inner = item.get("chat") if isinstance(item.get("chat"), dict) else item
    raw_messages = inner.get("messages")
    if not isinstance(raw_messages, list):
        return None
    messages = [
        {"role": m["role"], "content": m["content"], **({"model": m["model"]} if isinstance(m.get("model"), str) else {})}
        for m in raw_messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
    ]
    if not messages:
        return None
    raw_tags = item.get("tags") or inner.get("tags") or []
    tags = [t.get("name", "") if isinstance(t, dict) else str(t) for t in raw_tags if isinstance(t, (dict, str))]
    imported = {
        "title": str(item.get("title") or inner.get("title") or "Imported chat")[:120],
        "messages": messages, "pinned": bool(item.get("pinned")), "archived": bool(item.get("archived")),
        "tags": tags, "model": str(item.get("model") or ""),
        **_imported_controls(item),
    }
    for key, source in (("created", "created"), ("updated", "updated"), ("created", "created_at"), ("updated", "updated_at")):
        stamp = _iso_timestamp(item.get(source))
        if stamp:
            imported[key] = stamp
    return imported

@router.get("/chat/sessions")
async def list_chat_sessions(q: str = "", archived: bool = False):
    return {"sessions": [_session_summary(s) for s in chat_sessions.list(query=q, archived=archived)]}

@router.post("/chat/sessions")
async def create_chat_session(body: CreateChatSessionBody):
    session = chat_sessions.add(title=body.title, messages=body.messages,
                                  parent_session_id=body.parent_session_id, branch_point=body.branch_point)
    return {**_session_summary(session), "messages": session.messages}

@router.get("/chat/sessions/export")
async def export_chat_sessions():
    from dataclasses import asdict
    from datetime import datetime, timezone
    body = json.dumps([asdict(s) for s in chat_sessions.all()], indent=2)
    filename = f"starfire-chats-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    return StreamingResponse(iter([body.encode()]), media_type="application/json",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})

@router.post("/chat/sessions/import")
async def import_chat_sessions(body: ImportChatsBody):
    if len(body.chats) > MAX_IMPORT_CHATS:
        raise HTTPException(400, f"too many chats in one import (max {MAX_IMPORT_CHATS})")
    readable = [chat for chat in (_imported_chat(item) for item in body.chats) if chat]
    if not readable:
        raise HTTPException(400, "no chats with user/assistant messages found in that file")
    chat_sessions.add_many(readable)
    return {"imported": len(readable), "skipped": len(body.chats) - len(readable)}

@router.post("/chat/sessions/archive-all")
async def archive_all_chat_sessions():
    return {"archived": chat_sessions.set_archived_all(True)}

@router.post("/chat/sessions/unarchive-all")
async def unarchive_all_chat_sessions():
    return {"unarchived": chat_sessions.set_archived_all(False)}

@router.delete("/chat/sessions")
async def delete_all_chat_sessions(confirm: str = ""):
    if confirm != "delete-all":
        raise HTTPException(400, "pass confirm=delete-all to delete every chat")
    return {"deleted": chat_sessions.delete_all()}

@router.get("/chat/tags")
async def list_chat_tags():
    return {"tags": [{"name": name, "count": count} for name, count in chat_sessions.tag_counts().items()]}

@router.get("/chat/sessions/{session_id}/branches")
async def list_session_branches(session_id: str):
    return {"branches": [_session_summary(s) for s in chat_sessions.branches_of(session_id)]}

@router.post("/chat/sessions/{session_id}/clone")
async def clone_chat_session(session_id: str):
    session = chat_sessions.clone(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return _session_summary(session)

@router.get("/chat/sessions/{session_id}")
async def get_chat_session(session_id: str):
    session = chat_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return _session_detail(session)

@router.put("/chat/sessions/{session_id}")
async def update_chat_session(session_id: str, body: UpdateChatSessionBody):
    fields = body.model_dump(exclude_unset=True)
    if fields.get("folder_id") and not chat_folders.get(fields["folder_id"]):
        raise HTTPException(404, "folder not found")
    if not chat_sessions.update(session_id, **fields):
        raise HTTPException(404, "session not found")
    return {"ok": True}

@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    if not chat_sessions.delete(session_id):
        raise HTTPException(404, "session not found")
    return {"ok": True}

class FolderBody(BaseModel):
    name: str

@router.get("/folders")
async def list_folders():
    return {"folders": [{"id": f.id, "name": f.name, "created": f.created} for f in chat_folders.list()]}

@router.post("/folders")
async def create_folder(body: FolderBody):
    try:
        folder = chat_folders.add(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": folder.id, "name": folder.name}

@router.put("/folders/{folder_id}")
async def rename_folder(folder_id: str, body: FolderBody):
    try:
        renamed = chat_folders.rename(folder_id, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not renamed:
        raise HTTPException(404, "folder not found")
    return {"ok": True}

@router.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str):
    if not chat_folders.delete(folder_id):
        raise HTTPException(404, "folder not found")
    chat_sessions.clear_folder(folder_id)
    return {"ok": True}

class PromptBody(BaseModel):
    command: str
    title: str = ""
    content: str

@router.get("/prompts")
async def list_prompts():
    return {"prompts": [{"id": p.id, "command": p.command, "title": p.title, "content": p.content}
                        for p in prompts.list()]}

@router.post("/prompts")
async def create_prompt(body: PromptBody):
    try:
        prompt = prompts.add(body.command, body.title, body.content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": prompt.id, "command": prompt.command}

@router.put("/prompts/{prompt_id}")
async def update_prompt(prompt_id: str, body: PromptBody):
    try:
        updated = prompts.update(prompt_id, body.command, body.title, body.content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not updated:
        raise HTTPException(404, "prompt not found")
    return {"ok": True}

@router.delete("/prompts/{prompt_id}")
async def delete_prompt(prompt_id: str):
    if not prompts.delete(prompt_id):
        raise HTTPException(404, "prompt not found")
    return {"ok": True}

class QuickCompleteBody(BaseModel):
    endpoint_id: str
    model: str
    prompt: str
    helper: bool = False

def _quick_target(body: QuickCompleteBody):
    helper_endpoint = helper_tasks.ollama_endpoint(endpoints.list()) if body.helper and config.task_model else None
    if helper_endpoint:
        return (helper_endpoint, config.task_model, helper_tasks.helper_options(),
                helper_tasks.helper_think(config.task_model))
    endpoint = endpoints.get(body.endpoint_id)
    if not endpoint:
        raise HTTPException(404, "endpoint not found")
    return endpoint, body.model, None, None

@router.post("/quick-complete")
async def quick_complete(body: QuickCompleteBody):
    from agent_loop import run_chat_collected

    endpoint, model, options, think = _quick_target(body)
    api_key = _api_key_for(endpoint.id, endpoint.provider)
    try:
        text = await run_chat_collected(endpoint.base_url, api_key, model,
                                          [{"role": "user", "content": body.prompt}], options=options, think=think)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    text = helper_tasks.clean_reply(text)
    usage.record(endpoint.provider, model, body.prompt, text)
    return {"text": text}

class CodeCheckBody(BaseModel):
    language: str = ""
    code: str

@router.post("/code-check")
async def check_code_block(body: CodeCheckBody):
    return await code_check.check(body.language, body.code)

@router.get("/usage")
async def get_usage():
    return usage.summary()

@router.get("/model-capabilities")
async def get_model_capabilities(provider: str, model: str):
    tier = model_capabilities.tool_tier(provider, model)
    return {
        "supports_tools": model_capabilities.supports_tools(provider, model),
        "tool_tier": tier,
        "tier_description": model_capabilities.tier_description(tier),
        "basic_tools": model_capabilities.BASIC_TIER_TOOLS,
    }

@router.get("/hardware")
async def get_hardware():
    return await hardware_probe.probe()

@router.get("/hardware/ollama")
async def get_ollama_status():
    installed = ollama_manager.is_installed()
    running = bool(await detect_ollama(config.ollama_base_url)) if installed else False
    return {
        "installed": installed,
        "running": running,
        "install": None if installed else ollama_manager.install_info(),
    }

@router.post("/hardware/ollama/start")
async def start_ollama():
    ok, message = await ollama_manager.start(config.ollama_base_url)
    if not ok:
        raise HTTPException(502, message)
    return {"started": True, "message": message}

@router.post("/hardware/pull")
async def pull_model(body: dict):
    model = body.get("model")
    if not model:
        raise HTTPException(400, "model is required")
    ollama_url = await detect_ollama(config.ollama_base_url)
    if not ollama_url:
        raise HTTPException(502, "Ollama not detected - is it running?")

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

@router.get("/pending-shell")
async def list_pending_shell():
    return {"commands": shell_tool.list_pending(user_data.current_user_id.get())}

def _require_host_tool_access(action: str) -> None:
    if not _current_user_is_active_admin():
        raise HTTPException(403, f"only an admin can {action}")

@router.post("/pending-shell/{pending_id}/approve")
async def approve_pending_shell(pending_id: str):
    _require_host_tool_access("approve shell commands")
    if not shell_tool.resolve(pending_id, True, user_data.current_user_id.get()):
        raise HTTPException(404, "no command is waiting with that id (already decided, timed out, or the server restarted)")
    return {"approved": True}

@router.post("/pending-shell/{pending_id}/reject")
async def reject_pending_shell(pending_id: str):
    if not shell_tool.resolve(pending_id, False, user_data.current_user_id.get()):
        raise HTTPException(404, "no command is waiting with that id")
    return {"rejected": True}

@router.get("/pending-edits")
async def list_pending_edits():
    return {"edits": file_edit_tool.list_pending(user_data.current_user_id.get())}

@router.post("/pending-edits/{edit_id}/approve")
async def approve_pending_edit(edit_id: str):
    _require_host_tool_access("approve file edits")
    owner = user_data.current_user_id.get()
    edit = file_edit_tool.get_pending(edit_id, owner)
    if not edit:
        raise HTTPException(404, "no pending edit with that id (already approved/rejected, or the server restarted)")
    result = file_edit_tool.apply_edit(edit["path"], edit["content"])
    file_edit_tool.remove_pending(edit_id, owner)
    if not result["ok"]:
        raise HTTPException(500, result["error"])
    return {"ok": True, "path": edit["path"]}

@router.post("/pending-edits/{edit_id}/reject")
async def reject_pending_edit(edit_id: str):
    if not file_edit_tool.remove_pending(edit_id, user_data.current_user_id.get()):
        raise HTTPException(404, "no pending edit with that id")
    return {"ok": True}

class PresetBody(BaseModel):
    name: str
    system_prompt: str = ""
    endpoint_id: str = ""
    model: str = ""
    enabled_mcp_servers: list[str] = []
    enabled_builtin_tools: list[str] = []

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
    return FileResponse(path, media_type=entry.content_type, filename=entry.filename,
                        content_disposition_type="inline", headers=_stored_file_headers(entry.content_type))

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

def _folder_bytes(folder: str) -> int:
    return sum(os.path.getsize(os.path.join(root, name)) for root, _dirs, files in os.walk(folder) for name in files)

def _chunks_then_close(handle):
    try:
        while chunk := handle.read(MIB):
            yield chunk
    finally:
        handle.close()

MCP_SERVERS_FILE = "mcp_servers.json"
CREDENTIAL_FILE_OR_COPY = re.compile(
    r"\.?(?:\.key|\.env|sessions\.json|api_keys\.json|mcp_servers\.json)(?:[.~_-].*)?", re.S)

DOT_SEGMENTS = frozenset({os.curdir, os.pardir})
ARCHIVE_SEPARATORS = re.compile(r"[\\/]")

def _extracted_name(member: str) -> str:
    arcname = member.replace("/", os.sep)
    if os.altsep:
        arcname = arcname.replace(os.altsep, os.sep)
    arcname = os.path.splitdrive(arcname)[1]
    return os.sep.join(part for part in arcname.split(os.sep) if part and part not in DOT_SEGMENTS)

def _is_unsafe_member(member: str) -> bool:
    is_absolute = member.startswith(("/", "\\")) or bool(os.path.splitdrive(member)[0])
    return is_absolute or any(part in DOT_SEGMENTS for part in ARCHIVE_SEPARATORS.split(member))

def _holds_credentials(relative_path: str) -> bool:
    extracted = _extracted_name(relative_path)
    if extracted == MCP_SERVERS_FILE:
        return False
    return bool(CREDENTIAL_FILE_OR_COPY.fullmatch(os.path.basename(extracted).lower()))

def _mcp_servers_without_secrets(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("left %s out of the backup because it could not be read: %s", path, error)
        return None
    if not isinstance(items, list):
        logger.warning("left %s out of the backup because it holds a %s, not a list", path, type(items).__name__)
        return None
    return json.dumps(McpServerStore(os.path.dirname(path)).without_secret_values(items), indent=2)

def _add_to_backup(zf: zipfile.ZipFile, full_path: str, relative_path: str) -> None:
    if _holds_credentials(relative_path):
        return
    if relative_path == MCP_SERVERS_FILE:
        blanked = _mcp_servers_without_secrets(full_path)
        if blanked is not None:
            zf.writestr(relative_path, blanked)
        return
    zf.write(full_path, arcname=relative_path)

@router.get("/backup")
async def backup_data():
    data_dir = os.path.abspath(config.data_dir)
    require_room(data_dir, _folder_bytes(data_dir), config.min_free_disk_mb, "a backup")
    spooled = tempfile.TemporaryFile(dir=data_dir)
    with zipfile.ZipFile(spooled, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(data_dir):
            for name in files:
                full = os.path.join(root, name)
                _add_to_backup(zf, full, os.path.relpath(full, data_dir))
    spooled.seek(0)
    from datetime import datetime, timezone
    filename = f"starfire-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        _chunks_then_close(spooled), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

async def _spool_capped(file: UploadFile, spooled, limit: int) -> None:
    size = 0
    while chunk := await file.read(MIB):
        size += len(chunk)
        if size > limit:
            raise too_large("that backup", limit)
        spooled.write(chunk)
    spooled.seek(0)

@router.post("/restore")
async def restore_data(file: UploadFile):
    data_dir = os.path.abspath(config.data_dir)
    with tempfile.TemporaryFile(dir=data_dir) as spooled:
        await _spool_capped(file, spooled, config.max_restore_mb * MIB)
        try:
            zf = zipfile.ZipFile(spooled)
        except zipfile.BadZipFile:
            raise HTTPException(400, "not a valid zip file")
        with zf:
            for member in zf.namelist():
                target = os.path.abspath(os.path.join(data_dir, member))
                escapes = not target.startswith(data_dir + os.sep) and target != data_dir
                if escapes or _is_unsafe_member(member):
                    raise HTTPException(400, f"refusing to restore: unsafe path in archive ({member})")
            kept = [info for info in zf.infolist() if not _holds_credentials(info.filename)]
            skipped = sorted(info.filename for info in zf.infolist() if _holds_credentials(info.filename))
            unpacked = sum(info.file_size for info in kept)
            require_room(data_dir, unpacked, config.min_free_disk_mb, "that backup")
            zf.extractall(data_dir, members=kept)
    if skipped:
        logger.info("restore kept this install's keys and sessions and left out these files from the backup: %s",
                    ", ".join(skipped))
    return {"ok": True}
