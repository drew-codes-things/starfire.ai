import asyncio
import os
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import httpx
import pytest

import mcp_oauth
from mcp_oauth import (ClientRegistration, McpSignInError, OAuthGrant, PendingSignIn, PendingSignIns,
                       RefreshingBearerAuth, SignInNeeded, SignInRejected, TokenSet)
from oauth_fake_server import RunningFakeServer, s256

RFC7636_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC7636_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
CALLBACK = "https://starfire.example.test/api/mcp/servers/sign-in/callback"


@pytest.fixture
def fake():
    with RunningFakeServer() as server:
        yield server


def run(coroutine):
    return asyncio.run(coroutine)


async def discovered_and_registered(fake_server):
    async with httpx.AsyncClient() as client:
        found = await mcp_oauth.discover(client, fake_server.mcp_url)
        registration = await mcp_oauth.register_client(client, found.server, CALLBACK)
    return found, registration


async def signed_in_grant(fake_server) -> tuple[OAuthGrant, str]:
    found, registration = await discovered_and_registered(fake_server)
    verifier, challenge = mcp_oauth.new_pkce_pair()
    url = mcp_oauth.authorization_url(found.server, registration, "state-1", challenge)
    async with httpx.AsyncClient() as client:
        approved = await client.get(url)
        code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
        grant = OAuthGrant(found.server, registration, TokenSet(access_token=""))
        grant.tokens = await mcp_oauth.exchange_code(client, grant, code, verifier)
    return grant, verifier


def test_the_pkce_challenge_matches_the_rfc_7636_example():
    assert mcp_oauth.pkce_challenge(RFC7636_VERIFIER) == RFC7636_CHALLENGE


def test_a_new_pkce_verifier_is_long_random_and_paired_with_its_s256_challenge():
    first, second = mcp_oauth.new_pkce_pair(), mcp_oauth.new_pkce_pair()
    verifier, challenge = first
    assert 43 <= len(verifier) <= 128 and first[0] != second[0]
    assert challenge == s256(verifier) and "=" not in challenge


def test_challenge_parameters_are_read_quoted_or_bare():
    header = 'Bearer error="invalid_token", resource_metadata="https://a.test/.well-known/x", scope=read'
    assert mcp_oauth.challenge_param(header, "resource_metadata") == "https://a.test/.well-known/x"
    assert mcp_oauth.challenge_param(header, "scope") == "read"
    assert mcp_oauth.challenge_param(header, "realm") == ""
    assert mcp_oauth.challenge_param("", "scope") == ""


def test_metadata_is_looked_up_at_the_addresses_the_spec_lists_in_order():
    assert mcp_oauth.protected_resource_urls("https://mcp.test/v1/mcp", "") == [
        "https://mcp.test/.well-known/oauth-protected-resource/v1/mcp",
        "https://mcp.test/.well-known/oauth-protected-resource"]
    assert mcp_oauth.protected_resource_urls("https://mcp.test", "https://x.test/prm")[0] == "https://x.test/prm"
    assert mcp_oauth.authorization_server_urls("https://auth.test") == [
        "https://auth.test/.well-known/oauth-authorization-server",
        "https://auth.test/.well-known/openid-configuration"]
    assert mcp_oauth.authorization_server_urls("https://auth.test/tenant/") == [
        "https://auth.test/.well-known/oauth-authorization-server/tenant",
        "https://auth.test/.well-known/openid-configuration/tenant",
        "https://auth.test/tenant/.well-known/openid-configuration"]


def test_only_https_endpoints_are_used_except_on_this_computer():
    assert mcp_oauth.is_allowed_endpoint("https://auth.example.test/token")
    assert mcp_oauth.is_allowed_endpoint("http://127.0.0.1:9000/token")
    assert not mcp_oauth.is_allowed_endpoint("http://auth.example.test/token")
    assert not mcp_oauth.is_allowed_endpoint("javascript:alert(1)")
    assert not mcp_oauth.is_allowed_endpoint("")


