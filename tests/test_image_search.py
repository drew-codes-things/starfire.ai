import asyncio
import os
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import image_search
from image_search import ImageSearchError

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def fake_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://duckduckgo.com")


def test_search_parses_duckduckgo_results():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<script>vqd="4-123456789";</script>')
        assert request.url.params["vqd"] == "4-123456789"
        return httpx.Response(200, json={"results": [
            {"title": "A cat", "image": "https://img.example/cat.jpg", "url": "https://example.com/cats", "width": 10, "height": 5},
            {"title": "no image here"},
        ]})

    async def run():
        async with fake_client(handler) as client:
            return await image_search.search("cat", client=client)

    results = asyncio.run(run())
    assert results == [{"title": "A cat", "image_url": "https://img.example/cat.jpg", "thumbnail": "",
                        "source": "https://example.com/cats", "width": 10, "height": 5}]


def test_search_reports_missing_token():
    async def run():
        async with fake_client(lambda request: httpx.Response(200, text="<html>no token</html>")) as client:
            await image_search.search("cat", client=client)

    with pytest.raises(ImageSearchError):
        asyncio.run(run())


def test_download_accepts_images_and_rejects_other_things(monkeypatch):
    monkeypatch.setattr(image_search, "_resolves_to_public_address", lambda url: True)

    def handler(request):
        if request.url.path == "/cat.png":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if request.url.path == "/page.html":
            return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"x" * (image_search.MAX_IMAGE_BYTES + 1),
                              headers={"content-type": "image/jpeg"})

    async def run(path):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await image_search.download("https://img.example" + path, client=client)

    data, content_type = asyncio.run(run("/cat.png"))
    assert data == PNG and content_type == "image/png"
    with pytest.raises(ImageSearchError):
        asyncio.run(run("/page.html"))
    with pytest.raises(ImageSearchError):
        asyncio.run(run("/huge.jpg"))


def test_download_refuses_private_addresses():
    with pytest.raises(ImageSearchError):
        asyncio.run(image_search.download("http://127.0.0.1/secret.png"))
    with pytest.raises(ImageSearchError):
        asyncio.run(image_search.download("file:///etc/passwd"))
