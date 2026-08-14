from starlette.testclient import TestClient

from supply_radar.api.main import app

client = TestClient(app)


def test_healthz_reports_ok_and_capabilities():
    res = client.get("/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    # Capability flags must always be present so the front end can render an
    # honest status pill rather than guessing.
    assert set(body["capabilities"]) == {"places", "llm", "bigquery"}


def test_healthz_never_leaks_secret_values():
    body = client.get("/healthz").json()
    assert all(isinstance(v, bool) for v in body["capabilities"].values())


def test_meta_exposes_cost_guards():
    body = client.get("/api/meta").json()
    guards = body["guards"]
    assert guards["max_cells_per_run"] > 0
    assert guards["max_subdivision_depth"] > 0
    assert guards["daily_spend_cap_gbp"] > 0


def test_index_serves_the_spa():
    res = client.get("/")
    assert res.status_code == 200
    assert "Supply Radar" in res.text


def test_static_assets_are_served():
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