def test_a_resource_covers_its_own_url_and_paths_below_it_on_the_same_origin():
    assert mcp_oauth.resource_covers("https://mcp.test", "https://mcp.test/mcp")
    assert mcp_oauth.resource_covers("https://mcp.test/mcp", "https://mcp.test/mcp/")
    assert not mcp_oauth.resource_covers("https://mcp.test/mcp", "https://mcp.test/mcpx")
    assert not mcp_oauth.resource_covers("https://evil.test/mcp", "https://mcp.test/mcp")


def test_discovery_follows_the_401_challenge_to_both_metadata_documents(fake):
    found, _registration = run(discovered_and_registered(fake))
    server = found.server
    base = fake.state.base_url
    assert server.issuer == base and server.authorization_endpoint == f"{base}/authorize"
    assert server.token_endpoint == f"{base}/token" and server.registration_endpoint == f"{base}/register"
    assert server.resource == fake.mcp_url and server.scope == "read"
    assert [step["status"] for step in found.evidence] == [401, 200, 200]


def test_discovery_refuses_a_sign_in_server_without_s256_pkce(fake):
    fake.state.pkce_methods = ["plain"]
    with pytest.raises(McpSignInError, match="PKCE"):
        run(discovered_and_registered(fake))


def test_discovery_refuses_metadata_that_names_another_issuer(fake):
    fake.state.published_issuer = "https://someone-else.example.test"
    with pytest.raises(McpSignInError, match="issuer"):
        run(discovered_and_registered(fake))


def test_registration_is_dynamic_public_and_bound_to_the_callback(fake):
    _found, registration = run(discovered_and_registered(fake))
    registered = fake.state.clients[registration.client_id]
    assert registered["redirect_uris"] == [CALLBACK] and registered["token_endpoint_auth_method"] == "none"
    assert registration.auth_method == "none" and registration.redirect_uri == CALLBACK


def test_a_service_without_dynamic_registration_is_refused_with_a_clear_reason(fake):
    fake.state.offer_registration = False
    with pytest.raises(McpSignInError, match="register themselves"):
        run(discovered_and_registered(fake))


def test_the_authorization_url_carries_pkce_state_resource_and_scope(fake):
    found, registration = run(discovered_and_registered(fake))
    url = mcp_oauth.authorization_url(found.server, registration, "the-state", "the-challenge")
    params = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
    assert url.startswith(f"{fake.state.base_url}/authorize?")
    assert params == {"response_type": "code", "client_id": registration.client_id, "redirect_uri": CALLBACK,
                      "state": "the-state", "code_challenge": "the-challenge", "code_challenge_method": "S256",
                      "resource": fake.mcp_url, "scope": "read"}


def test_the_code_is_exchanged_with_its_verifier_for_access_and_refresh_tokens(fake):
    grant, _verifier = run(signed_in_grant(fake))
    assert grant.tokens.access_token in fake.state.access_tokens
    assert grant.tokens.refresh_token in fake.state.refresh_tokens
    assert grant.tokens.expires_at > 0
    [exchange] = fake.state.token_requests
    assert exchange["grant_type"] == "authorization_code" and exchange["resource"] == fake.mcp_url


def test_a_code_with_the_wrong_verifier_is_refused(fake):
    async def exchange_with_another_verifier():
        found, registration = await discovered_and_registered(fake)
        _verifier, challenge = mcp_oauth.new_pkce_pair()
        url = mcp_oauth.authorization_url(found.server, registration, "s", challenge)
        async with httpx.AsyncClient() as client:
            code = parse_qs(urlsplit((await client.get(url)).headers["location"]).query)["code"][0]
            grant = OAuthGrant(found.server, registration, TokenSet(access_token=""))
            await mcp_oauth.exchange_code(client, grant, code, mcp_oauth.new_pkce_pair()[0])

    with pytest.raises(SignInNeeded, match="invalid_grant"):
        run(exchange_with_another_verifier())


def test_refreshing_swaps_the_tokens_and_a_refused_refresh_needs_a_new_sign_in(fake):
    grant, _verifier = run(signed_in_grant(fake))

    async def refresh():
        async with httpx.AsyncClient() as client:
            return await mcp_oauth.refresh_tokens(client, grant)

    renewed = run(refresh())
    assert renewed.access_token != grant.tokens.access_token and renewed.access_token in fake.state.access_tokens
    grant.tokens = renewed
    fake.state.refuse_refresh = True
    with pytest.raises(SignInNeeded):
        run(refresh())


