import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

from mcp_servers_store import SECRET_REMOVED, without_arg_credentials, without_url_credentials

GONE = SECRET_REMOVED


@pytest.mark.parametrize("url, expected", [
    ("https://mcp.example.test/mcp", "https://mcp.example.test/mcp"),
    ("https://u:pw@h.example.test/x", f"https://u:{GONE}@h.example.test/x"),
    ("postgresql://reader@db.example.test/x", "postgresql://reader@db.example.test/x"),
    ("https://h.example.test/sse?api_key=k&mode=fast", f"https://h.example.test/sse?api_key={GONE}&mode=fast"),
    ("https://h.example.test/?accessToken=t&Signature=s&page=2", f"https://h.example.test/?accessToken={GONE}&Signature={GONE}&page=2"),
    ("https://h.example.test/?client_secret=s&PASSWORD=p&auth=a", f"https://h.example.test/?client_secret={GONE}&PASSWORD={GONE}&auth={GONE}"),
    ("https://h.example.test/?author=me&keyboard=uk&monkey=1", "https://h.example.test/?author=me&keyboard=uk&monkey=1"),
    ("https://h.example.test/cb#access_token=t&state=1", f"https://h.example.test/cb#access_token={GONE}&state=1"),
    ("https://h.example.test/?token=", "https://h.example.test/?token="),
    ("http://[not-an-ipv6/x?token=t", GONE),
    ("", ""),
])
def test_url_credentials_are_replaced_and_everything_else_kept(url, expected):
    assert without_url_credentials(url) == expected


@pytest.mark.parametrize("args, expected", [
    (["-y", "@modelcontextprotocol/server-github"], ["-y", "@modelcontextprotocol/server-github"]),
    (["--api-key", "k", "--port", "3000"], ["--api-key", GONE, "--port", "3000"]),
    (["--token=t", "--password", "p", "-k", "x"], [f"--token={GONE}", "--password", GONE, "-k", "x"]),
    (["--no-auth", "--port", "3000"], ["--no-auth", "--port", "3000"]),
    (["--repository", "/home/me/repo", "--db-path", "C:\\data\\x.db"], ["--repository", "/home/me/repo", "--db-path", "C:\\data\\x.db"]),
    (["-e", "GITHUB_PERSONAL_ACCESS_TOKEN=t", "-e", "LOG_LEVEL=info"], ["-e", f"GITHUB_PERSONAL_ACCESS_TOKEN={GONE}", "-e", "LOG_LEVEL=info"]),
    (["--header", "Authorization: Bearer t", "--header", "Accept: text/plain"], ["--header", f"Authorization: {GONE}", "--header", "Accept: text/plain"]),
    (["--url=https://u:pw@h.example.test/x"], [f"--url=https://u:{GONE}@h.example.test/x"]),
    (["mcp-remote", "https://h.example.test/sse?key=k"], ["mcp-remote", f"https://h.example.test/sse?key={GONE}"]),
    (["--api-key"], ["--api-key"]),
    ([], []),
])
def test_argument_credentials_are_replaced_and_everything_else_kept(args, expected):
    assert without_arg_credentials(args) == expected
