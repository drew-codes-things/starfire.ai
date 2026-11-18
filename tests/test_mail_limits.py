import asyncio
import concurrent.futures
import datetime
import imaplib
import importlib
import ipaddress
import os
import re
import smtplib
import socket
import ssl
import sys
import threading
import time
import tracemalloc

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import builtin_tools
import code_check
import email_client
import routes
import user_data
from api_key_manager import APIKeyManager
from config import config
from email_store import EmailAccount, EmailAccountStore

DRIP_INTERVAL_SECONDS = 0.2
DRIP_FOR_SECONDS = 15
TLS_RECORD_PROMISING_16_KIB = b"\x16\x03\x03\x40\x00"
SMALL_DEFAULT_POOL = 4
SLOW_MAIL_CALLS = 10


class DripServer:
    def __init__(self, first_bytes: bytes):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.first_bytes = first_bytes
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.server.settimeout(10)
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            try:
                conn.sendall(self.first_bytes)
                ends = time.monotonic() + DRIP_FOR_SECONDS
                while not self.stop.is_set() and time.monotonic() < ends:
                    conn.sendall(b"2")
                    self.stop.wait(DRIP_INTERVAL_SECONDS)
            except OSError:
                return

    def close(self):
        self.stop.set()
        self.server.close()


@pytest.fixture
def drip():
    servers = []

    def make(first_bytes: bytes = b""):
        servers.append(DripServer(first_bytes))
        return servers[-1]

    yield make
    for server in servers:
        server.close()


def local_account(port: int) -> EmailAccount:
    return EmailAccount(id="acc1", label="Home", email_address="me@home.example", imap_host="127.0.0.1",
                        imap_port=port, smtp_host="127.0.0.1", smtp_port=port, username="")


@pytest.fixture
def as_admin(monkeypatch):
    monkeypatch.setattr(routes, "_current_user_is_active_admin", lambda: True)
    monkeypatch.setattr(email_client, "MAIL_DEADLINE_SECONDS", 1, raising=False)


STILL_RUNNING_AFTER_SECONDS = 8


def time_mail_call(action, *args) -> tuple[float, BaseException | str | None]:
    async def call():
        task = asyncio.ensure_future(routes._mail(action, *args))
        done, _ = await asyncio.wait({task}, timeout=STILL_RUNNING_AFTER_SECONDS)
        if not done:
            return "still running"
        return task.exception()

    started = time.monotonic()
    outcome = asyncio.run(call())
    return time.monotonic() - started, outcome


MEBIBYTE = 1024 * 1024
HOSTILE_LITERAL_BYTES = 200 * MEBIBYTE
NOISE_LINE_BYTES = 512 * 1024
SEND_CHUNK = b"x" * MEBIBYTE
READ_PEAK_LIMIT_BYTES = 64 * MEBIBYTE
PARTIAL_FETCH = re.compile(rb"BODY\.PEEK\[\]<0\.(\d+)>")