def test_a_grant_without_a_refresh_token_cannot_be_renewed():
    grant = OAuthGrant(mcp_oauth.AuthorizationServer("https://a.test", "https://a.test/authorize",
                                                     "https://a.test/token", "", "https://a.test/mcp", ""),
                       ClientRegistration("client"), TokenSet("expired", "", 1.0))
    async def refresh():
        async with httpx.AsyncClient() as client:
            return await mcp_oauth.refresh_tokens(client, grant)

    with pytest.raises(SignInNeeded, match="no refresh token"):
        run(refresh())


def test_a_grant_round_trips_through_its_stored_record(fake):
    grant, _verifier = run(signed_in_grant(fake))
    assert OAuthGrant.from_record({**grant.to_record(), "service": "x", "needs_sign_in": False}) == grant


class Recorder:
    def __init__(self):
        self.refreshed = []
        self.problems = []


def bearer_for(grant: OAuthGrant, recorder: Recorder, clock=None) -> RefreshingBearerAuth:
    extra = {"clock": clock} if clock else {}
    return RefreshingBearerAuth(grant, on_refreshed=recorder.refreshed.append,
                                on_sign_in_needed=recorder.problems.append, **extra)


async def call_mcp(fake_server, auth) -> httpx.Response:
    async with httpx.AsyncClient(auth=auth) as client:
        return await client.post(fake_server.mcp_url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                 headers={"Accept": mcp_oauth.MCP_ACCEPT})


def test_the_bearer_is_renewed_before_it_expires(fake):
    grant, _verifier = run(signed_in_grant(fake))
    fake.state.mcp_authorizations.clear()
    recorder = Recorder()
    soon_after_expiry = grant.tokens.expires_at - mcp_oauth.REFRESH_MARGIN_S + 1
    response = run(call_mcp(fake, bearer_for(grant, recorder, clock=lambda: soon_after_expiry)))
    assert response.status_code == 200
    assert fake.state.grant_count("refresh_token") == 1 and len(recorder.refreshed) == 1
    assert fake.state.mcp_authorizations == [recorder.refreshed[0].access_token]


def test_a_401_renews_the_bearer_once_and_retries(fake):
    grant, _verifier = run(signed_in_grant(fake))
    fake.state.mcp_authorizations.clear()
    recorder = Recorder()
    first_token = grant.tokens.access_token
    fake.state.revoke_access_tokens()
    response = run(call_mcp(fake, bearer_for(grant, recorder)))
    assert response.status_code == 200
    assert fake.state.mcp_authorizations == [first_token, recorder.refreshed[0].access_token]
    assert fake.state.grant_count("refresh_token") == 1 and recorder.problems == []


def test_a_refused_refresh_reports_that_the_server_needs_signing_in_again(fake):
    grant, _verifier = run(signed_in_grant(fake))
    recorder = Recorder()
    fake.state.revoke_access_tokens()
    fake.state.refuse_refresh = True
    with pytest.raises(SignInNeeded):
        run(call_mcp(fake, bearer_for(grant, recorder)))
    assert recorder.refreshed == [] and len(recorder.problems) == 1 and "invalid_grant" in str(recorder.problems[0])


def pending(user_id="admin", digest="session-a") -> PendingSignIn:
    grant = OAuthGrant(mcp_oauth.AuthorizationServer("https://a.test", "https://a.test/authorize",
                                                     "https://a.test/token", "", "https://a.test/mcp", ""),
                       ClientRegistration("client", redirect_uri=CALLBACK), TokenSet(""))
    return PendingSignIn(user_id=user_id, session_digest=digest, verifier="v", server_url="https://a.test/mcp",
                         name="A", service="a", replaces_server_id="", grant=grant)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_pending_sign_in_is_handed_back_once_to_the_session_that_started_it():
    store = PendingSignIns()
    state = store.add(pending())
    assert len(state) >= 43
    assert store.take(state, "admin", "session-a").name == "A"
    with pytest.raises(SignInRejected, match="already used"):
        store.take(state, "admin", "session-a")


