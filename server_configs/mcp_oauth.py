from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import re
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import MISSING as MISSING_DEFAULT, asdict, dataclass, field
from typing import TypeVar
from urllib.parse import quote, urlencode, urlsplit

import httpx

from version import VERSION

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 15.0
FLOW_DEADLINE_S = 40.0
DNS_TIMEOUT_S = 5.0
MAX_RESPONSE_BYTES = 256 * 1024
PKCE_METHOD = "S256"
PKCE_VERIFIER_BYTES = 64
STATE_BYTES = 32
REFRESH_MARGIN_S = 60
PENDING_LIFETIME_S = 600
MAX_PENDING_SIGN_INS = 32
CLIENT_NAME = "starfire.ai"
CALLBACK_PATH = "/api/mcp/servers/sign-in/callback"
PROTECTED_RESOURCE_WELL_KNOWN = "/.well-known/oauth-protected-resource"
AUTH_SERVER_WELL_KNOWN = "/.well-known/oauth-authorization-server"
OPENID_WELL_KNOWN = "/.well-known/openid-configuration"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
LOCAL_NAME_SUFFIX = ".localhost"
PROBE_PROTOCOL_VERSION = "2025-06-18"
MCP_ACCEPT = "application/json, text/event-stream"
SECRET_AUTH_METHODS = ("client_secret_post", "client_secret_basic")
USABLE_AUTH_METHODS = ("none", *SECRET_AUTH_METHODS)
SIGN_IN_REFUSED_STATUSES = frozenset({400, 401, 403})
INVALID_CLIENT = "invalid_client"
OAUTH_ERROR_CODE = re.compile(r"[a-z_]{1,64}")
CHALLENGE_PARAM = r'{name}\s*=\s*(?:"([^"]*)"|([^\s,]+))'
ISS_SUPPORTED_FIELD = "authorization_response_iss_parameter_supported"
REQUIRED_RECORD_TEXT = ("issuer", "authorization_endpoint", "token_endpoint", "resource", "client_id")

Result = TypeVar("Result")


class McpSignInError(Exception):
    pass


class SignInNeeded(McpSignInError):
    def __init__(self, message: str, client_rejected: bool = False):
        super().__init__(message)
        self.client_rejected = client_rejected


class ResponseTooLarge(McpSignInError):
    pass


class SignInRejected(McpSignInError):
    pass


@dataclass
class AuthorizationServer:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    resource: str
    scope: str
    iss_required: bool = False


@dataclass
class ClientRegistration:
    client_id: str
    client_secret: str = ""
    auth_method: str = "none"
    redirect_uri: str = ""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0

    def is_fresh(self, now: float) -> bool:
        return bool(self.access_token) and (not self.expires_at or now < self.expires_at - REFRESH_MARGIN_S)


@dataclass
class OAuthGrant:
    server: AuthorizationServer
    registration: ClientRegistration
    tokens: TokenSet

    def to_record(self) -> dict:
        return {**asdict(self.server), **asdict(self.registration), **asdict(self.tokens)}

    @classmethod
    def from_record(cls, record) -> "OAuthGrant":
        if not isinstance(record, dict):
            raise SignInNeeded("the saved sign-in is damaged, so it needs signing in again")
        _check_record(record)

        def pick(kind) -> dict:
            return {name: record[name] for name in kind.__dataclass_fields__ if name in record}
        return cls(AuthorizationServer(**pick(AuthorizationServer)), ClientRegistration(**pick(ClientRegistration)),
                   TokenSet(**pick(TokenSet)))


def _expected_type(kind, name: str) -> type | tuple:
    annotation = kind.__dataclass_fields__[name].type
    return {"str": str, "bool": bool, "float": (int, float)}[annotation]


def _has_expected_type(value, expected) -> bool:
    is_bool_for_number = isinstance(value, bool) and expected != bool
    return isinstance(value, expected) and not is_bool_for_number


