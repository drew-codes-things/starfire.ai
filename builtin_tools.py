"""Native (non-MCP) tools the agent loop can call: memory, document search,
scheduled tasks, and email. These are first-party, in-process Python
functions — running them as separate MCP stdio subprocesses (odysseus's
approach for its bundled email/memory/image_gen/rag servers) would be pure
overhead for something only starfire itself ever calls; odysseus does it
partly so the same servers are reusable from other MCP clients, which
doesn't apply here.

Schema shape matches what agent_loop.py already expects from MCP servers
(OpenAI-style {type:'function', function:{name, description, parameters}}),
so all tool sources merge into one list with no special-casing beyond
dispatch-by-name in call_builtin_tool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import audio_generation
import comfyui_client
import date_parsing
import deep_research
import document_generation
import email_client
import file_edit_tool  # also carries the pending-edit queue (stage/get_pending/...)
import github_tool
import image_generation
import piper_tts
import shell_tool
import web_search
from api_key_manager import APIKeyManager
from documents_store import DocumentStore
from email_store import EmailAccountStore
from local_gen_store import (
    DEFAULT_COMFYUI_BASE_URL as COMFYUI_DEFAULT_BASE_URL,
    ComfyUIConfigStore,
    CustomWorkflowStore,
    GeneratedFileStore,
    PiperConfigStore,
)
from model_endpoints import ModelEndpointStore
from memory_store import VALID_CATEGORIES, MemoryStore
from note_store import VALID_NOTE_TYPES, VALID_REPEATS, NoteStore
from task_store import ScheduledTask, TaskRunStore, TaskStore

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "manage_memory",
            "description": "Manage the user's persistent memory: list, add, edit, delete, or search remembered facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "edit", "delete", "search"]},
                    "text": {"type": "string", "description": "the fact text, for add/edit"},
                    "memory_id": {"type": "string", "description": "target id, for edit/delete"},
                    "category": {"type": "string", "enum": sorted(VALID_CATEGORIES)},
                    "query": {"type": "string", "description": "search query, for action=search"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "Search the user's uploaded documents for relevant passages.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_tasks",
            "description": "Manage scheduled tasks that send a prompt to the model on a schedule (once/daily/weekly/cron).",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "create", "pause", "resume", "delete", "run_now"]},
                    "name": {"type": "string", "description": "task name, for create"},
                    "prompt": {"type": "string", "description": "the prompt to send when the task runs, for create"},
                    "schedule": {"type": "string", "enum": ["once", "daily", "weekly", "cron"], "description": "for create"},
                    "scheduled_time": {"type": "string", "description": "\"HH:MM\", for once/daily/weekly"},
                    "scheduled_day": {"type": "string", "description": "weekday name, for weekly"},
                    "cron_expression": {"type": "string", "description": "for schedule=cron"},
                    "task_id": {"type": "string", "description": "target id, for pause/resume/delete/run_now"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_email",
            "description": "Read and send email: list accounts, list/search/read messages, send, reply, mark read, archive, delete, or get an AI summary/urgency check/draft reply for a message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": [
                        "list_accounts", "list_messages", "read_message", "search", "send", "reply",
                        "mark_read", "archive", "delete", "summarize", "draft_reply", "check_urgency",
                    ]},
                    "account_id": {"type": "string"},
                    "folder": {"type": "string", "description": "defaults to INBOX"},
                    "uid": {"type": "string", "description": "message id, for read_message/reply/mark_read/archive/delete/summarize/draft_reply/check_urgency"},
                    "query": {"type": "string", "description": "for action=search"},
                    "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
                    "instruction": {"type": "string", "description": "how to draft the reply, for action=draft_reply"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_notes",
            "description": "Manage notes and checklists: list, view, add, update, delete, toggle_item. "
                            "For a to-do list, set note_type='checklist' and pass items as the "
                            "checklist_items array. due_date accepts natural language (\"tomorrow\", "
                            "\"next friday\", \"in 3 days\", optionally with \"at 9am\") or an ISO "
                            "date/datetime string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "view", "add", "update", "delete", "toggle_item"]},
                    "note_id": {"type": "string"},
                    "title": {"type": "string"}, "content": {"type": "string"},
                    "note_type": {"type": "string", "enum": sorted(VALID_NOTE_TYPES)},
                    "checklist_items": {"type": "array", "items": {"type": "object", "properties": {
                        "text": {"type": "string"}, "done": {"type": "boolean"}}}},
                    "due_date": {"type": "string"}, "repeat": {"type": "string", "enum": sorted(VALID_REPEATS)},
                    "label": {"type": "string"}, "color": {"type": "string"},
                    "item_index": {"type": "integer", "description": "for action=toggle_item"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web and return a short list of results (title, url, snippet).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_cli",
            "description": "Run a GitHub CLI ('gh') command, e.g. args=['issue','list'] or "
                            "args=['pr','create','--title','...','--body','...']. Only the gh "
                            "binary can be run through this tool — not arbitrary shell.",
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {"type": "array", "items": {"type": "string"},
                              "description": "gh CLI arguments, e.g. [\"issue\", \"list\"]"},
                },
                "required": ["args"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": "Research a question: search the web, read the top sources, and "
                            "return a synthesized, cited report. Slower and more thorough than "
                            "search_web — use it for open-ended research questions, not quick lookups.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image from a text prompt and show it in the chat. Uses local "
                            "ComfyUI if configured (no API key, no content restrictions beyond whatever "
                            "checkpoint is loaded); otherwise falls back to OpenAI/DALL-E, which does "
                            "enforce its own content policy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "negative_prompt": {"type": "string", "description": "ComfyUI only — what to avoid"},
                    "quality": {"type": "string", "enum": sorted(comfyui_client.QUALITY_PRESETS),
                                 "description": "ComfyUI only — trades speed for resolution/steps"},
                    "size": {"type": "string", "enum": ["1024x1024", "1792x1024", "1024x1792"],
                              "description": "OpenAI fallback only"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_audio",
            "description": "Generate spoken audio from text and show a player in the chat. Uses local "
                            "Piper if a voice is configured (no API key); otherwise falls back to OpenAI TTS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string", "enum": sorted(audio_generation.VALID_VOICES),
                               "description": "OpenAI fallback only"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Generate a video (or video+audio) using a custom ComfyUI workflow you've "
                            "saved in Settings. Requires at least one saved workflow — there's no "
                            "built-in default the way images have, since video workflows vary by which "
                            "model/nodes you have installed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["workflow_name", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_document",
            "description": "Turn text/markdown content into a downloadable file (.md, .txt, .pdf, "
                            "or .docx) and show a download link in the chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "format": {"type": "string", "enum": sorted(document_generation.VALID_FORMATS)},
                    "filename": {"type": "string", "description": "without extension, optional"},
                },
                "required": ["content", "format"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Write a file's full new content and get a diff of the change. If the "
                            "user has approval mode on, the edit is staged for the user to "
                            "approve/reject in the chat UI rather than written immediately — the "
                            "tool result will say which happened.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "the file's complete new content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command on this machine and return its output. No sandbox — "
                            "runs as the local user with only a timeout and output-size limit. Only "
                            "available when explicitly enabled.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "description": "working directory, optional"},
                },
                "required": ["command"],
            },
        },
    },
]

_TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}


@dataclass
class ToolContext:
    """Everything call_builtin_tool needs beyond the tool name/arguments.
    Bundled into one object because manage_tasks/manage_email need access to
    several stores plus the current conversation's chat endpoint (for
    creating tasks with sensible defaults, and for the email AI-extras
    actions, which run one direct chat call against whatever endpoint the
    calling conversation is already using)."""
    memory: MemoryStore
    documents: DocumentStore
    tasks: TaskStore
    task_runs: TaskRunStore
    email_accounts: EmailAccountStore
    api_keys: APIKeyManager
    notes: NoteStore
    endpoints: ModelEndpointStore = None
    generated_files: GeneratedFileStore = None
    comfyui_config: ComfyUIConfigStore = None
    piper_config: PiperConfigStore = None
    custom_workflows: CustomWorkflowStore = None
    base_url: str = ""
    api_key: str | None = None
    model: str = ""
    require_edit_approval: bool = False


def schemas_for(enabled: set[str]) -> list[dict]:
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in enabled]


def is_builtin_tool(name: str) -> bool:
    return name in _TOOL_NAMES


async def call_builtin_tool(name: str, arguments: dict, ctx: ToolContext) -> str:
    if name == "manage_memory":
        return await _manage_memory(arguments, ctx.memory)
    if name == "search_documents":
        return await _search_documents(arguments, ctx.documents)
    if name == "manage_tasks":
        return await _manage_tasks(arguments, ctx)
    if name == "manage_email":
        return await _manage_email(arguments, ctx)
    if name == "manage_notes":
        return _manage_notes(arguments, ctx.notes)
    if name == "search_web":
        return await _search_web(arguments)
    if name == "github_cli":
        return await github_tool.run_gh(arguments.get("args") or [])
    if name == "deep_research":
        return await _deep_research(arguments, ctx)
    if name == "generate_image":
        return await _generate_image(arguments, ctx)
    if name == "generate_audio":
        return await _generate_audio(arguments, ctx)
    if name == "generate_video":
        return await _generate_video(arguments, ctx)
    if name == "generate_document":
        return _generate_document(arguments, ctx)
    if name == "edit_file":
        return _edit_file(arguments, ctx)
    if name == "run_shell":
        return await shell_tool.run_shell(arguments.get("command", ""), arguments.get("cwd"))
    raise ValueError(f"unknown builtin tool: {name}")


def _find_openai_endpoint(ctx: ToolContext) -> tuple[str, str] | None:
    """Image/audio generation need the real OpenAI API specifically (DALL-E
    and TTS are OpenAI-proprietary endpoints, not something generic
    OpenAI-compatible servers usually implement) — checked by hostname
    rather than trusting providers.py's "openai" provider label, since that
    label is also the catch-all for Groq/OpenRouter/local OpenAI-compatible
    servers, none of which serve these two endpoints."""
    if not ctx.endpoints:
        return None
    keys = ctx.api_keys.load()
    for endpoint in ctx.endpoints.list():
        if "api.openai.com" in endpoint.base_url:
            api_key = keys.get(endpoint.id) or keys.get("openai")
            if api_key:
                return endpoint.base_url.rstrip("/"), api_key
    return None


async def _generate_image(args: dict, ctx: ToolContext) -> str:
    prompt = args.get("prompt", "")
    if not prompt:
        return "error: prompt is required"

    comfy_cfg = ctx.comfyui_config.get() if ctx.comfyui_config else None
    if comfy_cfg and comfy_cfg.default_checkpoint and await comfyui_client.detect(comfy_cfg.base_url):
        preset = comfyui_client.QUALITY_PRESETS.get(args.get("quality", "medium"), comfyui_client.QUALITY_PRESETS["medium"])
        try:
            data = await comfyui_client.generate(
                prompt, comfy_cfg.base_url, comfy_cfg.default_checkpoint,
                negative_prompt=args.get("negative_prompt", comfy_cfg.default_negative_prompt), **preset,
            )
        except RuntimeError as e:
            return f"error: {e}"
        entry = ctx.generated_files.add("image", "generated.png", "image/png", data, source=prompt)
        return json.dumps({"kind": "image", "id": entry.id, "url": f"/api/generated/{entry.id}", "prompt": prompt})

    found = _find_openai_endpoint(ctx)
    if not found:
        return ("error: no local ComfyUI configured (Settings → Local Generation) and no OpenAI endpoint "
                "with an API key configured either — set up at least one")
    base_url, api_key = found
    try:
        data = await image_generation.generate(prompt, base_url, api_key, args.get("size", "1024x1024"))
    except RuntimeError as e:
        return f"error: {e}"
    entry = ctx.generated_files.add("image", "generated.png", "image/png", data, source=prompt)
    return json.dumps({"kind": "image", "id": entry.id, "url": f"/api/generated/{entry.id}", "prompt": prompt})


async def _generate_audio(args: dict, ctx: ToolContext) -> str:
    text = args.get("text", "")
    if not text:
        return "error: text is required"

    piper_cfg = ctx.piper_config.get() if ctx.piper_config else None
    if piper_cfg and piper_cfg.voice_model_path and piper_tts.is_installed():
        try:
            data = await piper_tts.generate(text, piper_cfg.voice_model_path)
        except RuntimeError as e:
            return f"error: {e}"
        entry = ctx.generated_files.add("audio", "generated.wav", "audio/wav", data, source=text[:200])
        return json.dumps({"kind": "audio", "id": entry.id, "url": f"/api/generated/{entry.id}"})

    found = _find_openai_endpoint(ctx)
    if not found:
        return ("error: no local Piper voice configured (Settings → Local Generation) and no OpenAI "
                "endpoint with an API key configured either — set up at least one")
    base_url, api_key = found
    try:
        data = await audio_generation.generate(text, base_url, api_key, args.get("voice", "alloy"))
    except RuntimeError as e:
        return f"error: {e}"
    entry = ctx.generated_files.add("audio", "generated.mp3", "audio/mpeg", data, source=text[:200])
    return json.dumps({"kind": "audio", "id": entry.id, "url": f"/api/generated/{entry.id}"})


_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".gif", ".mkv"}


async def _generate_video(args: dict, ctx: ToolContext) -> str:
    workflow_name = args.get("workflow_name", "")
    prompt = args.get("prompt", "")
    if not workflow_name or not prompt:
        return "error: workflow_name and prompt are required"
    if not ctx.custom_workflows:
        return "error: no custom workflows available"

    workflow = next((w for w in ctx.custom_workflows.list() if w.name == workflow_name), None)
    if not workflow:
        names = [w.name for w in ctx.custom_workflows.list()]
        return f"error: no saved workflow named '{workflow_name}'. Available: {names or 'none — add one in Settings → Local Generation'}"

    comfy_cfg = ctx.comfyui_config.get() if ctx.comfyui_config else None
    base_url = comfy_cfg.base_url if comfy_cfg else COMFYUI_DEFAULT_BASE_URL
    if not await comfyui_client.detect(base_url):
        return f"error: ComfyUI is not reachable at {base_url}"

    try:
        files = await comfyui_client.run_custom_workflow(
            base_url, workflow.workflow, workflow.prompt_node_id, workflow.prompt_input_key, prompt,
        )
    except RuntimeError as e:
        return f"error: {e}"

    saved = []
    for filename, data in files:
        ext = ("." + filename.rsplit(".", 1)[-1]).lower() if "." in filename else ""
        kind = "video" if ext in _VIDEO_EXTENSIONS else "image"
        content_type = "video/mp4" if kind == "video" else "image/png"
        entry = ctx.generated_files.add(kind, filename, content_type, data, source=prompt)
        saved.append({"kind": kind, "id": entry.id, "url": f"/api/generated/{entry.id}", "filename": filename})
    return json.dumps({"files": saved})


def _generate_document(args: dict, ctx: ToolContext) -> str:
    content = args.get("content", "")
    fmt = args.get("format", "md")
    if not content:
        return "error: content is required"
    if fmt not in document_generation.VALID_FORMATS:
        return f"error: unsupported format '{fmt}'"
    try:
        data = document_generation.generate(content, fmt)
    except Exception as e:
        return f"error: could not generate document: {e}"
    filename = (args.get("filename") or "document").strip() + "." + fmt
    entry = ctx.generated_files.add("document", filename, document_generation.CONTENT_TYPES[fmt],
                                      data, source=content[:200])
    return json.dumps({"kind": "document", "id": entry.id, "url": f"/api/generated/{entry.id}", "filename": filename})


def _edit_file(args: dict, ctx: ToolContext) -> str:
    path = args.get("path")
    new_content = args.get("content", "")
    if not path:
        return "error: path is required"

    old_content = file_edit_tool.read_current(path)
    diff_text = file_edit_tool.make_diff(path, old_content, new_content)

    if ctx.require_edit_approval:
        pending_id = file_edit_tool.stage(path, new_content, diff_text)
        return json.dumps({"staged": True, "pending_id": pending_id, "path": path, "diff": diff_text})

    result = file_edit_tool.apply_edit(path, new_content)
    return json.dumps({"applied": result["ok"], "path": path, "diff": diff_text,
                        **({"error": result["error"]} if not result["ok"] else {})})


async def _deep_research(args: dict, ctx: ToolContext) -> str:
    query = args.get("query", "")
    if not query:
        return "error: query is required"

    async def chat_fn(prompt: str) -> str:
        from agent_loop import run_chat_collected
        return await run_chat_collected(ctx.base_url, ctx.api_key, ctx.model,
                                          [{"role": "user", "content": prompt}])

    return await deep_research.research(query, chat_fn)


async def _search_web(args: dict) -> str:
    query = args.get("query", "")
    if not query:
        return "error: query is required"
    results = await web_search.search(query)
    if not results:
        return "no results found"
    return json.dumps(results)


async def _manage_memory(args: dict, memory: MemoryStore) -> str:
    action = args.get("action")
    if action == "list":
        return json.dumps([{"id": e.id, "text": e.text, "category": e.category} for e in memory.list()])
    if action == "add":
        text = args.get("text")
        if not text:
            return "error: text is required for action=add"
        entry = memory.add(text, category=args.get("category", "fact"), source="agent")
        return json.dumps({"id": entry.id, "text": entry.text, "category": entry.category})
    if action == "edit":
        memory_id = args.get("memory_id")
        if not memory_id:
            return "error: memory_id is required for action=edit"
        ok = memory.update(memory_id, text=args.get("text"), category=args.get("category"))
        return "updated" if ok else f"error: no memory with id {memory_id}"
    if action == "delete":
        memory_id = args.get("memory_id")
        if not memory_id:
            return "error: memory_id is required for action=delete"
        ok = memory.delete(memory_id)
        return "deleted" if ok else f"error: no memory with id {memory_id}"
    if action == "search":
        query = args.get("query", "")
        results = await memory.relevant(query)
        return json.dumps([{"id": e.id, "text": e.text, "category": e.category} for e in results])
    return f"error: unknown action '{action}'"


async def _search_documents(args: dict, documents: DocumentStore) -> str:
    query = args.get("query", "")
    if not query:
        return "error: query is required"
    results = await documents.search(query)
    if not results:
        return "no matching passages found"
    return json.dumps(results)


async def _manage_tasks(args: dict, ctx: ToolContext) -> str:
    action = args.get("action")
    if action == "list":
        return json.dumps([
            {"id": t.id, "name": t.name, "schedule": t.schedule, "status": t.status, "next_run": t.next_run}
            for t in ctx.tasks.list()
        ])
    if action == "create":
        prompt = args.get("prompt")
        schedule = args.get("schedule")
        if not prompt or schedule not in {"once", "daily", "weekly", "cron"}:
            return "error: prompt and a valid schedule are required for action=create"
        from task_scheduler import compute_next_run
        try:
            next_run = compute_next_run(schedule, args.get("scheduled_time", ""),
                                         args.get("scheduled_day", ""), args.get("cron_expression", ""))
        except ValueError as e:
            return f"error: {e}"
        task = ctx.tasks.add(ScheduledTask(
            id="", name=args.get("name", "")[:80] or prompt[:40], prompt=prompt, schedule=schedule,
            scheduled_time=args.get("scheduled_time", ""), scheduled_day=args.get("scheduled_day", ""),
            cron_expression=args.get("cron_expression", ""), endpoint_id="", model=ctx.model,
            next_run=next_run,
        ))
        return json.dumps({"id": task.id, "name": task.name, "next_run": task.next_run})
    if action in ("pause", "resume", "delete", "run_now"):
        task_id = args.get("task_id")
        if not task_id:
            return f"error: task_id is required for action={action}"
        task = ctx.tasks.get(task_id)
        if not task:
            return f"error: no task with id {task_id}"
        if action == "pause":
            ctx.tasks.update(task_id, status="paused")
            return "paused"
        if action == "resume":
            ctx.tasks.update(task_id, status="active")
            return "resumed"
        if action == "delete":
            ctx.tasks.delete(task_id)
            return "deleted"
        if action == "run_now":
            from task_scheduler import TaskScheduler
            scheduler = TaskScheduler(ctx.tasks, ctx.task_runs, _make_task_chat_fn(ctx))
            await scheduler.run_now(task)
            return "ran"
    return f"error: unknown action '{action}'"


def _make_task_chat_fn(ctx: ToolContext):
    async def chat_fn(endpoint_id, model, prompt, enabled_mcp_servers, enabled_builtin_tools):
        from agent_loop import run_chat_collected
        base_url = ctx.base_url  # tasks created without a real endpoint reuse the caller's
        return await run_chat_collected(
            base_url, ctx.api_key, model or ctx.model, [{"role": "user", "content": prompt}],
            enabled_server_ids=set(enabled_mcp_servers), enabled_builtin_tools=set(enabled_builtin_tools),
            ctx=ctx,
        )
    return chat_fn


async def _manage_email(args: dict, ctx: ToolContext) -> str:
    action = args.get("action")
    if action == "list_accounts":
        return json.dumps([
            {"id": a.id, "label": a.label, "email_address": a.email_address}
            for a in ctx.email_accounts.list()
        ])

    account_id = args.get("account_id")
    account = ctx.email_accounts.get(account_id) if account_id else None
    if action != "list_accounts" and not account:
        return "error: a valid account_id is required"
    password = ctx.api_keys.load().get(account_id) if account else None
    folder = args.get("folder") or "INBOX"

    try:
        if action == "list_messages":
            return json.dumps(email_client.list_messages(account, password, folder))
        if action == "search":
            query = args.get("query", "")
            if not query:
                return "error: query is required"
            return json.dumps(email_client.search_messages(account, password, query, folder))
        if action == "read_message":
            uid = args.get("uid")
            if not uid:
                return "error: uid is required"
            return json.dumps(email_client.read_message(account, password, folder, uid))
        if action == "send":
            to, subject, body = args.get("to"), args.get("subject", ""), args.get("body", "")
            if not to or not body:
                return "error: to and body are required"
            email_client.send_message(account, password, to, subject, body)
            return "sent"
        if action == "reply":
            uid, body = args.get("uid"), args.get("body", "")
            if not uid or not body:
                return "error: uid and body are required"
            email_client.reply_message(account, password, folder, uid, body)
            return "sent"
        if action == "mark_read":
            uid = args.get("uid")
            if not uid:
                return "error: uid is required"
            email_client.mark_read(account, password, folder, uid)
            return "marked read"
        if action == "archive":
            uid = args.get("uid")
            if not uid:
                return "error: uid is required"
            email_client.archive_message(account, password, folder, uid)
            return "archived"
        if action == "delete":
            uid = args.get("uid")
            if not uid:
                return "error: uid is required"
            email_client.delete_message(account, password, folder, uid)
            return "deleted"
        if action in ("summarize", "draft_reply", "check_urgency"):
            uid = args.get("uid")
            if not uid:
                return "error: uid is required"
            message = email_client.read_message(account, password, folder, uid, mark_seen=False)
            return await email_ai_action(action, message, args.get("instruction", ""), ctx)
    except Exception as e:
        return f"error: {e}"
    return f"error: unknown action '{action}'"


async def email_ai_action(action: str, message: dict, instruction: str, ctx: ToolContext) -> str:
    """summarize/draft_reply/check_urgency — each a single on-demand chat
    call against whatever endpoint the current conversation is using. Never
    called automatically; only when this specific tool action is invoked."""
    from agent_loop import run_chat_collected

    if action == "summarize":
        prompt = f"Summarize this email in 2-3 sentences.\n\nFrom: {message['from']}\nSubject: {message['subject']}\n\n{message['body']}"
    elif action == "check_urgency":
        prompt = (f"Is this email urgent (needs action within 24 hours)? Reply with one line: "
                  f"URGENT or NOT URGENT, then a short reason.\n\nFrom: {message['from']}\nSubject: {message['subject']}\n\n{message['body']}")
    else:  # draft_reply
        prompt = (f"Draft a reply to this email. {instruction or 'Keep it brief and polite.'}\n\n"
                  f"From: {message['from']}\nSubject: {message['subject']}\n\n{message['body']}")

    return await run_chat_collected(ctx.base_url, ctx.api_key, ctx.model, [{"role": "user", "content": prompt}])


def _note_summary(n) -> dict:
    return {"id": n.id, "title": n.title, "note_type": n.note_type, "due_date": n.due_date,
            "repeat": n.repeat, "label": n.label, "pinned": n.pinned,
            "items": [{"text": i.text, "done": i.done} for i in n.items]}


def _manage_notes(args: dict, notes: NoteStore) -> str:
    action = args.get("action")
    if action == "list":
        return json.dumps([_note_summary(n) for n in notes.list(archived=False)])
    if action == "view":
        note_id = args.get("note_id")
        if not note_id:
            return "error: note_id is required for action=view"
        note = notes.get(note_id)
        return json.dumps(_note_summary(note)) if note else f"error: no note with id {note_id}"
    if action == "add":
        title = args.get("title")
        if not title:
            return "error: title is required for action=add"
        note = notes.add(
            title=title, content=args.get("content", ""), items=args.get("checklist_items"),
            note_type=args.get("note_type", "note"), color=args.get("color", ""),
            label=args.get("label", ""), due_date=date_parsing.parse_due_date(args.get("due_date", "")),
            repeat=args.get("repeat", "none"), source="agent",
        )
        return json.dumps(_note_summary(note))
    if action == "update":
        note_id = args.get("note_id")
        if not note_id:
            return "error: note_id is required for action=update"
        raw_due_date = args.get("due_date")
        ok = notes.update(
            note_id, title=args.get("title"), content=args.get("content"),
            items=args.get("checklist_items"), note_type=args.get("note_type"),
            color=args.get("color"), label=args.get("label"),
            due_date=date_parsing.parse_due_date(raw_due_date) if raw_due_date is not None else None,
            repeat=args.get("repeat"),
        )
        return "updated" if ok else f"error: no note with id {note_id}"
    if action == "delete":
        note_id = args.get("note_id")
        if not note_id:
            return "error: note_id is required for action=delete"
        ok = notes.delete(note_id)
        return "deleted" if ok else f"error: no note with id {note_id}"
    if action == "toggle_item":
        note_id = args.get("note_id")
        index = args.get("item_index")
        if not note_id or index is None:
            return "error: note_id and item_index are required for action=toggle_item"
        ok = notes.toggle_item(note_id, index)
        return "toggled" if ok else "error: no note/item at that index"
    return f"error: unknown action '{action}'"
