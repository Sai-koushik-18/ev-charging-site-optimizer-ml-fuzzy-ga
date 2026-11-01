"""Live planner API: validation, job lifecycle and result formats, with the pipeline stubbed out."""
import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import server.app as srv  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "WORK", tmp_path)
    srv.jobs.clear()
    srv.by_key.clear()

    def fake_run_place(place, settings, progress, **kw):
        progress("GA: planning 1 station (1 of 1)", 0.5)
        if place == "Nowhere at all":
            raise RuntimeError("No area outline found for 'Nowhere at all'.")
        assert settings.ga_runs == 3 and settings.ml_day_step == 4  # fast mode
        return {"web_data": {"candidates": [], "meta": {"place": {"short": place, "budget": settings.budget_lakhs}}}}

    monkeypatch.setattr(srv, "run_place", fake_run_place)
    return TestClient(srv.app)


def wait(client, job_id):
    for _ in range(100):
        s = client.get(f"/api/jobs/{job_id}").json()
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["active_jobs"] == 0


@pytest.mark.parametrize("payload", [
    {"place": "IIT Madras", "budget": 1},
    {"place": "ab", "budget": 300},
    {"place": "IIT Madras", "budget": 300, "stations": 20},
    {"place": "IIT Madras", "budget": 300, "spacing": 5},
])
def test_rejects_bad_input(client, payload):
    assert client.post("/api/plan", json=payload).status_code == 422


def test_plan_runs_and_returns_dashboard_data(client):
    r = client.post("/api/plan", json={"place": "  IIT   Madras ", "budget": 250})
    assert r.status_code == 202
    job = r.json()["job"]
    assert wait(client, job)["status"] == "done"
    data = client.get(f"/api/jobs/{job}/result").json()
    assert data["meta"]["place"] == {"short": "IIT Madras", "budget": 250}
    js = client.get(f"/api/jobs/{job}/result.js").text
    assert js.startswith("window.DASHBOARD_DATA = {")
    # the identical request reuses the finished job instead of running again
    assert client.post("/api/plan", json={"place": "IIT Madras", "budget": 250}).json()["job"] == job


def test_pipeline_errors_reach_the_user(client):
    job = client.post("/api/plan", json={"place": "Nowhere at all", "budget": 300}).json()["job"]
    s = wait(client, job)
    assert s["status"] == "error" and "No area outline" in s["error"]
    assert client.get(f"/api/jobs/{job}/result").status_code == 409


def test_unknown_job(client):
    assert client.get("/api/jobs/abcdef123456").status_code == 404
    assert client.get("/api/jobs/abcdef123456/result.js").text.startswith("window.EV_LIVE_ERROR = ")
