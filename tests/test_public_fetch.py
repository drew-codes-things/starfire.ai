import asyncio
import ipaddress
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import deep_research

NOT_PUBLIC = ["100.64.0.1", "100.100.100.200", "127.0.0.1", "10.0.0.5", "192.168.0.10", "169.254.169.254",
              "::ffff:127.0.0.1", "fd00::1", "0.0.0.0"]


def answers(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]


@pytest.mark.parametrize("address", NOT_PUBLIC)
def test_research_refuses_every_address_that_is_not_on_the_public_internet(monkeypatch, address):
    monkeypatch.setattr(deep_research.socket, "getaddrinfo", lambda host, port: answers(address))
    assert deep_research._resolves_to_public_address("https://looks-fine.example/page") is False


def test_research_still_reaches_a_public_address(monkeypatch):
    monkeypatch.setattr(deep_research.socket, "getaddrinfo", lambda host, port: answers("93.184.216.34"))
    assert deep_research._resolves_to_public_address("https://example.com/") is True


class RecordingBackend:
    def __init__(self):
        self.hosts = []

    async def connect_tcp(self, host, port, **options):
        self.hosts.append(host)
        return "stream"


def resolving_to(monkeypatch, *addresses):
    import public_fetch

    async def resolve(host):
        return [ipaddress.ip_address(address) for address in addresses]

    monkeypatch.setattr(public_fetch, "resolve_addresses", resolve)
    return public_fetch


def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    public_fetch = resolving_to(monkeypatch, "93.184.216.34")
    inner = RecordingBackend()
    asyncio.run(public_fetch.PublicOnlyBackend(inner).connect_tcp("example.com", 443))
    assert inner.hosts == ["93.184.216.34"]


@pytest.mark.parametrize("addresses", [("100.64.0.1",), ("93.184.216.34", "127.0.0.1"), ()])
def test_a_name_that_points_anywhere_private_never_gets_a_connection(monkeypatch, addresses):
    public_fetch = resolving_to(monkeypatch, *addresses)
    inner = RecordingBackend()
    with pytest.raises(public_fetch.NonPublicAddress):
        asyncio.run(public_fetch.PublicOnlyBackend(inner).connect_tcp("rebinding.example", 80))
    assert inner.hosts == []


def test_a_client_request_to_a_private_name_fails_before_anything_is_sent(monkeypatch):
    public_fetch = resolving_to(monkeypatch, "127.0.0.1")

    async def fetch():
        async with public_fetch.public_only_client() as client:
            await client.get("http://rebinding.example/")

    with pytest.raises(httpx.ConnectError, match="non-public"):
        asyncio.run(fetch())


def test_a_pinned_request_keeps_the_name_in_its_host_header(monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers["Host"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    public_fetch = resolving_to(monkeypatch, "127.0.0.1")
    monkeypatch.setattr(public_fetch, "is_public_address", lambda address: True)

    async def fetch():
        async with public_fetch.public_only_client() as client:
            return await client.get(f"http://pinned.example:{server.server_port}/")

    try:
        assert asyncio.run(fetch()).text == "ok"
    finally:
        server.shutdown()
    assert seen == [f"pinned.example:{server.server_port}"]
