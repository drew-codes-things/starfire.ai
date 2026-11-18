import asyncio
import os
import socket
import ssl
import sys
import threading
import time

import bcrypt
import httpx
import pytest
from fastapi import FastAPI

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import email_client
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from email_store import EmailAccount

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
PUBLIC_ADDRESS = "93.184.215.14"
SNI_NAME = "mail.example.test"


class Listener:
    def __init__(self, greeting: bytes = b"", hold_seconds: float = 3.0):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.greeting = greeting
        self.hold_seconds = hold_seconds
        self.received = b""
        self.connections = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.server.settimeout(10)
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        self.connections += 1
        with conn:
            conn.sendall(self.greeting)
            conn.settimeout(self.hold_seconds)
            try:
                self.received = conn.recv(4096)
                time.sleep(self.hold_seconds)
            except OSError:
                return

    def close(self):
        self.server.close()


@pytest.fixture
def listener():
    created = []

    def make(**options):
        created.append(Listener(**options))
        return created[-1]

    yield make
    for item in created:
        item.close()


def account(host, port, smtp_port=587):
    return EmailAccount(id="acc1", label="Home", email_address="me@home.example", imap_host=host, imap_port=port,
                        smtp_host=host, smtp_port=smtp_port, username="")


def resolving_to(monkeypatch, *addresses):
    lookups = []

    def resolve(host, port, *args, **kwargs):
        lookups.append(host)
        return [(2, 1, 6, "", (address, port)) for address in addresses]

    monkeypatch.setattr(email_client.socket, "getaddrinfo", resolve)
    return lookups


@pytest.mark.parametrize("addresses", [
    ("127.0.0.1",), ("10.1.2.3",), ("192.168.100.7",), ("100.64.0.9",), ("169.254.169.254",), ("::1",),
    ("fd00::1",), ("::ffff:127.0.0.1",), (PUBLIC_ADDRESS, "10.0.0.1"),
])
def test_a_mail_server_on_a_private_address_is_refused_before_connecting(monkeypatch, addresses):
    resolving_to(monkeypatch, *addresses)
    opened = []
    monkeypatch.setattr(email_client, "PinnedIMAP4SSL", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(email_client, "PinnedSMTP", lambda *a, **k: opened.append(a))
    with pytest.raises(email_client.MailHostRefused, match="is not a public mail server"):
        email_client.list_folders(account("imap.home.example", 993), "pw")
    with pytest.raises(email_client.MailHostRefused, match="is not a public mail server"):
        email_client.send_message(account("imap.home.example", 993), "pw", "a@b.example", "s", "b")
    assert opened == []


def test_the_address_checked_is_the_address_used_and_the_name_is_looked_up_once(monkeypatch):
    lookups = resolving_to(monkeypatch, PUBLIC_ADDRESS)
    opened = []

    def imap(host, port, **options):
        opened.append((host, port, options))
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(email_client, "PinnedIMAP4SSL", imap)
    with pytest.raises(ConnectionRefusedError):
        email_client.list_folders(account(SNI_NAME, 993), "pw")
    [(host, port, options)] = opened
    assert (host, port, options["address"], lookups) == (SNI_NAME, 993, PUBLIC_ADDRESS, [SNI_NAME])


def test_every_mail_connection_verifies_the_certificate_and_has_a_timeout(monkeypatch):
    resolving_to(monkeypatch, PUBLIC_ADDRESS)
    seen = {}

    def imap(host, port, **options):
        seen["imap"] = options
        raise ConnectionRefusedError("refused")

    class Smtp:
        def __init__(self, host, port, **options):
            seen.setdefault("smtp", []).append(options)

        def starttls(self, context=None):
            seen["starttls"] = context

        def login(self, user, password):
            raise ConnectionRefusedError("stop here")

        def quit(self):
            return None

    monkeypatch.setattr(email_client, "PinnedIMAP4SSL", imap)
    monkeypatch.setattr(email_client, "PinnedSMTP", Smtp)
    monkeypatch.setattr(email_client, "PinnedSMTPSSL", Smtp)
    with pytest.raises(ConnectionRefusedError):
        email_client.list_folders(account(SNI_NAME, 993), "pw")
    with pytest.raises(ConnectionRefusedError):
        email_client.send_message(account(SNI_NAME, 993), "pw", "a@b.example", "s", "b")
    with pytest.raises(ConnectionRefusedError):
        email_client.send_message(account(SNI_NAME, 993, smtp_port=465), "pw", "a@b.example", "s", "b")
    contexts = [seen["imap"]["ssl_context"], seen["starttls"], seen["smtp"][1]["context"]]
    for context in contexts:
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    timeouts = [seen["imap"]["timeout"], seen["smtp"][0]["timeout"], seen["smtp"][1]["timeout"]]
    assert timeouts == [email_client.MAIL_TIMEOUT_SECONDS] * 3


def test_imap_connects_to_the_pinned_address_and_still_asks_for_the_right_certificate(listener):
    server = listener(hold_seconds=0.2)
    with pytest.raises((ssl.SSLError, OSError)):
        email_client.PinnedIMAP4SSL(SNI_NAME, server.port, address="127.0.0.1",
                                    ssl_context=ssl.create_default_context(), timeout=2)
    server.thread.join(5)
    assert server.connections == 1 and SNI_NAME.encode() in server.received


def test_smtp_over_ssl_connects_to_the_pinned_address_with_the_right_name(listener):
    server = listener(hold_seconds=0.2)
    with pytest.raises((ssl.SSLError, OSError)):
        email_client.PinnedSMTPSSL(SNI_NAME, server.port, address="127.0.0.1", timeout=2,
                                   context=ssl.create_default_context())
    server.thread.join(5)
    assert server.connections == 1 and SNI_NAME.encode() in server.received


def test_plain_smtp_connects_to_the_pinned_address(listener):
    server = listener(greeting=b"220 mail ready\r\n", hold_seconds=0.2)
    connection = email_client.PinnedSMTP(SNI_NAME, server.port, address="127.0.0.1", timeout=2)
    connection.close()
    assert server.connections == 1


def test_a_mail_server_that_never_answers_gives_up_after_the_timeout(listener, monkeypatch):
    server = listener(hold_seconds=6)
    monkeypatch.setattr(email_client, "MAIL_TIMEOUT_SECONDS", 0.5)
    started = time.monotonic()
    with pytest.raises(OSError):
        email_client.list_folders(account("127.0.0.1", server.port), "pw", allow_private_hosts=True)
    assert time.monotonic() - started < 3


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)

    @application.get("/api/ping")
    async def ping():
        return {"ok": True}

    return application


