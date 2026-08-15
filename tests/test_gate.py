"""The access code in front of the whole site.

Worth testing properly rather than by eye, because the failure mode is silent
and the wrong way round: a gate that lets everything through looks identical to
a working one from the browser you already have a cookie in.
"""

import pytest
from starlette.testclient import TestClient

from supply_radar.api import gate, main, routes

CODE = "18079"


@pytest.fixture
def client(monkeypatch):
    """A client against an instance that has a code configured.

    Settings are read once at import, so both modules that hold a reference are
    patched. Attempt tracking is cleared so a rate-limit test cannot leak into
    the next one.
    """
    monkeypatch.setattr(main.settings, "access_code", CODE)
    monkeypatch.setattr(routes.settings, "access_code", CODE)
    gate._ATTEMPTS.clear()
    return TestClient(main.app)


def test_the_api_is_closed_without_a_code(client):
    """The point of gating server-side. /api/snapshot is the whole lead list,
    and the page source names the endpoint, so gating only the UI is theatre."""
    assert client.get("/api/snapshot").status_code == 401


def test_a_browser_navigation_is_redirected_not_401(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/enter"


def test_an_api_call_gets_json_not_a_redirect(client):
    """Redirecting XHR to an HTML page produces a parse error in the console
    rather than a message anyone can act on."""
    res = client.get("/api/snapshot", follow_redirects=False)
    assert res.status_code == 401
    assert res.json()["detail"]


def test_the_entry_page_and_probe_stay_open(client):
    assert client.get("/enter").status_code == 200
    assert client.get("/api/healthz").status_code == 200


def test_the_wrong_code_is_refused(client):
    assert client.post("/api/enter", json={"code": "00000"}).status_code == 401


def test_the_right_code_opens_the_site(client):
    assert client.post("/api/enter", json={"code": CODE}).status_code == 200
    # The cookie is now on the client, so the gated endpoint answers.
    assert client.get("/api/snapshot").status_code == 200


def test_the_cookie_never_contains_the_code(client):
    """A cookie holding the literal code hands it to anyone who opens developer
    tools, and that code also authorises spending money."""
    res = client.post("/api/enter", json={"code": CODE})
    assert CODE not in res.headers.get("set-cookie", "")


def test_a_malformed_body_is_a_400_not_a_crash(client):
    """/api/enter is the only endpoint reachable without a cookie, so it is the
    entire unauthenticated attack surface. It must not raise."""
    res = client.post(
        "/api/enter", content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400


def test_guessing_is_rate_limited(client):
    for _ in range(gate.MAX_ATTEMPTS):
        client.post("/api/enter", json={"code": "00000"})
    res = client.post("/api/enter", json={"code": "00000"})
    assert res.status_code == 429
    # And the correct code is refused too while the limit holds, otherwise the
    # limit would only slow down someone who never guesses right.
    assert client.post("/api/enter", json={"code": CODE}).status_code == 429


def test_no_code_configured_leaves_the_site_open(monkeypatch):
    """Local development, and how access_code already behaved."""
    monkeypatch.setattr(main.settings, "access_code", "")
    assert TestClient(main.app).get("/api/snapshot").status_code == 200
