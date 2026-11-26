from __future__ import annotations

from fastapi import FastAPI, Request

SERVER_RECEIVE = "starfire.server_receive"
WATCHED_PREFIX = "/v1/"
DISCONNECT = "http.disconnect"


class KeepServerReceive:

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith(WATCHED_PREFIX):
            scope[SERVER_RECEIVE] = receive
        await self.app(scope, receive, send)


def server_receive(request: Request):
    return request.scope.get(SERVER_RECEIVE) or request.receive


async def caller_hangs_up(request: Request) -> None:
    receive = server_receive(request)
    while (await receive())["type"] != DISCONNECT:
        continue


def install(app: FastAPI) -> None:
    app.add_middleware(KeepServerReceive)
