from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from croniter import croniter

from task_store import ScheduledTask, TaskRun, TaskRunStore, TaskStore

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

def compute_next_run(schedule: str, scheduled_time: str = "", scheduled_day: str = "",
                      cron_expression: str = "", after: datetime | None = None) -> str:
    now = after or datetime.now(timezone.utc)

    if schedule == "cron":
        if not cron_expression:
            raise ValueError("cron_expression is required for schedule='cron'")
        return croniter(cron_expression, now).get_next(datetime).isoformat()

    hour, minute = 9, 0
    if scheduled_time:
        try:
            hour, minute = (int(p) for p in scheduled_time.split(":", 1))
        except ValueError:
            pass

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if schedule == "once" or schedule == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat()

    if schedule == "weekly":
        target_weekday = _WEEKDAYS.index(scheduled_day.lower()) if scheduled_day.lower() in _WEEKDAYS else candidate.weekday()
        days_ahead = (target_weekday - candidate.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate.isoformat()

    raise ValueError(f"unknown schedule type: {schedule}")

async def _run_task(task: ScheduledTask, task_runs: TaskRunStore, chat_fn) -> None:
    started = datetime.now(timezone.utc).isoformat()
    run = task_runs.add(TaskRun(id="", task_id=task.id, started=started, status="running"))
    try:
        output = await chat_fn(task.endpoint_id, task.model, task.prompt,
                                task.enabled_mcp_servers, task.enabled_builtin_tools)
        task_runs.update(run.id, status="ok", output=output, finished=datetime.now(timezone.utc).isoformat())
    except Exception as e:
        logger.warning("task %s (%s) failed: %s", task.id, task.name, e)
        task_runs.update(run.id, status="error", output=str(e), finished=datetime.now(timezone.utc).isoformat())

class TaskScheduler:
    def __init__(self, tasks: TaskStore, task_runs: TaskRunStore, chat_fn):
        self.tasks = tasks
        self.task_runs = task_runs
        self.chat_fn = chat_fn
        self._stop = asyncio.Event()

    async def run_loop(self):
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                logger.warning("task scheduler tick failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    def stop(self):
        self._stop.set()

    async def _tick(self):
        now = datetime.now(timezone.utc).isoformat()
        for task in self.tasks.list():
            if task.status != "active" or not task.next_run or task.next_run > now:
                continue
            await self.run_now(task)

    async def run_now(self, task: ScheduledTask) -> None:
        await _run_task(task, self.task_runs, self.chat_fn)
        updates = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "run_count": task.run_count + 1,
        }
        if task.schedule == "once":
            updates["status"] = "paused"
        else:
            updates["next_run"] = compute_next_run(task.schedule, task.scheduled_time,
                                                     task.scheduled_day, task.cron_expression)
        self.tasks.update(task.id, **updates)
