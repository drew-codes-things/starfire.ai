from __future__ import annotations

import email as email_pkg
import imaplib
import ipaddress
import logging
import re
import smtplib
import socket
import ssl
import threading
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from email_store import EmailAccount
from mcp_oauth import is_public_address

logger = logging.getLogger(__name__)

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

MAIL_TIMEOUT_SECONDS = 20
MEBIBYTE = 1024 * 1024
MAX_MESSAGE_BYTES = 25 * MEBIBYTE
MAX_BYTES_PER_MAIL_CONNECTION = 32 * MEBIBYTE
MESSAGE_CUT_NOTE = f"\n\n(the rest of this message is over {MAX_MESSAGE_BYTES // MEBIBYTE} MB and was not fetched)"
SIZE_IN_FETCH = re.compile(rb"RFC822\.SIZE (\d+)")
MAIL_DEADLINE_SECONDS = 60
MAIL_SERVER_UNREACHABLE = "could not reach the mail server"
SERVER_ANSWERS = (imaplib.IMAP4.error, smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused, RuntimeError)


class MailHostRefused(ValueError):
    pass


class MailDeadlinePassed(TimeoutError):
    pass


class MailTooLarge(imaplib.IMAP4.error):
    pass


class MailDeadline:

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.expired = False
        self._lock = threading.Lock()
        self._copies: list[socket.socket] = []
        self._timer = threading.Timer(seconds, self.expire)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def watch(self, sock: socket.socket) -> None:
        copy = sock.dup()
        with self._lock:
            self._copies.append(copy)
            expired = self.expired
        if expired:
            _cut(copy)
            raise MailDeadlinePassed(f"the mail server took longer than {self.seconds:g}s")

    def expire(self) -> None:
        with self._lock:
            self.expired = True
            copies = list(self._copies)
        for copy in copies:
            _cut(copy)

    def finish(self) -> None:
        self._timer.cancel()
        with self._lock:
            copies, self._copies = self._copies, []
        for copy in copies:
            copy.close()


def _cut(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError as error:
        logger.debug("mail socket was already closed when its deadline passed: %s", error)


_operation = threading.local()


def _watched(sock: socket.socket) -> socket.socket:
    deadline = getattr(_operation, "deadline", None)
    if deadline is not None:
        deadline.watch(sock)
    return sock


def call_with_deadline(seconds: float, action, *args, **kwargs):
    deadline = MailDeadline(seconds)
    _operation.deadline = deadline
    deadline.start()
    try:
        return action(*args, **kwargs)
    except Exception as error:
        if deadline.expired:
            raise MailDeadlinePassed(f"the mail server took longer than {seconds:g}s") from error
        raise
    finally:
        deadline.finish()
        _operation.deadline = None


def mail_server_address(host: str, port: int, allow_private_hosts: bool) -> str:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as error:
        raise MailHostRefused(f"could not find the mail server {host}") from error
    addresses = [ipaddress.ip_address(str(answer[4][0]).split("%", 1)[0]) for answer in answers]
    if not addresses:
        raise MailHostRefused(f"could not find the mail server {host}")
    if not allow_private_hosts and not all(is_public_address(address) for address in addresses):
        raise MailHostRefused(f"{host} is not a public mail server; only an admin can use a private address")
    return str(addresses[0])


def describe_failure(error: Exception, show_details: bool) -> str:
    is_server_answer = isinstance(error, SERVER_ANSWERS) and not isinstance(error, imaplib.IMAP4.abort)
    if show_details or is_server_answer or isinstance(error, MailHostRefused):
        return str(error)
    logger.info("mail server connection failed: %s", error)
    return MAIL_SERVER_UNREACHABLE


class PinnedIMAP4SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, *, address: str, ssl_context: ssl.SSLContext, timeout: float):
        self.pinned_address = address
        self.bytes_left = MAX_BYTES_PER_MAIL_CONNECTION
        self.stopped_for_size = False
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout):
        sock = _watched(socket.create_connection((self.pinned_address, self.port), timeout))
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)

    def _spend(self, size: int) -> None:
        if self.stopped_for_size or size > self.bytes_left:
            self.stopped_for_size = True
            raise MailTooLarge(f"the mail server sent more than {MAX_BYTES_PER_MAIL_CONNECTION // MEBIBYTE} MB "
                               "for one mail request, so it was stopped")
        self.bytes_left -= size

    def read(self, size):
        self._spend(size)
        return super().read(size)

    def readline(self):
        line = super().readline()
        self._spend(len(line))
        return line

    def logout(self):
        if not self.stopped_for_size:
            return super().logout()
        self.shutdown()
        return "BYE", [b"stopped after the mail server sent too much"]


