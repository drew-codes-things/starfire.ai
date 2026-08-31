"""Background loop that checks each enabled email rule's account/folder for
new messages and applies the rule's action. Same run_loop()/stop() shape as
task_scheduler.py's TaskScheduler, so server.py starts/stops it the same way.

email_client.py's functions are synchronous (imaplib/smtplib, blocking) —
routes.py calls them directly since a single interactive request blocking
briefly is fine. A background loop is a different failure mode: blocking
the whole event loop every TICK_SECONDS while polling a possibly slow/
unreachable IMAP server would stall every other request (chat streaming
included) for that duration. Every email_client call here goes through
asyncio.to_thread for exactly that reason — not needed for the on-demand
routes, needed here.
"""

from __future__ import annotations

import asyncio
import logging

import email_client
from email_store import EmailAccountStore, EmailRule, EmailRuleStore
from note_store import NoteStore

logger = logging.getLogger(__name__)

TICK_SECONDS = 120


class EmailRuleChecker:
    def __init__(self, rules: EmailRuleStore, accounts: EmailAccountStore, get_password, chat_fn, notes: NoteStore):
        """get_password(account_id) -> str | None and chat_fn(endpoint_id,
        model, prompt) -> awaitable[str] are injected rather than imported
        directly, mirroring task_scheduler.py's chat_fn injection — avoids
        this module importing routes.py (which would be a cycle, since
        routes.py is what constructs this checker)."""
        self.rules = rules
        self.accounts = accounts
        self.get_password = get_password
        self.chat_fn = chat_fn
        self.notes = notes
        self._stop = asyncio.Event()

    async def run_loop(self):
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as e:
                logger.warning("email rule check failed: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    def stop(self):
        self._stop.set()

    async def _tick(self):
        for rule in self.rules.list():
            if rule.enabled:
                await self._check_rule(rule)

    async def _check_rule(self, rule: EmailRule):
        account = self.accounts.get(rule.account_id)
        if not account:
            return
        password = self.get_password(rule.account_id)
        if not password:
            return
        try:
            messages = await asyncio.to_thread(email_client.list_messages, account, password, rule.folder)
        except Exception as e:
            logger.warning("email rule %s: could not list messages: %s", rule.id, e)
            return

        new_max_uid = rule.last_seen_uid
        for message in messages:
            try:
                uid = int(message["uid"])
            except (KeyError, ValueError, TypeError):
                continue
            if uid <= rule.last_seen_uid:
                continue
            new_max_uid = max(new_max_uid, uid)
            if self._matches(rule, message):
                await self._apply_action(rule, account, password, message)

        if new_max_uid != rule.last_seen_uid:
            self.rules.update(rule.id, last_seen_uid=new_max_uid)

    def _matches(self, rule: EmailRule, message: dict) -> bool:
        if not rule.match_value:
            return False
        field_value = (message.get(rule.match_field) or "").lower()
        return rule.match_value.lower() in field_value

    async def _apply_action(self, rule: EmailRule, account, password: str, message: dict):
        uid = message["uid"]
        try:
            if rule.action == "mark_read":
                await asyncio.to_thread(email_client.mark_read, account, password, rule.folder, uid)
            elif rule.action == "archive":
                await asyncio.to_thread(email_client.archive_message, account, password, rule.folder, uid)
            elif rule.action == "delete":
                await asyncio.to_thread(email_client.delete_message, account, password, rule.folder, uid)
            elif rule.action == "add_note":
                self.notes.add(
                    title=f"Email: {message.get('subject') or '(no subject)'}",
                    content=f"From: {message.get('from', '')}\n\n(added automatically — matched an email rule)",
                    source="agent",
                )
            elif rule.action == "ai_summarize_note":
                full = await asyncio.to_thread(email_client.read_message, account, password, rule.folder, uid, False)
                prompt = (f"Summarize this email in 2-3 sentences.\n\nFrom: {full['from']}\n"
                          f"Subject: {full['subject']}\n\n{full['body']}")
                summary = await self.chat_fn(rule.endpoint_id, rule.model, prompt)
                self.notes.add(title=f"Email: {full.get('subject') or '(no subject)'}", content=summary, source="agent")
        except Exception as e:
            logger.warning("email rule %s action %s failed: %s", rule.id, rule.action, e)
