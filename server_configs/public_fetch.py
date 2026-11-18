from __future__ import annotations

import httpcore
import httpx

from mcp_oauth import is_public_address, resolve_addresses


class NonPublicAddress(httpcore.ConnectError):
    pass


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None):
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                          socket_options=None) -> httpcore.AsyncNetworkStream:
        addresses = await resolve_addresses(host)
        if not addresses:
            raise NonPublicAddress(f"could not find an address for {host}")
        if not all(is_public_address(address) for address in addresses):
            raise NonPublicAddress(f"blocked request to non-public address: {host}")
        return await self._inner.connect_tcp(str(addresses[0]), port, timeout=timeout, local_address=local_address,
                                             socket_options=socket_options)

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise NonPublicAddress("local sockets are not reachable from a web fetch")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def public_only_transport(backend: httpcore.AsyncNetworkBackend | None = None) -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(trust_env=False)
    transport._pool = httpcore.AsyncConnectionPool(ssl_context=httpx.create_ssl_context(),
                                                   network_backend=PublicOnlyBackend(backend))
    return transport


def public_only_client(**options) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=public_only_transport(), trust_env=False, **options)