def _check_record(record: dict) -> None:
    for kind in (AuthorizationServer, ClientRegistration, TokenSet):
        for name, spec in kind.__dataclass_fields__.items():
            has_default = spec.default is not MISSING_DEFAULT
            if name not in record and has_default:
                continue
            if name not in record or not _has_expected_type(record[name], _expected_type(kind, name)):
                raise SignInNeeded(f"the saved sign-in is damaged ({name}), so it needs signing in again")
    if not all(record[name] for name in REQUIRED_RECORD_TEXT):
        raise SignInNeeded("the saved sign-in is incomplete, so it needs signing in again")


@dataclass
class Discovery:
    server: AuthorizationServer
    evidence: list = field(default_factory=list)


def is_allowed_endpoint(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if not parts.hostname:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS)


def _ip_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _normalised_host(host: str) -> str:
    return host.lower().rstrip(".")


def _is_local_name(host: str) -> bool:
    return host == "localhost" or host.endswith(LOCAL_NAME_SUFFIX)


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = _normalised_host(host)
    address = _ip_address(host)
    return _is_local_name(host) or (address is not None and address.is_loopback)


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


async def resolve_addresses(host: str) -> list:
    loop = asyncio.get_running_loop()
    try:
        answers = await asyncio.wait_for(loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), DNS_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError):
        return []
    addresses = [_ip_address(str(answer[4][0])) for answer in answers]
    return [address for address in addresses if address is not None]


@dataclass(frozen=True)
class EndpointRules:
    allows_local: bool

    @classmethod
    def for_server(cls, server_url: str) -> "EndpointRules":
        try:
            host = urlsplit(server_url).hostname
        except ValueError:
            host = None
        return cls(allows_local=is_loopback_host(host))

    def permits(self, url) -> bool:
        if not isinstance(url, str):
            return False
        if self.allows_local:
            return is_allowed_endpoint(url)
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme != "https" or not parts.hostname:
            return False
        host = _normalised_host(parts.hostname)
        address = _ip_address(host)
        return not _is_local_name(host) and (address is None or is_public_address(address))

    async def checked(self, url, what: str) -> str:
        if not self.permits(url):
            raise McpSignInError(f"the service published an unusable {what} ({url!r}); "
                                 f"{self.requirement()}")
        host = urlsplit(url).hostname
        if self.allows_local or _ip_address(host) is not None:
            return url
        addresses = await resolve_addresses(host)
        if not addresses:
            raise McpSignInError(f"could not look up the {what} host {host}")
        if not all(is_public_address(address) for address in addresses):
            raise McpSignInError(f"the {what} host {host} points at a private or local network address, "
                                 "which starfire only uses for an MCP server on this computer")
        return url

    def requirement(self) -> str:
        if self.allows_local:
            return "it must be an https address or an http address on this computer"
        return "it must be a public https address"


def _rules_for(server: AuthorizationServer) -> EndpointRules:
    return EndpointRules.for_server(server.resource)


def _require_endpoint(url, what: str, rules: EndpointRules) -> str:
    if not rules.permits(url):
        raise McpSignInError(f"the service published an unusable {what} ({url!r}); {rules.requirement()}")
    return url


async def within_deadline(work: Awaitable[Result], what: str) -> Result:
    try:
        return await asyncio.wait_for(work, FLOW_DEADLINE_S)
    except asyncio.TimeoutError as error:
        raise McpSignInError(f"{what} took longer than {FLOW_DEADLINE_S:g} seconds") from error


@dataclass
class BoundedAnswer:
    status_code: int
    headers: httpx.Headers
    body: bytes

    def json(self):
        return json.loads(self.body)


async def bounded_request(client: httpx.AsyncClient, method: str, url: str, **options) -> BoundedAnswer:
    async with client.stream(method, url, **options) as response:
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ResponseTooLarge(f"the answer from {_origin(url)} was larger than "
                                       f"{MAX_RESPONSE_BYTES // 1024} KiB")
        return BoundedAnswer(response.status_code, response.headers, bytes(body))


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _without_trailing_slash(url: str) -> str:
    return url[:-1] if url.endswith("/") else url


