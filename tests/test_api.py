from starlette.testclient import TestClient

from supply_radar.api.main import app

client = TestClient(app)


def test_healthz_reports_ok_and_capabilities():
    res = client.get("/api/healthz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    # Capability flags must always be present so the front end can render an
    # honest status pill rather than guessing.
    # "bigquery" is deliberately absent: nothing imports the client, so
    # advertising it would be a claim the code does not support.
    assert set(body["capabilities"]) == {"places", "llm"}


def test_healthz_never_leaks_secret_values():
    body = client.get("/api/healthz").json()
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


def test_a_sweep_marks_operators_already_in_the_pipeline():
    """A live sweep must not offer to queue something already held.

    Not matching: place_source_id is Google's own identifier and both sides come
    from Google, so it is an exact lookup. Without it, the obvious way to use
    the tool twice is to queue everything twice.
    """
    from supply_radar.api.routes import _already_in_pipeline

    known = _already_in_pipeline()
    snap = client.get("/api/snapshot").json()

    # Every published lead, review item and match is recognised.
    for lead in snap["leads"]:
        assert known[lead["place_source_id"]]["where"] == "leads"
    for item in snap["review_queue"]:
        assert known[item["place_source_id"]]["where"] == "review"
    for m in snap["matched"]:
        assert known[m["place_source_id"]]["where"] == "matched"

    assert len(known) == (
        len(snap["leads"]) + len(snap["review_queue"]) + len(snap["matched"])
    ), "the three sets must be disjoint or one is shadowing another"

    # Something never discovered is not claimed as known.
    assert "not-a-real-place-id" not in known
