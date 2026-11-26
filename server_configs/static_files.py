import os
import re

from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import HTMLResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp

MIN_COMPRESSED_BYTES = 1024
HOME_PAGE = "index.html"
HOME_PATHS = frozenset({"", ".", HOME_PAGE})
ADDON_FOLDER = "local_addon"
ADDON_SLOT = re.compile(r'<template data-addon-slot="([a-z-]+)"></template>\n?')


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as page:
        return page.read()


def addon_part(directory: str, name: str) -> str:
    path = os.path.join(directory, ADDON_FOLDER, f"{name}.html")
    return read_text(path) if os.path.isfile(path) else ""


def with_addon_parts(directory: str, page: str) -> str:
    return ADDON_SLOT.sub(lambda slot: addon_part(directory, slot.group(1)), page)


class PagesWithAddons(StaticFiles):
    async def get_response(self, path: str, scope):
        if path not in HOME_PATHS:
            return await super().get_response(path, scope)
        return HTMLResponse(with_addon_parts(self.directory, read_text(os.path.join(self.directory, HOME_PAGE))))


def compressed_static(directory: str) -> ASGIApp:
    return GZipMiddleware(PagesWithAddons(directory=directory, html=True), minimum_size=MIN_COMPRESSED_BYTES)
