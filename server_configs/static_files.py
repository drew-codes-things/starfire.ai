from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp

MIN_COMPRESSED_BYTES = 1024


def compressed_static(directory: str) -> ASGIApp:
    return GZipMiddleware(StaticFiles(directory=directory, html=True), minimum_size=MIN_COMPRESSED_BYTES)