def challenge_param(header: str, name: str) -> str:
    found = re.search(CHALLENGE_PARAM.format(name=re.escape(name)), header or "")
    if not found:
        return ""
    return found.group(1) if found.group(1) is not None else found.group(2)


def protected_resource_urls(server_url: str, from_challenge: str) -> list[str]:
    parts = urlsplit(server_url)
    path = parts.path.rstrip("/")
    candidates = [from_challenge] if from_challenge else []
    if path:
        candidates.append(f"{_origin(server_url)}{PROTECTED_RESOURCE_WELL_KNOWN}{path}")
    candidates.append(f"{_origin(server_url)}{PROTECTED_RESOURCE_WELL_KNOWN}")
    return candidates


def authorization_server_urls(issuer: str) -> list[str]:
    parts = urlsplit(issuer)
    origin, path = _origin(issuer), parts.path.rstrip("/")
    if not path:
        return [f"{origin}{AUTH_SERVER_WELL_KNOWN}", f"{origin}{OPENID_WELL_KNOWN}"]
    return [f"{origin}{AUTH_SERVER_WELL_KNOWN}{path}", f"{origin}{OPENID_WELL_KNOWN}{path}",
            f"{origin}{path}{OPENID_WELL_KNOWN}"]


def resource_covers(resource: str, server_url: str) -> bool:
    resource_parts, server_parts = urlsplit(resource), urlsplit(server_url)
    same_origin = (resource_parts.scheme, resource_parts.netloc.lower()) == (server_parts.scheme,
                                                                            server_parts.netloc.lower())
    resource_path = resource_parts.path.rstrip("/")
    server_path = server_parts.path.rstrip("/")
    return same_origin and (server_path == resource_path or server_path.startswith(resource_path + "/"))


async def _probe_challenge(client: httpx.AsyncClient, server_url: str, evidence: list) -> tuple[str, str]:
    body = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": PROBE_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": CLIENT_NAME, "version": VERSION}}}
    try:
        async with client.stream("POST", server_url, json=body, headers={"Accept": MCP_ACCEPT}) as response:
            status, answered = response.status_code, response.is_success
            challenge = response.headers.get("www-authenticate", "")
    except httpx.HTTPError as error:
        raise McpSignInError(f"could not reach {_origin(server_url)}: {type(error).__name__}") from error
    evidence.append({"step": "mcp", "url": server_url, "status": status, "www_authenticate": bool(challenge)})
    if answered:
        raise McpSignInError("this server answered without asking for a sign-in; add it as a plain URL instead")
    return challenge_param(challenge, "resource_metadata"), challenge_param(challenge, "scope")


async def _first_json(client: httpx.AsyncClient, urls: list[str], step: str, evidence: list,
                      rules: EndpointRules | None = None) -> dict | None:
    rules = rules or EndpointRules(allows_local=False)
    for url in urls:
        try:
            await rules.checked(url, "metadata address")
        except McpSignInError as error:
            evidence.append({"step": step, "url": url, "status": f"skipped, {error}"})
            continue
        try:
            response = await bounded_request(client, "GET", url, headers={"Accept": "application/json"})
        except ResponseTooLarge:
            evidence.append({"step": step, "url": url, "status": "skipped, answer too large"})
            continue
        except httpx.HTTPError as error:
            evidence.append({"step": step, "url": url, "status": type(error).__name__})
            continue
        evidence.append({"step": step, "url": url, "status": response.status_code})
        if response.status_code != 200:
            continue
        try:
            document = response.json()
        except ValueError:
            continue
        if isinstance(document, dict):
            return document
    return None


def _scope_from(challenge_scope: str, resource_metadata: dict | None) -> str:
    if challenge_scope:
        return challenge_scope
    supported = (resource_metadata or {}).get("scopes_supported")
    if isinstance(supported, list) and all(isinstance(scope, str) for scope in supported):
        return " ".join(supported)
    return ""


