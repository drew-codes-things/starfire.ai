"""Stdlib IMAP/SMTP mailbox operations — no OAuth, no caching, every call
opens a live connection. Ported from odysseus-dev's protocol-handling code
in routes/email_helpers.py / routes/email_routes.py, which is itself pure
stdlib (imaplib/smtplib/email) with no third-party mail library — that
carries over here with zero new dependencies.

Every function takes the account's password already decrypted by the
caller (routes.py), the same separation providers.py uses for provider API
keys — this module never touches the encrypted store directly.
"""

from __future__ import annotations

import email as email_pkg
import imaplib
import smtplib
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from email_store import EmailAccount


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for text, enc in parts:
        if isinstance(text, bytes):
            decoded.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


def _connect_imap(account: EmailAccount, password: str) -> imaplib.IMAP4_SSL:
    conn = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
    conn.login(account.username or account.email_address, password)
    return conn


def _strip_html(html: str) -> str:
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts: list[str] = []

        def handle_data(self, data):
            self.parts.append(data)

    stripper = _Stripper()
    stripper.feed(html)
    return "".join(stripper.parts)


def _body_text(msg: "email_pkg.message.Message") -> str:
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        if payload is None:
            return msg.get_payload() or ""
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        return _strip_html(text) if msg.get_content_type() == "text/html" else text

    plain = html = None
    for part in msg.walk():
        if "attachment" in str(part.get("Content-Disposition", "")):
            continue
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if ctype == "text/plain" and plain is None:
            plain = text
        elif ctype == "text/html" and html is None:
            html = text
    if plain is not None:
        return plain
    if html is not None:
        return _strip_html(html)
    return ""


def list_folders(account: EmailAccount, password: str) -> list[str]:
    conn = _connect_imap(account, password)
    try:
        typ, data = conn.list()
        if typ != "OK":
            raise RuntimeError(f"LIST failed: {typ}")
        folders = []
        for line in data:
            if not line:
                continue
            decoded = line.decode("utf-8", errors="replace")
            # e.g. '(\\HasNoChildren) "/" "INBOX"' — folder name is the last
            # quoted token.
            if '"' in decoded:
                name = decoded.rsplit('"', 2)[-2]
            else:
                name = decoded.split()[-1]
            folders.append(name)
        return folders
    finally:
        conn.logout()


def list_messages(account: EmailAccount, password: str, folder: str = "INBOX", limit: int = 30) -> list[dict]:
    conn = _connect_imap(account, password)
    try:
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"cannot open folder '{folder}'")
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK":
            raise RuntimeError("SEARCH failed")
        uids = data[0].split()
        uids = list(reversed(uids[-limit:]))  # most recent first

        messages = []
        for uid in uids:
            typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)] FLAGS)")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            flags_line = msg_data[0][0].decode("utf-8", errors="replace")
            msg = email_pkg.message_from_bytes(msg_data[0][1])
            messages.append({
                "uid": uid.decode(), "from": _decode(msg.get("From")), "to": _decode(msg.get("To")),
                "subject": _decode(msg.get("Subject")), "date": msg.get("Date", ""),
                "unread": "\\Seen" not in flags_line,
            })
        return messages
    finally:
        conn.logout()


def read_message(account: EmailAccount, password: str, folder: str, uid: str, mark_seen: bool = True) -> dict:
    conn = _connect_imap(account, password)
    try:
        typ, _ = conn.select(folder)
        if typ != "OK":
            raise RuntimeError(f"cannot open folder '{folder}'")
        typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[])")
        if typ != "OK" or not msg_data or msg_data[0] is None:
            raise RuntimeError(f"message {uid} not found in '{folder}'")
        msg = email_pkg.message_from_bytes(msg_data[0][1])
        result = {
            "uid": uid, "from": _decode(msg.get("From")), "to": _decode(msg.get("To")),
            "subject": _decode(msg.get("Subject")), "date": msg.get("Date", ""),
            "message_id": msg.get("Message-ID", ""), "body": _body_text(msg),
        }
        if mark_seen:
            conn.uid("store", uid, "+FLAGS", "\\Seen")
        return result
    finally:
        conn.logout()


def search_messages(account: EmailAccount, password: str, query: str, folder: str = "INBOX", limit: int = 20) -> list[dict]:
    conn = _connect_imap(account, password)
    try:
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"cannot open folder '{folder}'")
        escaped = query.replace('"', '\\"')
        typ, data = conn.uid("search", None, "TEXT", f'"{escaped}"')
        if typ != "OK":
            raise RuntimeError("SEARCH failed")
        uids = list(reversed(data[0].split()))[:limit]

        results = []
        for uid in uids:
            typ, msg_data = conn.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
            if typ != "OK" or not msg_data or msg_data[0] is None:
                continue
            msg = email_pkg.message_from_bytes(msg_data[0][1])
            results.append({
                "uid": uid.decode(), "from": _decode(msg.get("From")), "subject": _decode(msg.get("Subject")),
                "date": msg.get("Date", ""),
            })
        return results
    finally:
        conn.logout()


def mark_read(account: EmailAccount, password: str, folder: str, uid: str, read: bool = True) -> None:
    conn = _connect_imap(account, password)
    try:
        conn.select(folder)
        flag_op = "+FLAGS" if read else "-FLAGS"
        conn.uid("store", uid, flag_op, "\\Seen")
    finally:
        conn.logout()


def archive_message(account: EmailAccount, password: str, folder: str, uid: str, archive_folder: str = "Archive") -> None:
    """Copies to `archive_folder` (defaults to "Archive" — the exact
    special-use folder name varies by provider, e.g. Gmail's is
    "[Gmail]/All Mail"; pass the right one for your provider) then deletes
    from the source folder."""
    conn = _connect_imap(account, password)
    try:
        conn.select(folder)
        typ, _ = conn.uid("copy", uid, archive_folder)
        if typ != "OK":
            raise RuntimeError(f"could not copy to '{archive_folder}' — does that folder exist?")
        conn.uid("store", uid, "+FLAGS", "\\Deleted")
        conn.expunge()
    finally:
        conn.logout()


def delete_message(account: EmailAccount, password: str, folder: str, uid: str) -> None:
    conn = _connect_imap(account, password)
    try:
        conn.select(folder)
        conn.uid("store", uid, "+FLAGS", "\\Deleted")
        conn.expunge()
    finally:
        conn.logout()


def send_message(account: EmailAccount, password: str, to: str, subject: str, body: str,
                  in_reply_to: str | None = None) -> None:
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = account.email_address
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to

    smtp_host = account.smtp_host or account.imap_host.replace("imap", "smtp", 1)
    if account.smtp_port == 465:
        server = smtplib.SMTP_SSL(smtp_host, account.smtp_port)
    else:
        server = smtplib.SMTP(smtp_host, account.smtp_port)
        server.starttls()
    try:
        server.login(account.username or account.email_address, password)
        server.send_message(msg)
    finally:
        server.quit()


def reply_message(account: EmailAccount, password: str, folder: str, uid: str, body: str) -> None:
    original = read_message(account, password, folder, uid, mark_seen=False)
    subject = original["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    reply_to = original["from"]
    send_message(account, password, reply_to, subject, body, in_reply_to=original["message_id"] or None)
