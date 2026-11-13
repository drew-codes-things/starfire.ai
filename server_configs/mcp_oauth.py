from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from urllib.parse import quote, urlencode, urlsplit

import httpx

from version import VERSION

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_S = 15.0
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
PROBE_PROTOCOL_VERSION = "2025-06-18"
MCP_ACCEPT = "application/json, text/event-stream"
SECRET_AUTH_METHODS = ("client_secret_post", "client_secret_basic")
USABLE_AUTH_METHODS = ("none", *SECRET_AUTH_METHODS)
SIGN_IN_REFUSED_STATUSES = frozenset({400, 401, 403})
OAUTH_ERROR_CODE = re.compile(r"[a-z_]{1,64}")
CHALLENGE_PARAM = r'{name}\s*=\s*(?:"([^"]*)"|([^\s,]+))'


class McpSignInError(Exception):
    pass


class SignInNeeded(McpSignInError):
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
    def from_record(cls, record: dict) -> "OAuthGrant":
        def pick(kind) -> dict:
            return {name: record[name] for name in kind.__dataclass_fields__ if name in record}
        return cls(AuthorizationServer(**pick(AuthorizationServer)), ClientRegistration(**pick(ClientRegistration)),
                   TokenSet(**pick(TokenSet)))


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


def _require_endpoint(url, what: str) -> str:
    if not isinstance(url, str) or not is_allowed_endpoint(url):
        raise McpSignInError(f"the service published an unusable {what} ({url!r}); it must be an https address")
    return url


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
        response = await client.post(server_url, json=body, headers={"Accept": MCP_ACCEPT})
    except httpx.HTTPError as error:
        raise McpSignInError(f"could not reach {_origin(server_url)}: {type(error).__name__}") from error
    challenge = response.headers.get("www-authenticate", "")
    evidence.append({"step": "mcp", "url": server_url, "status": response.status_code,
                     "www_authenticate": bool(challenge)})
    if response.is_success:
        raise McpSignInError("this server answered without asking for a sign-in; add it as a plain URL instead")
    return challenge_param(challenge, "resource_metadata"), challenge_param(challenge, "scope")


async def _first_json(client: httpx.AsyncClient, urls: list[str], step: str, evidence: list) -> dict | None:
    for url in urls:
        if not is_allowed_endpoint(url):
            evidence.append({"step": step, "url": url, "status": "skipped, not https"})
            continue
        try:
            response = await client.get(url, headers={"Accept": "application/json"})
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
    evidence: list = []
    metadata_url, challenge_scope = await _probe_challenge(client, server_url, evidence)
    resource_metadata = await _first_json(client, protected_resource_urls(server_url, metadata_url),
                                          "protected_resource", evidence)
    if resource_metadata:
        servers = resource_metadata.get("authorization_servers")
        if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
            raise McpSignInError("the server's protected resource metadata names no sign-in server")
        issuer = _require_endpoint(servers[0], "sign-in server address")
        resource = resource_metadata.get("resource") or server_url
        if not isinstance(resource, str) or not resource_covers(resource, server_url):
            raise McpSignInError(f"the server's metadata is for {resource!r}, not for {server_url}")
    else:
        issuer, resource = _origin(server_url), server_url
    metadata = await _first_json(client, authorization_server_urls(issuer), "authorization_server", evidence)
    if not metadata:
        raise McpSignInError(f"found no OAuth metadata for the sign-in server at {issuer}")
    _checked_metadata(metadata, issuer)
    registration_endpoint = metadata.get("registration_endpoint") or ""
    if registration_endpoint:
        _require_endpoint(registration_endpoint, "registration address")
    server = AuthorizationServer(
        issuer=_without_trailing_slash(metadata["issuer"]),
        authorization_endpoint=_require_endpoint(metadata.get("authorization_endpoint"), "authorization address"),
        token_endpoint=_require_endpoint(metadata.get("token_endpoint"), "token address"),
        registration_endpoint=registration_endpoint, resource=resource,
        scope=_scope_from(challenge_scope, resource_metadata))
    return Discovery(server=server, evidence=evidence)


def _oauth_error_code(response: httpx.Response) -> str:
    try:
        code = response.json().get("error", "")
    except (ValueError, AttributeError):
        return ""
    return code if isinstance(code, str) and OAUTH_ERROR_CODE.fullmatch(code) else ""


def _refusal(what: str, response: httpx.Response) -> str:
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
    try:
        response = await client.post(server.registration_endpoint, json=body, headers={"Accept": "application/json"})
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


def _token_set(response: httpx.Response, previous_refresh: str, now: float) -> TokenSet:
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
    try:
        response = await client.post(grant.server.token_endpoint, data=form, headers=headers)
    except httpx.HTTPError as error:
        raise McpSignInError(f"could not reach the token address: {type(error).__name__}") from error
    if response.status_code in SIGN_IN_REFUSED_STATUSES:
        raise SignInNeeded(_refusal(what, response))
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
                 on_sign_in_needed: Callable[[str], None], clock: Callable[[], float] = time.time):
        self.grant = grant
        self._on_refreshed = on_refreshed
        self._on_sign_in_needed = on_sign_in_needed
        self._clock = clock
        self._lock = asyncio.Lock()

    async def _renew(self, stale_token: str) -> None:
        async with self._lock:
            if self.grant.tokens.access_token != stale_token:
                return
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
                    renewed = await refresh_tokens(client, self.grant, self._clock)
            except SignInNeeded as error:
                self._on_sign_in_needed(str(error))
                raise
            self.grant.tokens = renewed
            self._on_refreshed(renewed)

    async def async_auth_flow(self, request: httpx.Request):
        if not self.grant.tokens.is_fresh(self._clock()):
            await self._renew(self.grant.tokens.access_token)
        sent_token = self.grant.tokens.access_token
        request.headers["Authorization"] = f"Bearer {sent_token}"
        response = yield request
        if response.status_code != 401:
            return
        await self._renew(sent_token)
        request.headers["Authorization"] = f"Bearer {self.grant.tokens.access_token}"
        yield request