async def signed_in(app, email):
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver", headers=HEADERS)
    reply = await client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert reply.status_code == 200
    return client


def new_account(host, port):
    return {"label": "Home", "email_address": "me@home.example", "imap_host": host, "imap_port": port,
            "smtp_host": "", "smtp_port": 587, "username": "", "password": "pw"}


def test_a_slow_mail_server_does_not_hold_up_everyone_else(app, listener, monkeypatch):
    server = listener(hold_seconds=2)
    monkeypatch.setattr(email_client, "MAIL_TIMEOUT_SECONDS", 1.5)

    async def scenario():
        admin = await signed_in(app, "admin@example.com")
        finished = []

        async def timed(name, request):
            await request
            finished.append(name)

        adding = asyncio.create_task(timed("mail", admin.post("/api/email/accounts", json=new_account(
            "127.0.0.1", server.port))))
        await asyncio.sleep(0.3)
        await asyncio.wait_for(timed("ping", admin.get("/api/ping")), 1)
        await adding
        return finished

    assert asyncio.run(scenario()) == ["ping", "mail"]


def test_an_ordinary_account_cannot_point_mail_at_a_private_address(app, listener):
    server = listener(hold_seconds=0.2)

    async def scenario():
        sam = await signed_in(app, "sam@example.com")
        reply = await sam.post("/api/email/accounts", json=new_account("127.0.0.1", server.port))
        return reply, (await sam.get("/api/email/accounts")).json()

    reply, listed = asyncio.run(scenario())
    assert reply.status_code == 502 and "is not a public mail server" in reply.json()["detail"]
    assert listed == {"accounts": []} and server.connections == 0


@pytest.mark.parametrize("email, expected", [
    ("sam@example.com", "could not connect: could not reach the mail server"),
    ("admin@example.com", "could not connect: [Errno 111] Connection refused"),
])
def test_only_admins_see_the_raw_connection_error(app, monkeypatch, email, expected):
    resolving_to(monkeypatch, PUBLIC_ADDRESS)

    def imap(host, port, **options):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(email_client, "PinnedIMAP4SSL", imap)

    async def scenario():
        client = await signed_in(app, email)
        return await client.post("/api/email/accounts", json=new_account("imap.example.test", 993))

    reply = asyncio.run(scenario())
    assert reply.status_code == 502 and reply.json()["detail"] == expected


def test_the_mail_servers_own_refusal_is_still_shown_to_everyone():
    from imaplib import IMAP4

    refusal = IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials")
    assert "AUTHENTICATIONFAILED" in email_client.describe_failure(refusal, show_details=False)
    assert email_client.describe_failure(IMAP4.abort("socket error: EOF"), show_details=False) == \
        email_client.MAIL_SERVER_UNREACHABLE


def test_every_mail_connection_has_a_socket_timeout_shorter_than_the_whole_operation():
    timeout = email_client.MAIL_TIMEOUT_SECONDS
    assert isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
    assert 0 < timeout < email_client.MAIL_DEADLINE_SECONDS
