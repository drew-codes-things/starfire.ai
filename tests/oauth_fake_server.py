import base64
import hashlib
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

MCP_PATH = "/mcp"
SERVED_PROTOCOL_VERSION = "2025-06-18"
ECHO_TOOL = {"name": "echo", "description": "Repeats the text it is given",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}


def s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


@dataclass
class FakeOAuthState:
    base_url: str = ""
    access_lifetime: int = 3600
    refuse_refresh: bool = False
    offer_registration: bool = True
    pkce_methods: list = field(default_factory=lambda: ["S256"])
    published_issuer: str = ""
    clients: dict = field(default_factory=dict)
    codes: dict = field(default_factory=dict)
    access_tokens: dict = field(default_factory=dict)
    refresh_tokens: dict = field(default_factory=dict)
    token_requests: list = field(default_factory=list)
    authorize_requests: list = field(default_factory=list)
    mcp_authorizations: list = field(default_factory=list)

    def revoke_access_tokens(self) -> None:
        self.access_tokens.clear()

    def grant_count(self, grant_type: str) -> int:
        return sum(1 for form in self.token_requests if form.get("grant_type") == grant_type)


def _issue_tokens(state: FakeOAuthState, client_id: str) -> dict:
    access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    state.access_tokens[access] = time.time() + state.access_lifetime
    state.refresh_tokens[refresh] = client_id
    return {"access_token": access, "token_type": "Bearer", "expires_in": state.access_lifetime,
            "refresh_token": refresh}


def build_app(state: FakeOAuthState) -> Starlette:
    def resource_metadata_url() -> str:
        return f"{state.base_url}/.well-known/oauth-protected-resource{MCP_PATH}"

    async def protected_resource(request: Request):
        return JSONResponse({"resource": f"{state.base_url}{MCP_PATH}", "authorization_servers": [state.base_url],
                             "scopes_supported": ["read"]})

    async def authorization_server(request: Request):
        metadata = {"issuer": state.published_issuer or state.base_url,
                    "authorization_endpoint": f"{state.base_url}/authorize",
                    "token_endpoint": f"{state.base_url}/token", "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": state.pkce_methods,
                    "token_endpoint_auth_methods_supported": ["none"]}
        if state.offer_registration:
            metadata["registration_endpoint"] = f"{state.base_url}/register"
        return JSONResponse(metadata)

    async def register(request: Request):
        body = await request.json()
        client_id = secrets.token_hex(8)
        state.clients[client_id] = body
        return JSONResponse({"client_id": client_id, "redirect_uris": body["redirect_uris"],
                             "token_endpoint_auth_method": "none",
                             "grant_types": ["authorization_code", "refresh_token"]}, status_code=201)

    async def authorize(request: Request):
        params = dict(request.query_params)
        state.authorize_requests.append(params)
        client = state.clients.get(params.get("client_id", ""))
        if not client or params.get("redirect_uri") not in client["redirect_uris"]:
            return JSONResponse({"error": "invalid_client"}, status_code=400)
        if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        code = secrets.token_urlsafe(16)
        state.codes[code] = params
        query = urlencode({"code": code, "state": params.get("state", ""), "iss": state.base_url})
        return RedirectResponse(f"{params['redirect_uri']}?{query}", status_code=302)

    async def token(request: Request):
        form = dict(await request.form())
        state.token_requests.append(form)
        if form.get("grant_type") == "authorization_code":
            issued = state.codes.pop(form.get("code", ""), None)
            if not issued or s256(form.get("code_verifier", "")) != issued["code_challenge"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if form.get("redirect_uri") != issued["redirect_uri"] or form.get("client_id") != issued["client_id"]:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(_issue_tokens(state, issued["client_id"]))
        if form.get("grant_type") == "refresh_token":
            client_id = state.refresh_tokens.pop(form.get("refresh_token", ""), None)
            if state.refuse_refresh or client_id is None or client_id != form.get("client_id"):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(_issue_tokens(state, client_id))
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    def is_authorized(request: Request) -> bool:
        scheme, _space, presented = request.headers.get("authorization", "").partition(" ")
        state.mcp_authorizations.append(presented)
        expires = state.access_tokens.get(presented)
        return scheme.lower() == "bearer" and expires is not None and expires > time.time()

    def answer(message: dict) -> dict | None:
        method, message_id = message.get("method"), message.get("id")
        if message_id is None:
            return None
        if method == "initialize":
            result = {"protocolVersion": SERVED_PROTOCOL_VERSION, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake-oauth-mcp", "version": "1.0.0"}}
        elif method == "tools/list":
            result = {"tools": [ECHO_TOOL]}
        elif method == "tools/call":
            text = message.get("params", {}).get("arguments", {}).get("text", "")
            result = {"content": [{"type": "text", "text": f"echo: {text}"}], "isError": False}
        else:
            return {"jsonrpc": "2.0", "id": message_id, "error": {"code": -32601, "message": f"no method {method}"}}
        return {"jsonrpc": "2.0", "id": message_id, "result": result}

    async def mcp_endpoint(request: Request):
        if not is_authorized(request):
            challenge = f'Bearer resource_metadata="{resource_metadata_url()}", scope="read"'
            return JSONResponse({"error": "invalid_token"}, status_code=401, headers={"WWW-Authenticate": challenge})
        if request.method == "GET":
            return Response(status_code=405)
        if request.method == "DELETE":
            return Response(status_code=200)
        reply = answer(await request.json())
        if reply is None:
            return Response(status_code=202)
        return JSONResponse(reply)

    return Starlette(routes=[
        Route(f"/.well-known/oauth-protected-resource{MCP_PATH}", protected_resource),
        Route("/.well-known/oauth-authorization-server", authorization_server),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize),
        Route("/token", token, methods=["POST"]),
        Route(MCP_PATH, mcp_endpoint, methods=["GET", "POST", "DELETE"]),
    ])


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class RunningFakeServer:
    def __init__(self):
        self.state = FakeOAuthState()
        port = _free_port()
        self.state.base_url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(build_app(self.state), host="127.0.0.1", port=port, log_level="warning",
                                lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def mcp_url(self) -> str:
        return f"{self.state.base_url}{MCP_PATH}"

    def __enter__(self) -> "RunningFakeServer":
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("the fake OAuth MCP server did not start within 10 seconds")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
