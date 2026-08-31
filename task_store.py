"""Scheduled task automation: a task sends a prompt to a configured model on
a schedule and records the response. Ported from odysseus-dev's
core/database.py ScheduledTask/TaskRun tables, scoped to JSON-file storage
(no DB) and LLM-prompt tasks only — no shell/SSH/webhook action types (see
task_scheduler.py's module docstring for why that's a deliberate cut, not an
oversight).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field

from atomic_io import atomic_write_json

VALID_SCHEDULES = {"once", "daily", "weekly", "cron"}


@dataclass
class ScheduledTask:
    id: str
    name: str
    prompt: str
    schedule: str  # once | daily | weekly | cron
    scheduled_time: str = ""   # "HH:MM", for once/daily/weekly
    scheduled_day: str = ""    # weekday name (e.g. "monday"), for weekly
    cron_expression: str = ""  # for cron
    status: str = "active"     # active | paused
    endpoint_id: str = ""
    model: str = ""
    enabled_mcp_servers: list[str] = field(default_factory=list)
    enabled_builtin_tools: list[str] = field(default_factory=list)
    next_run: str = ""
    last_run: str = ""
    run_count: int = 0


@dataclass
class TaskRun:
    id: str
    task_id: str
    started: str
    finished: str = ""
    status: str = "running"  # running | ok | error
    output: str = ""


class TaskStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "tasks.json")

    def _load(self) -> list[ScheduledTask]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        tasks = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "prompt" not in item:
                continue
            tasks.append(ScheduledTask(
                id=item["id"], name=item.get("name", ""), prompt=item["prompt"],
                schedule=item.get("schedule", "once"),
                scheduled_time=item.get("scheduled_time", ""),
                scheduled_day=item.get("scheduled_day", ""),
                cron_expression=item.get("cron_expression", ""),
                status=item.get("status", "active"),
                endpoint_id=item.get("endpoint_id", ""), model=item.get("model", ""),
                enabled_mcp_servers=item.get("enabled_mcp_servers", []),
                enabled_builtin_tools=item.get("enabled_builtin_tools", []),
                next_run=item.get("next_run", ""), last_run=item.get("last_run", ""),
                run_count=item.get("run_count", 0),
            ))
        return tasks

    def _save(self, tasks: list[ScheduledTask]) -> None:
        atomic_write_json(self.path, [asdict(t) for t in tasks])

    def list(self) -> list[ScheduledTask]:
        return self._load()

    def get(self, task_id: str) -> ScheduledTask | None:
        for t in self._load():
            if t.id == task_id:
                return t
        return None

    def add(self, task: ScheduledTask) -> ScheduledTask:
        if not task.id:
            task.id = uuid.uuid4().hex[:12]
        tasks = self._load()
        tasks.append(task)
        self._save(tasks)
        return task

    def update(self, task_id: str, **fields) -> bool:
        tasks = self._load()
        found = False
        for t in tasks:
            if t.id == task_id:
                for k, v in fields.items():
                    if hasattr(t, k):
                        setattr(t, k, v)
                found = True
        if found:
            self._save(tasks)
        return found

    def delete(self, task_id: str) -> bool:
        tasks = self._load()
        remaining = [t for t in tasks if t.id != task_id]
        if len(remaining) == len(tasks):
            return False
        self._save(remaining)
        return True


class TaskRunStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "task_runs.json")

    def _load(self) -> list[TaskRun]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        runs = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "task_id" not in item:
                continue
            runs.append(TaskRun(
                id=item["id"], task_id=item["task_id"], started=item.get("started", ""),
                finished=item.get("finished", ""), status=item.get("status", "running"),
                output=item.get("output", ""),
            ))
        return runs

    def _save(self, runs: list[TaskRun]) -> None:
        atomic_write_json(self.path, [asdict(r) for r in runs])

    def add(self, run: TaskRun) -> TaskRun:
        if not run.id:
            run.id = uuid.uuid4().hex[:12]
        runs = self._load()
        runs.append(run)
        self._save(runs)
        return run

    def update(self, run_id: str, **fields) -> bool:
        runs = self._load()
        found = False
        for r in runs:
            if r.id == run_id:
                for k, v in fields.items():
                    if hasattr(r, k):
                        setattr(r, k, v)
                found = True
        if found:
            self._save(runs)
        return found

    def for_task(self, task_id: str, limit: int = 20) -> list[TaskRun]:
        runs = [r for r in self._load() if r.task_id == task_id]
        return sorted(runs, key=lambda r: r.started, reverse=True)[:limit]

    def recent(self, limit: int = 50) -> list[TaskRun]:
        runs = self._load()
        return sorted(runs, key=lambda r: r.started, reverse=True)[:limit]