def test_an_unknown_or_empty_state_is_rejected():
    store = PendingSignIns()
    store.add(pending())
    for state in ("not-the-state", ""):
        with pytest.raises(SignInRejected, match="unknown"):
            store.take(state, "admin", "session-a")


def test_a_pending_sign_in_from_another_session_or_user_is_rejected_and_spent():
    store = PendingSignIns()
    for user_id, digest in (("admin", "session-b"), ("other-admin", "session-a")):
        state = store.add(pending())
        with pytest.raises(SignInRejected, match="different starfire session"):
            store.take(state, user_id, digest)
        with pytest.raises(SignInRejected, match="already used"):
            store.take(state, "admin", "session-a")


def test_a_pending_sign_in_expires():
    clock = Clock()
    store = PendingSignIns(clock=clock)
    state = store.add(pending())
    clock.now += mcp_oauth.PENDING_LIFETIME_S + 1
    with pytest.raises(SignInRejected, match="took too long"):
        store.take(state, "admin", "session-a")


def test_expired_and_surplus_pending_sign_ins_are_dropped():
    clock = Clock()
    store = PendingSignIns(clock=clock, limit=3)
    store.add(pending())
    clock.now += mcp_oauth.PENDING_LIFETIME_S + 1
    states = [store.add(pending()) for _ in range(4)]
    assert len(store) == 3
    with pytest.raises(SignInRejected):
        store.take(states[0], "admin", "session-a")
    assert store.take(states[-1], "admin", "session-a").name == "A"


def test_a_token_endpoint_blip_fails_the_request_but_does_not_end_the_session(fake, monkeypatch):
    grant, _verifier = run(signed_in_grant(fake))
    recorder = Recorder()
    fake.state.revoke_access_tokens()

    async def unreachable_token_address(client, held, clock):
        raise McpSignInError("could not reach the token address: ConnectTimeout")

    monkeypatch.setattr(mcp_oauth, "refresh_tokens", unreachable_token_address)
    response = run(call_mcp(fake, bearer_for(grant, recorder)))
    assert response.status_code == 401
    assert recorder.problems == [] and recorder.refreshed == []


REMOTE = "https://mcp.remote.example.test"
LOCAL_TARGETS = ("http://127.0.0.1:2019/load", "https://127.0.0.1/token", "https://localhost/token",
                 "https://api.localhost/token", "https://10.1.2.3/token", "https://192.168.1.16/token",
                 "https://172.16.0.9/token", "https://169.254.169.254/latest", "https://[::1]/token",
                 "https://[fe80::1]/token", "https://[fd00::5]/token", "https://[::ffff:127.0.0.1]/token",
                 "https://0.0.0.0/token", "https://100.64.0.1/token", "http://auth.example.test/token")


def test_endpoints_learned_from_a_remote_server_must_be_public_https_addresses():
    remote = mcp_oauth.EndpointRules.for_server(f"{REMOTE}/mcp")
    assert not remote.allows_local
    assert remote.permits("https://auth.example.test/token") and remote.permits("https://93.184.216.34/token")
    for target in LOCAL_TARGETS:
        assert not remote.permits(target), target
    local = mcp_oauth.EndpointRules.for_server("http://127.0.0.1:8123/mcp")
    assert local.allows_local and local.permits("http://127.0.0.1:2019/token")
    assert local.permits("https://auth.example.test/token") and not local.permits("http://auth.example.test/token")
    assert mcp_oauth.EndpointRules.for_server("http://localhost:9000/mcp").allows_local


def pivot_transport(seen: list) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        url = str(request.url)
        if url == f"{REMOTE}/mcp":
            return httpx.Response(401, headers={
                "WWW-Authenticate": 'Bearer resource_metadata="http://127.0.0.1:11434/api/tags"'})
        if url == f"{REMOTE}/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": f"{REMOTE}/mcp", "authorization_servers": [REMOTE]})
        if url == f"{REMOTE}/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"issuer": REMOTE, "authorization_endpoint": f"{REMOTE}/authorize",
                                             "token_endpoint": "http://127.0.0.1:2019/load",
                                             "registration_endpoint": "http://localhost:2019/stop",
                                             "code_challenge_methods_supported": ["S256"]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def is_local_request(url: str) -> bool:
    return urlsplit(url).hostname in ("127.0.0.1", "localhost")