def local_tls_contexts(tmp_path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "imap.crt", tmp_path / "imap.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    return server, client


def one_message(body_bytes: int) -> bytes:
    return (b"From: big@example.test\r\nTo: me@home.example\r\nSubject: big\r\n"
            b"Content-Type: text/plain\r\n\r\n" + b"y" * body_bytes)


class LocalImapServer:
    def __init__(self, context: ssl.SSLContext, message_size: int, honours_partial_fetch: bool,
                 select_noise_bytes: int = 0):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.context = context
        self.message_size = message_size
        self.honours_partial_fetch = honours_partial_fetch
        self.select_noise_bytes = select_noise_bytes
        self.commands = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.server.settimeout(10)
        try:
            raw, _ = self.server.accept()
            with self.context.wrap_socket(raw, server_side=True) as conn:
                self._converse(conn)
        except OSError:
            return

    def _converse(self, conn):
        conn.sendall(b"* OK ready\r\n")
        lines = conn.makefile("rb")
        while line := lines.readline():
            tag, _, command = line.rstrip(b"\r\n").partition(b" ")
            self.commands.append(command)
            upper = command.upper()
            if upper.startswith(b"LOGOUT"):
                conn.sendall(b"* BYE\r\n" + tag + b" OK bye\r\n")
                return
            for piece in self._untagged(upper):
                conn.sendall(piece)
            conn.sendall(tag + b" OK done\r\n")

    def _untagged(self, command: bytes):
        if command.startswith(b"CAPABILITY"):
            return [b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n"]
        if command.startswith((b"SELECT", b"EXAMINE")):
            return [*self._noise(), b"* 1 EXISTS\r\n"]
        if b"RFC822.SIZE" in command:
            return [b"* 1 FETCH (UID 1 RFC822.SIZE %d)\r\n" % self.message_size]
        if b"FETCH" in command:
            return self._fetched(PARTIAL_FETCH.search(command))
        return []

    def _noise(self):
        line = b"* OK " + b"n" * (NOISE_LINE_BYTES - len(b"* OK \r\n")) + b"\r\n"
        return [line] * (self.select_noise_bytes // NOISE_LINE_BYTES)

    def _fetched(self, partial):
        if not self.honours_partial_fetch:
            chunks = self.message_size // MEBIBYTE
            return [b"* 1 FETCH (UID 1 BODY[] {%d}\r\n" % (chunks * MEBIBYTE), *[SEND_CHUNK] * chunks, b")\r\n"]
        message = one_message(self.message_size)
        if partial:
            message = message[:int(partial.group(1))]
            return [b"* 1 FETCH (UID 1 BODY[]<0> {%d}\r\n" % len(message), message, b")\r\n"]
        return [b"* 1 FETCH (UID 1 BODY[] {%d}\r\n" % len(message), message, b")\r\n"]

    def close(self):
        self.server.close()


@pytest.fixture
def local_imap(tmp_path, monkeypatch):
    server_context, client_context = local_tls_contexts(tmp_path)
    monkeypatch.setattr(email_client.ssl, "create_default_context", lambda *args, **kwargs: client_context)
    servers = []

    def start(message_size: int, honours_partial_fetch: bool, select_noise_bytes: int = 0):
        servers.append(LocalImapServer(server_context, message_size, honours_partial_fetch, select_noise_bytes))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def read_with_peak(server) -> tuple[object, int]:
    tracemalloc.start()
    try:
        outcome = email_client.read_message(local_account(server.port), "pw", "INBOX", "1", mark_seen=False,
                                            allow_private_hosts=True)
    except imaplib.IMAP4.error as error:
        outcome = error
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return outcome, peak


def test_a_mail_server_that_sends_a_huge_message_is_stopped_before_it_is_read(local_imap):
    server = local_imap(HOSTILE_LITERAL_BYTES, honours_partial_fetch=False)
    started = time.monotonic()
    outcome, peak = read_with_peak(server)
    assert isinstance(outcome, imaplib.IMAP4.error) and "more than" in str(outcome), outcome
    assert peak < READ_PEAK_LIMIT_BYTES, peak
    assert time.monotonic() - started < 10


def test_a_mail_server_that_sends_endless_status_lines_is_stopped_at_the_byte_limit(local_imap):
    server = local_imap(1000, honours_partial_fetch=True,
                        select_noise_bytes=email_client.MAX_BYTES_PER_MAIL_CONNECTION + 8 * MEBIBYTE)
    outcome, _ = read_with_peak(server)
    assert isinstance(outcome, imaplib.IMAP4.error) and "more than" in str(outcome), outcome
    assert not any(b"FETCH" in command for command in server.commands)


def test_a_message_over_the_size_limit_is_fetched_only_up_to_the_limit(local_imap):
    server = local_imap(email_client.MAX_MESSAGE_BYTES + MEBIBYTE, honours_partial_fetch=True)
    message, peak = read_with_peak(server)
    assert isinstance(message, dict), message
    assert message["subject"] == "big" and message["body"].endswith(email_client.MESSAGE_CUT_NOTE)
    assert any(b"RFC822.SIZE" in command for command in server.commands)
    assert any(b"BODY.PEEK[]<0.%d>" % email_client.MAX_MESSAGE_BYTES in command for command in server.commands)


def test_a_small_message_is_fetched_whole(local_imap):
    server = local_imap(1000, honours_partial_fetch=True)
    message, _ = read_with_peak(server)
    assert isinstance(message, dict) and message["body"] == "y" * 1000
    assert any(re.search(rb"BODY\.PEEK\[\]\)", command) for command in server.commands)


def test_an_smtp_server_that_drips_its_greeting_is_cut_off_at_the_deadline(drip, as_admin):
    server = drip()
    seconds, error = time_mail_call(email_client.send_message, local_account(server.port), "pw", "a@b.example", "s", "b")
    assert isinstance(error, OSError), error
    assert seconds < 4


def test_an_imap_server_that_drips_its_tls_handshake_is_cut_off_at_the_deadline(drip, as_admin):
    server = drip(TLS_RECORD_PROMISING_16_KIB)
    seconds, error = time_mail_call(email_client.list_folders, local_account(server.port), "pw")
    assert isinstance(error, OSError), error
    assert seconds < 4


def test_ten_slow_mail_calls_do_not_stall_code_checks_or_other_threaded_work(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(code_check, "check_slots", code_check.CheckSlots())

    def slow_mail(*args, **kwargs):
        release.wait(10)
        return []

    async def scenario():
        asyncio.get_running_loop().set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=SMALL_DEFAULT_POOL))
        calls = []
        for number in range(SLOW_MAIL_CALLS):
            token = user_data.current_user_id.set(f"account-{number}")
            calls.append(asyncio.create_task(routes._mail(slow_mail)))
            user_data.current_user_id.reset(token)
        await asyncio.sleep(0.3)
        try:
            other_work = await asyncio.wait_for(asyncio.to_thread(lambda: "done"), timeout=3)
            checked = await asyncio.wait_for(code_check.check("json", "{}", account="someone"), timeout=5)
        finally:
            release.set()
            await asyncio.gather(*calls, return_exceptions=True)
        return other_work, checked["status"]

    assert asyncio.run(scenario()) == ("done", "ok")


def test_one_account_cannot_run_more_than_two_mail_calls_at_once():
    mail_runner = importlib.import_module("mail_runner")
    runner = mail_runner.MailRunner()
    release = threading.Event()

    def slow_mail():
        release.wait(5)
        return "read"

    async def scenario():
        first = [asyncio.create_task(runner.run("alice", None, slow_mail)) for _ in range(2)]
        await asyncio.sleep(0.1)
        with pytest.raises(mail_runner.MailRefused):
            await runner.run("alice", None, slow_mail)
        other = asyncio.create_task(runner.run("bob", None, slow_mail))
        release.set()
        return await asyncio.gather(*first, other)

    assert asyncio.run(scenario()) == ["read", "read", "read"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    accounts = EmailAccountStore(str(tmp_path))
    keys = APIKeyManager(str(tmp_path))
    monkeypatch.setattr(routes, "email_accounts", accounts)
    monkeypatch.setattr(routes, "api_keys", keys)
    monkeypatch.setattr(email_client, "list_folders", lambda *args, **kwargs: ["INBOX"])
    account = accounts.add(label="Home", email_address="me@home.example", imap_host="imap.home.example",
                           imap_port=993, smtp_host="smtp.home.example", smtp_port=587, username="")
    keys.save(account.id, "pw")
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), account.id


def test_mail_requests_are_limited_per_account_per_minute(client, monkeypatch):
    test_client, account_id = client
    monkeypatch.setattr(config, "mail_requests_per_minute", 5, raising=False)
    try:
        monkeypatch.setattr(routes, "mail_runner", importlib.import_module("mail_runner").MailRunner(), raising=False)
    except ModuleNotFoundError:
        pass
    answers = [test_client.get(f"/api/email/{account_id}/folders") for _ in range(6)]
    assert [answer.status_code for answer in answers] == [200] * 5 + [429]
    assert int(answers[-1].headers["Retry-After"]) > 0
    assert "5 mail requests a minute" in answers[-1].json()["detail"]


def test_the_manage_email_tool_counts_toward_the_mail_requests_a_minute(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "mail_requests_per_minute", 3)
    monkeypatch.setattr(builtin_tools, "mail_runner", importlib.import_module("mail_runner").MailRunner())
    monkeypatch.setattr(email_client, "list_messages", lambda *args, **kwargs: [])
    data_dir = str(tmp_path)
    ctx = builtin_tools.ToolContext(memory=None, documents=None, tasks=None, task_runs=None,
                                    email_accounts=EmailAccountStore(data_dir), api_keys=APIKeyManager(data_dir),
                                    notes=None, events=asyncio.Queue())
    account = ctx.email_accounts.add(label="Home", email_address="me@home.example", imap_host="imap.home.example",
                                     imap_port=993, smtp_host="smtp.home.example", smtp_port=587, username="")
    ctx.api_keys.save(account.id, "pw")
    asked = {"action": "list_messages", "account_id": account.id}
    answers = [asyncio.run(builtin_tools.call_builtin_tool("manage_email", asked, ctx)) for _ in range(4)]
    assert answers[:3] == ["[]"] * 3
    assert "3 mail requests a minute" in answers[3], answers[3]


SMTP_FLOOD_BYTES = 8 * MEBIBYTE
SMTP_FLOOD_LINE = b"250-" + b"e" * 96 + b"\r\n"
SMTP_PEAK_LIMIT_BYTES = 2 * MEBIBYTE
SMTP_LONG_LINE = b"250-" + b"l" * 5000 + b"\r\n"


class FloodingSmtpServer:
    def __init__(self, flood_greeting: bool, flood: bytes):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.greeting = flood.replace(b"250-", b"220-") + b"220 ready\r\n" if flood_greeting else b"220 ready\r\n"
        self.ehlo_reply = flood + b"250 SIZE 1000\r\n"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.server.settimeout(10)
        try:
            conn, _ = self.server.accept()
            with conn:
                self._converse(conn)
        except OSError:
            return

    def _converse(self, conn):
        conn.sendall(self.greeting)
        lines = conn.makefile("rb")
        while line := lines.readline():
            if line.upper().startswith((b"EHLO", b"HELO")):
                conn.sendall(self.ehlo_reply)
            elif line.upper().startswith(b"QUIT"):
                conn.sendall(b"221 bye\r\n")
                return
            else:
                conn.sendall(b"502 no\r\n")

    def close(self):
        self.server.close()


@pytest.fixture
def flooding_smtp():
    servers = []

    def start(flood: bytes, flood_greeting: bool = False):
        servers.append(FloodingSmtpServer(flood_greeting, flood))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def send_with_peak(server) -> tuple[BaseException | None, int, float]:
    started = time.monotonic()
    tracemalloc.start()
    try:
        email_client.send_message(local_account(server.port), "pw", "a@b.example", "s", "b", allow_private_hosts=True)
        outcome = None
    except Exception as error:
        outcome = error
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return outcome, peak, time.monotonic() - started


def flood_of(line: bytes, total_bytes: int) -> bytes:
    return line * (total_bytes // len(line))


@pytest.mark.parametrize("flood_greeting", [False, True], ids=["ehlo", "greeting"])
def test_an_smtp_server_that_floods_a_multi_line_reply_is_stopped_at_the_reply_limit(flooding_smtp, flood_greeting):
    server = flooding_smtp(flood_of(SMTP_FLOOD_LINE, SMTP_FLOOD_BYTES), flood_greeting=flood_greeting)
    outcome, peak, seconds = send_with_peak(server)
    assert "more than" in str(outcome), (outcome, peak)
    assert isinstance(outcome, email_client.SmtpReplyTooLarge), outcome
    assert email_client.describe_failure(outcome, show_details=False) == str(outcome)
    assert peak < SMTP_PEAK_LIMIT_BYTES, peak
    assert seconds < 5


def test_an_smtp_reply_line_over_the_line_limit_is_refused(flooding_smtp):
    server = flooding_smtp(SMTP_LONG_LINE)
    outcome, _, _ = send_with_peak(server)
    assert "line" in str(outcome), outcome
    assert isinstance(outcome, email_client.SmtpReplyTooLarge), outcome


def test_an_ordinary_multi_line_ehlo_reply_still_works(flooding_smtp):
    server = flooding_smtp(b"250-mail.example.test\r\n250-PIPELINING\r\n250-8BITMIME\r\n")
    outcome, _, _ = send_with_peak(server)
    assert isinstance(outcome, smtplib.SMTPNotSupportedError), outcome


UNTAGGED_FLOOD_LINES = 400_000
UNTAGGED_PEAK_LIMIT_BYTES = 32 * MEBIBYTE
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def response_type_named(number: int) -> bytes:
    name = ""
    for _ in range(6):
        number, letter = divmod(number, len(LETTERS))
        name += LETTERS[letter]
    return name.encode()


UNTAGGED_FLOOD = b"".join(b"* " + response_type_named(number) + b"\r\n" for number in range(UNTAGGED_FLOOD_LINES))


class UntaggedFloodImapServer(LocalImapServer):
    def _untagged(self, command: bytes):
        if command.startswith(b"LOGIN"):
            return [UNTAGGED_FLOOD]
        return super()._untagged(command)


def test_short_untagged_lines_of_different_types_are_charged_for_what_they_keep_in_memory(tmp_path, monkeypatch):
    server_context, client_context = local_tls_contexts(tmp_path)
    monkeypatch.setattr(email_client.ssl, "create_default_context", lambda *args, **kwargs: client_context)
    server = UntaggedFloodImapServer(server_context, 1000, honours_partial_fetch=True)
    tracemalloc.start()
    try:
        outcome = email_client.list_folders(local_account(server.port), "pw", allow_private_hosts=True)
    except imaplib.IMAP4.error as error:
        outcome = error
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        server.close()
    assert isinstance(outcome, imaplib.IMAP4.error) and "more than" in str(outcome), (outcome, peak)
    assert peak < UNTAGGED_PEAK_LIMIT_BYTES, peak