def _checked_metadata(metadata: dict, issuer: str) -> None:
    published_issuer = metadata.get("issuer")
    if not isinstance(published_issuer, str) or _without_trailing_slash(published_issuer) != \
            _without_trailing_slash(issuer):
        raise McpSignInError(f"the sign-in server's metadata names issuer {published_issuer!r}, expected {issuer!r}")
    methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or PKCE_METHOD not in methods:
        raise McpSignInError("the sign-in server does not offer PKCE with S256, so starfire will not use it")


async def discover(client: httpx.AsyncClient, server_url: str) -> Discovery:
    rules = EndpointRules.for_server(server_url)
    evidence: list = []
    metadata_url, challenge_scope = await _probe_challenge(client, server_url, evidence)
    resource_metadata = await _first_json(client, protected_resource_urls(server_url, metadata_url),
                                          "protected_resource", evidence, rules)
    if resource_metadata:
        servers = resource_metadata.get("authorization_servers")
        if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
            raise McpSignInError("the server's protected resource metadata names no sign-in server")
        issuer = _require_endpoint(servers[0], "sign-in server address", rules)
        resource = resource_metadata.get("resource") or server_url
        if not isinstance(resource, str) or not resource_covers(resource, server_url):
            raise McpSignInError(f"the server's metadata is for {resource!r}, not for {server_url}")
    else:
        issuer, resource = _origin(server_url), server_url
    metadata = await _first_json(client, authorization_server_urls(issuer), "authorization_server", evidence, rules)
    if not metadata:
        raise McpSignInError(f"found no OAuth metadata for the sign-in server at {issuer}")
    _checked_metadata(metadata, issuer)
    registration_endpoint = metadata.get("registration_endpoint") or ""
    if registration_endpoint:
        _require_endpoint(registration_endpoint, "registration address", rules)
    server = AuthorizationServer(
        issuer=_without_trailing_slash(metadata["issuer"]),
        authorization_endpoint=_require_endpoint(metadata.get("authorization_endpoint"), "authorization address",
                                                 rules),
        token_endpoint=_require_endpoint(metadata.get("token_endpoint"), "token address", rules),
        registration_endpoint=registration_endpoint, resource=resource,
        scope=_scope_from(challenge_scope, resource_metadata),
        iss_required=metadata.get(ISS_SUPPORTED_FIELD) is True)
    return Discovery(server=server, evidence=evidence)


def _oauth_error_code(response: BoundedAnswer) -> str:
    try:
        code = response.json().get("error", "")
    except (ValueError, AttributeError):
        return ""
    return code if isinstance(code, str) and OAUTH_ERROR_CODE.fullmatch(code) else ""


def _refusal(what: str, response: BoundedAnswer) -> str:
    code = _oauth_error_code(response)
    return f"{what} was refused ({response.status_code}{', ' + code if code else ''})"