def test_a_remote_server_cannot_point_starfire_at_addresses_on_this_computer(monkeypatch):
    async def public(host):
        return [mcp_oauth.ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(mcp_oauth, "resolve_addresses", public, raising=False)
    seen = []

    async def discover():
        async with httpx.AsyncClient(transport=pivot_transport(seen)) as client:
            return await mcp_oauth.discover(client, f"{REMOTE}/mcp")

    with pytest.raises(McpSignInError, match="unusable .* public https"):
        run(discover())
    assert seen and not any(is_local_request(url) for url in seen)


def test_a_learned_host_whose_name_resolves_to_a_private_address_is_not_contacted(monkeypatch):
    async def private(host):
        return [mcp_oauth.ipaddress.ip_address("93.184.216.34"), mcp_oauth.ipaddress.ip_address("10.0.0.7")]

    monkeypatch.setattr(mcp_oauth, "resolve_addresses", private, raising=False)
    seen = []
    server = mcp_oauth.AuthorizationServer(REMOTE, f"{REMOTE}/authorize", "https://token.rebind.example.test/token",
                                           "https://token.rebind.example.test/register", f"{REMOTE}/mcp", "")
    grant = OAuthGrant(server, ClientRegistration("client"), TokenSet("old", "refresh", 1.0))

    async def attempts():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))) \
                as client:
            with pytest.raises(McpSignInError, match="private or local"):
                await mcp_oauth.register_client(client, server, CALLBACK)
            with pytest.raises(McpSignInError, match="private or local"):
                await mcp_oauth.refresh_tokens(client, grant)

    run(attempts())
    assert seen == []


ENDLESS_CHUNK = b" " * 65536


async def endless_body():
    while True:
        yield ENDLESS_CHUNK
        await asyncio.sleep(0)


def test_an_endless_metadata_answer_is_cut_off_instead_of_read_forever():
    local_server = "http://127.0.0.1:9/mcp"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"})
        return httpx.Response(200, headers={"content-type": "application/json"}, content=endless_body())

    async def discover():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await mcp_oauth.discover(client, local_server)

    with pytest.raises(McpSignInError, match="no OAuth metadata"):
        run(asyncio.wait_for(discover(), timeout=20))


def test_a_sign_in_step_that_runs_past_the_deadline_is_stopped(monkeypatch):
    monkeypatch.setattr(mcp_oauth, "FLOW_DEADLINE_S", 0.2, raising=False)

    async def slow():
        await asyncio.sleep(1)
        return "finished late"

    async def attempt():
        return await asyncio.wait_for(mcp_oauth.within_deadline(slow(), "finding the sign-in server"), timeout=5)

    with pytest.raises(McpSignInError, match="took longer than 0.2 seconds"):
        run(attempt())


def test_a_damaged_stored_record_needs_a_new_sign_in_instead_of_crashing(fake):
    grant, _verifier = run(signed_in_grant(fake))
    good = {**grant.to_record(), "service": "x", "needs_sign_in": False}
    for damaged in ({"access_token": "x"}, {**good, "expires_at": "soon"}, {**good, "issuer": 5},
                    {**good, "token_endpoint": ""}, {**good, "client_id": None}, {**good, "expires_at": True},
                    ["not", "a", "record"]):
        with pytest.raises(SignInNeeded, match="saved sign-in"):
            OAuthGrant.from_record(damaged)
    assert OAuthGrant.from_record({key: value for key, value in good.items() if key != "iss_required"}) == grant


def test_the_iss_requirement_is_read_from_the_sign_in_server_metadata(fake):
    assert run(discovered_and_registered(fake))[0].server.iss_required is False
    fake.state.advertise_iss = True
    assert run(discovered_and_registered(fake))[0].server.iss_required is True