class _PinnedSmtpSocket:
    def __init__(self, host: str, port: int, *, address: str, timeout: float, **options):
        self.pinned_address = address
        super().__init__(host, port, timeout=timeout, **options)

    def _connect_pinned(self, port, timeout) -> socket.socket:
        return _watched(socket.create_connection((self.pinned_address, port), timeout, self.source_address))


class PinnedSMTP(_PinnedSmtpSocket, smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return self._connect_pinned(port, timeout)


class PinnedSMTPSSL(_PinnedSmtpSocket, smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        return self.context.wrap_socket(self._connect_pinned(port, timeout), server_hostname=self._host)


def _connect_imap(account: EmailAccount, password: str, allow_private_hosts: bool = False) -> imaplib.IMAP4_SSL:
    address = mail_server_address(account.imap_host, account.imap_port, allow_private_hosts)
    conn = PinnedIMAP4SSL(account.imap_host, account.imap_port, address=address,
                          ssl_context=ssl.create_default_context(), timeout=MAIL_TIMEOUT_SECONDS)
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

def list_folders(account: EmailAccount, password: str, *, allow_private_hosts: bool = False) -> list[str]:
    conn = _connect_imap(account, password, allow_private_hosts)
    try:
        typ, data = conn.list()
        if typ != "OK":
            raise RuntimeError(f"LIST failed: {typ}")
        folders = []
        for line in data:
            if not line:
                continue
            decoded = line.decode("utf-8", errors="replace")
            if '"' in decoded:
                name = decoded.rsplit('"', 2)[-2]
            else:
                name = decoded.split()[-1]
            folders.append(name)
        return folders
    finally:
        conn.logout()

def list_messages(account: EmailAccount, password: str, folder: str = "INBOX", limit: int = 30,
                  *, allow_private_hosts: bool = False) -> list[dict]:
    conn = _connect_imap(account, password, allow_private_hosts)
    try:
        typ, _ = conn.select(folder, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"cannot open folder '{folder}'")
        typ, data = conn.uid("search", None, "ALL")
        if typ != "OK":
            raise RuntimeError("SEARCH failed")
        uids = data[0].split()
        uids = list(reversed(uids[-limit:]))

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

def _message_size(conn: imaplib.IMAP4, uid: str) -> int | None:
    typ, data = conn.uid("fetch", uid, "(RFC822.SIZE)")
    if typ != "OK":
        return None
    for item in data or []:
        line = item[0] if isinstance(item, tuple) else item
        found = SIZE_IN_FETCH.search(line) if isinstance(line, bytes) else None
        if found:
            return int(found.group(1))
    return None


def _fits_whole(size: int | None) -> bool:
    return size is not None and size <= MAX_MESSAGE_BYTES


def read_message(account: EmailAccount, password: str, folder: str, uid: str, mark_seen: bool = True,
                 *, allow_private_hosts: bool = False) -> dict:
    conn = _connect_imap(account, password, allow_private_hosts)
    try:
        typ, _ = conn.select(folder)
        if typ != "OK":
            raise RuntimeError(f"cannot open folder '{folder}'")
        is_whole = _fits_whole(_message_size(conn, uid))
        part = "(BODY.PEEK[])" if is_whole else f"(BODY.PEEK[]<0.{MAX_MESSAGE_BYTES}>)"
        typ, msg_data = conn.uid("fetch", uid, part)
        if typ != "OK" or not msg_data or msg_data[0] is None:
            raise RuntimeError(f"message {uid} not found in '{folder}'")
        raw = msg_data[0][1]
        was_cut = not is_whole and len(raw) >= MAX_MESSAGE_BYTES
        msg = email_pkg.message_from_bytes(raw)
        result = {
            "uid": uid, "from": _decode(msg.get("From")), "to": _decode(msg.get("To")),
            "subject": _decode(msg.get("Subject")), "date": msg.get("Date", ""),
            "message_id": msg.get("Message-ID", ""), "body": _body_text(msg) + (MESSAGE_CUT_NOTE if was_cut else ""),
        }
        if mark_seen:
            conn.uid("store", uid, "+FLAGS", "\\Seen")
        return result
    finally:
        conn.logout()

def search_messages(account: EmailAccount, password: str, query: str, folder: str = "INBOX", limit: int = 20,
                    *, allow_private_hosts: bool = False) -> list[dict]:
    conn = _connect_imap(account, password, allow_private_hosts)
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

def mark_read(account: EmailAccount, password: str, folder: str, uid: str, read: bool = True,
              *, allow_private_hosts: bool = False) -> None:
    conn = _connect_imap(account, password, allow_private_hosts)
    try:
        conn.select(folder)
        flag_op = "+FLAGS" if read else "-FLAGS"
        conn.uid("store", uid, flag_op, "\\Seen")
    finally:
        conn.logout()

def archive_message(account: EmailAccount, password: str, folder: str, uid: str, archive_folder: str = "Archive",
                    *, allow_private_hosts: bool = False) -> None:
    conn = _connect_imap(account, password, allow_private_hosts)
    try:
        conn.select(folder)
        typ, _ = conn.uid("copy", uid, archive_folder)
        if typ != "OK":
            raise RuntimeError(f"could not copy to '{archive_folder}' - does that folder exist?")
        conn.uid("store", uid, "+FLAGS", "\\Deleted")
        conn.expunge()
    finally:
        conn.logout()

def delete_message(account: EmailAccount, password: str, folder: str, uid: str,
                   *, allow_private_hosts: bool = False) -> None:
    conn = _connect_imap(account, password, allow_private_hosts)
    try:
        conn.select(folder)
        conn.uid("store", uid, "+FLAGS", "\\Deleted")
        conn.expunge()
    finally:
        conn.logout()

def send_message(account: EmailAccount, password: str, to: str, subject: str, body: str,
                  in_reply_to: str | None = None, *, allow_private_hosts: bool = False) -> None:
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
    address = mail_server_address(smtp_host, account.smtp_port, allow_private_hosts)
    context = ssl.create_default_context()
    if account.smtp_port == 465:
        server = PinnedSMTPSSL(smtp_host, account.smtp_port, address=address, timeout=MAIL_TIMEOUT_SECONDS,
                               context=context)
    else:
        server = PinnedSMTP(smtp_host, account.smtp_port, address=address, timeout=MAIL_TIMEOUT_SECONDS)
        server.starttls(context=context)
    try:
        server.login(account.username or account.email_address, password)
        server.send_message(msg)
    finally:
        _say_goodbye(server, smtp_host)

def _say_goodbye(server: smtplib.SMTP, smtp_host: str) -> None:
    try:
        server.quit()
    except smtplib.SMTPServerDisconnected as error:
        logger.info("%s had already closed the SMTP connection: %s", smtp_host, error)
        server.close()

def reply_message(account: EmailAccount, password: str, folder: str, uid: str, body: str,
                  *, allow_private_hosts: bool = False) -> None:
    original = read_message(account, password, folder, uid, mark_seen=False, allow_private_hosts=allow_private_hosts)
    subject = original["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    reply_to = original["from"]
    send_message(account, password, reply_to, subject, body, in_reply_to=original["message_id"] or None,
                 allow_private_hosts=allow_private_hosts)