async def register_client(client: httpx.AsyncClient, server: AuthorizationServer,
                          redirect_uri: str) -> ClientRegistration:
    if not server.registration_endpoint:
        raise McpSignInError("this service does not let apps register themselves, so it needs an OAuth app "
                             "set up by hand; use a token instead")
    body = {"client_name": CLIENT_NAME, "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}
    if server.scope:
        body["scope"] = server.scope
    await _rules_for(server).checked(server.registration_endpoint, "registration address")
    try:
        response = await bounded_request(client, "POST", server.registration_endpoint, json=body,
                                         headers={"Accept": "application/json"})
    except httpx.HTTPError as error:
        raise McpSignInError(f"could not reach the registration address: {type(error).__name__}") from error
    if response.status_code not in (200, 201):
        raise McpSignInError(_refusal("registering starfire with the service", response))
    try:
        registered = response.json()
    except ValueError as error:
        raise McpSignInError("the service's registration answer was not JSON") from error
    client_id = registered.get("client_id") if isinstance(registered, dict) else None
    if not isinstance(client_id, str) or not client_id:
        raise McpSignInError("the service's registration answer had no client_id")
    auth_method = registered.get("token_endpoint_auth_method") or "none"
    client_secret = registered.get("client_secret") or ""
    if auth_method not in USABLE_AUTH_METHODS or (auth_method in SECRET_AUTH_METHODS and not client_secret):
        raise McpSignInError(f"the service registered starfire for {auth_method!r}, which starfire cannot use")
    return ClientRegistration(client_id=client_id, client_secret=client_secret, auth_method=auth_method,
                              redirect_uri=redirect_uri)


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def new_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(PKCE_VERIFIER_BYTES)
    return verifier, pkce_challenge(verifier)


def authorization_url(server: AuthorizationServer, registration: ClientRegistration, state: str,
                      challenge: str) -> str:
    params = {"response_type": "code", "client_id": registration.client_id,
              "redirect_uri": registration.redirect_uri, "state": state, "code_challenge": challenge,
              "code_challenge_method": PKCE_METHOD, "resource": server.resource}
    if server.scope:
        params["scope"] = server.scope
    joiner = "&" if urlsplit(server.authorization_endpoint).query else "?"
    return f"{server.authorization_endpoint}{joiner}{urlencode(params)}"


def _client_auth(registration: ClientRegistration, form: dict) -> dict:
    headers = {"Accept": "application/json"}
    form["client_id"] = registration.client_id
    if registration.auth_method == "client_secret_basic":
        pair = f"{quote(registration.client_id, safe='')}:{quote(registration.client_secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
        del form["client_id"]
    elif registration.auth_method == "client_secret_post":
        form["client_secret"] = registration.client_secret
    return headers


def _token_set(response: BoundedAnswer, previous_refresh: str, now: float) -> TokenSet:
    try:
        answer = response.json()
    except ValueError as error:
        raise McpSignInError("the service's token answer was not JSON") from error
    access_token = answer.get("access_token") if isinstance(answer, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise McpSignInError("the service's token answer had no access token")
    token_type = answer.get("token_type", "bearer")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise McpSignInError(f"the service issued a {token_type!r} token, starfire needs a bearer token")
    expires_in = answer.get("expires_in")
    expires_at = now + float(expires_in) if isinstance(expires_in, (int, float)) and expires_in > 0 else 0.0
    refresh_token = answer.get("refresh_token")
    return TokenSet(access_token=access_token,
                    refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else previous_refresh,
                    expires_at=expires_at)


async def _token_request(client: httpx.AsyncClient, grant: OAuthGrant, form: dict, what: str,
                         clock: Callable[[], float]) -> TokenSet:
    headers = _client_auth(grant.registration, form)
    await _rules_for(grant.server).checked(grant.server.token_endpoint, "token address")
    try:
        response = await bounded_request(client, "POST", grant.server.token_endpoint, data=form, headers=headers)
    except httpx.HTTPError as error:
        raise McpSignInError(f"could not reach the token address: {type(error).__name__}") from error
    if response.status_code in SIGN_IN_REFUSED_STATUSES:
        raise SignInNeeded(_refusal(what, response), client_rejected=_oauth_error_code(response) == INVALID_CLIENT)
    if response.status_code != 200:
        raise McpSignInError(_refusal(what, response))
    return _token_set(response, grant.tokens.refresh_token, clock())


async def exchange_code(client: httpx.AsyncClient, grant: OAuthGrant, code: str, verifier: str,
                        clock: Callable[[], float] = time.time) -> TokenSet:
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": grant.registration.redirect_uri,
            "code_verifier": verifier, "resource": grant.server.resource}
    return await _token_request(client, grant, form, "exchanging the sign-in code", clock)


async def refresh_tokens(client: httpx.AsyncClient, grant: OAuthGrant,
                         clock: Callable[[], float] = time.time) -> TokenSet:
    if not grant.tokens.refresh_token:
        raise SignInNeeded("the service gave no refresh token, so the sign-in cannot be renewed")
    form = {"grant_type": "refresh_token", "refresh_token": grant.tokens.refresh_token,
            "resource": grant.server.resource}
    if grant.server.scope:
        form["scope"] = grant.server.scope
    return await _token_request(client, grant, form, "renewing the sign-in", clock)


def session_digest(session_token: str) -> str:
    return hashlib.sha256(session_token.encode()).hexdigest()


@dataclass
class PendingSignIn:
    user_id: str
    session_digest: str
    verifier: str
    server_url: str
    name: str
    service: str
    replaces_server_id: str
    grant: OAuthGrant
    created: float = 0.0


class PendingSignIns:
    def __init__(self, clock: Callable[[], float] = time.time, lifetime: float = PENDING_LIFETIME_S,
                 limit: int = MAX_PENDING_SIGN_INS):
        self._clock = clock
        self._lifetime = lifetime
        self._limit = limit
        self._items: dict[str, PendingSignIn] = {}

    def _is_expired(self, pending: PendingSignIn) -> bool:
        return self._clock() - pending.created > self._lifetime

    def _prune(self) -> None:
        self._items = {state: pending for state, pending in self._items.items() if not self._is_expired(pending)}
        while len(self._items) >= self._limit:
            self._items.pop(next(iter(self._items)))

    def add(self, pending: PendingSignIn) -> str:
        self._prune()
        state = secrets.token_urlsafe(STATE_BYTES)
        pending.created = self._clock()
        self._items[state] = pending
        return state

    def take(self, state: str, user_id: str, digest: str) -> PendingSignIn:
        pending = self._items.pop(state, None) if state else None
        if pending is None:
            raise SignInRejected("this sign-in is unknown or was already used; start it again from Settings")
        if self._is_expired(pending):
            raise SignInRejected("this sign-in took too long; start it again from Settings")
        same_session = secrets.compare_digest(pending.session_digest, digest)
        if pending.user_id != user_id or not same_session:
            raise SignInRejected("this sign-in was started from a different starfire session")
        return pending

    def __len__(self) -> int:
        return len(self._items)


class RefreshingBearerAuth(httpx.Auth):
    def __init__(self, grant: OAuthGrant, on_refreshed: Callable[[TokenSet], None],
                 on_sign_in_needed: Callable[[SignInNeeded], None], clock: Callable[[], float] = time.time):
        self.grant = grant
        self._on_refreshed = on_refreshed
        self._on_sign_in_needed = on_sign_in_needed
        self._clock = clock
        self._lock = asyncio.Lock()

    async def _refreshed_tokens(self) -> TokenSet:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            return await refresh_tokens(client, self.grant, self._clock)

    async def _renewed(self, stale_token: str) -> bool:
        async with self._lock:
            if self.grant.tokens.access_token != stale_token:
                return True
            try:
                renewed = await within_deadline(self._refreshed_tokens(), "renewing the sign-in")
            except SignInNeeded as error:
                self._on_sign_in_needed(error)
                raise
            except McpSignInError as error:
                logger.warning("could not renew an MCP sign-in, this request fails and the next one tries "
                               "again: %s", error)
                return False
            self.grant.tokens = renewed
            self._on_refreshed(renewed)
            return True

    async def async_auth_flow(self, request: httpx.Request):
        renewal_worked = True
        if not self.grant.tokens.is_fresh(self._clock()):
            renewal_worked = await self._renewed(self.grant.tokens.access_token)
        sent_token = self.grant.tokens.access_token
        request.headers["Authorization"] = f"Bearer {sent_token}"
        response = yield request
        if response.status_code != 401 or not renewal_worked:
            return
        if not await self._renewed(sent_token):
            return
        request.headers["Authorization"] = f"Bearer {self.grant.tokens.access_token}"
        yield request
