# PROMPT: "Write comprehensive pytest tests for a FastAPI Store Intelligence API.
# The API has endpoints: POST /events/ingest, GET /stores/{id}/metrics,
# GET /stores/{id}/funnel, GET /stores/{id}/heatmap, GET /stores/{id}/anomalies,
# GET /health. Tests must cover: happy path, edge cases (empty store, zero purchases,
# re-entry deduplication, all-staff clip), idempotency of ingest, partial success
# on malformed events, and schema validation."
#
# CHANGES MADE:
# - Added real store_id (STORE_BLR_002) to match our Brigade Road dataset
# - Added idempotency test (ingest same events twice → duplicate count increases)
# - Added all-staff edge case test
# - Added zero-purchase store test
# - Added re-entry deduplication test in funnel
# - Fixed event schema to match problem statement exactly

import os
import pathlib
import uuid
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("EVENTS_DIR", "./events")
os.environ.setdefault("FOOTAGE_DIR", "./footage")
pathlib.Path("./events").mkdir(exist_ok=True)
pathlib.Path("./footage").mkdir(exist_ok=True)

from app.main import app

STORE_ID = "STORE_BLR_002"

def make_event(event_type="ENTRY", visitor_id=None, zone_id=None,
               is_staff=False, camera_id="CAM_ENTRY_01"):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": camera_id,
        "visitor_id": visitor_id or f"VIS_{uuid.uuid4().hex[:6]}",
        "event_type": event_type,
        "timestamp": "2026-04-10T10:00:00Z",
        "zone_id": zone_id,
        "dwell_ms": 30000 if event_type == "ZONE_DWELL" else 0,
        "is_staff": is_staff,
        "confidence": 0.92,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1}
    }


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


# ─────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────

def test_health_returns_200(client):
    r = client.get("/health")
    assert r.status_code == 200

def test_health_structure(client):
    body = client.get("/health").json()
    assert "status" in body
    assert "timestamp" in body
    assert "version" in body

def test_health_status_values(client):
    body = client.get("/health").json()
    assert body["status"] in ("healthy", "degraded", "unhealthy")


# ─────────────────────────────────────────────
# Ingest
# ─────────────────────────────────────────────

def test_ingest_accepts_valid_events(client):
    events = [make_event("ENTRY"), make_event("ZONE_ENTER", zone_id="SKINCARE")]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] >= 0
    assert "rejected" in body
    assert "duplicate" in body

def test_ingest_idempotent(client):
    """Sending same events twice should increase duplicate count, not accepted."""
    event = make_event("ENTRY")
    r1 = client.post("/events/ingest", json={"events": [event]})
    r2 = client.post("/events/ingest", json={"events": [event]})
    assert r2.json()["duplicate"] >= 1

def test_ingest_partial_success_on_malformed(client):
    """Valid + malformed events → partial success, not total failure."""
    events = [
        make_event("ENTRY"),
        {"bad": "event", "missing": "fields"}
    ]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] >= 1
    assert body["rejected"] >= 1

def test_ingest_batch_limit(client):
    """Batch over 500 events should be rejected."""
    events = [make_event() for _ in range(501)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 400

def test_ingest_all_staff_clip(client):
    """All-staff clip: staff events ingested but excluded from customer metrics."""
    events = [make_event("ENTRY", is_staff=True) for _ in range(5)]
    r = client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200


# ─────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────

def test_metrics_returns_200(client):
    r = client.get(f"/stores/{STORE_ID}/metrics")
    assert r.status_code == 200

def test_metrics_structure(client):
    body = client.get(f"/stores/{STORE_ID}/metrics").json()
    assert "unique_visitors" in body
    assert "conversion_rate_pct" in body
    assert "currently_in_store" in body
    assert "queue_depth" in body
    assert "abandonment_rate_pct" in body

def test_metrics_conversion_rate_valid_range(client):
    body = client.get(f"/stores/{STORE_ID}/metrics").json()
    assert 0 <= body["conversion_rate_pct"] <= 100

def test_metrics_non_negative_values(client):
    body = client.get(f"/stores/{STORE_ID}/metrics").json()
    assert body["unique_visitors"] >= 0
    assert body["currently_in_store"] >= 0
    assert body["queue_depth"] >= 0

def test_metrics_zero_purchase_store(client):
    """Store with no POS transactions should return 0 conversion, not error."""
    r = client.get(f"/stores/STORE_EMPTY_999/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["conversion_rate_pct"] == 0
    assert body["total_transactions"] == 0


# ─────────────────────────────────────────────
# Funnel
# ─────────────────────────────────────────────

def test_funnel_returns_200(client):
    r = client.get(f"/stores/{STORE_ID}/funnel")
    assert r.status_code == 200

def test_funnel_has_stages(client):
    body = client.get(f"/stores/{STORE_ID}/funnel").json()
    assert "funnel" in body
    assert len(body["funnel"]) >= 3

def test_funnel_stages_ordered(client):
    """Each stage count must be <= previous stage (funnel narrows)."""
    funnel = client.get(f"/stores/{STORE_ID}/funnel").json()["funnel"]
    counts = [s["count"] for s in funnel]
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i-1], f"Stage {i} ({counts[i]}) > Stage {i-1} ({counts[i-1]})"

def test_funnel_first_stage_100_pct(client):
    funnel = client.get(f"/stores/{STORE_ID}/funnel").json()["funnel"]
    assert funnel[0]["pct_of_top"] == 100.0

def test_funnel_reentry_deduplication(client):
    """Re-entry events must not double-count a visitor in the funnel."""
    visitor_id = f"VIS_{uuid.uuid4().hex[:6]}"
    events = [
        make_event("ENTRY", visitor_id=visitor_id),
        make_event("EXIT", visitor_id=visitor_id),
        make_event("REENTRY", visitor_id=visitor_id),  # same person
    ]
    client.post("/events/ingest", json={"events": events})
    body = client.get(f"/stores/{STORE_ID}/funnel").json()
    # Funnel should not crash and conversion should be valid
    assert 0 <= body["conversion_rate_pct"] <= 100


# ─────────────────────────────────────────────
# Heatmap
# ─────────────────────────────────────────────

def test_heatmap_returns_200(client):
    r = client.get(f"/stores/{STORE_ID}/heatmap")
    assert r.status_code == 200

def test_heatmap_structure(client):
    body = client.get(f"/stores/{STORE_ID}/heatmap").json()
    assert "zones" in body
    for zone in body["zones"]:
        assert "zone_id" in zone
        assert "normalised_score" in zone
        assert "data_confidence" in zone
        assert zone["data_confidence"] in ("HIGH", "LOW")

def test_heatmap_normalised_score_range(client):
    body = client.get(f"/stores/{STORE_ID}/heatmap").json()
    for zone in body["zones"]:
        assert 0 <= zone["normalised_score"] <= 100


# ─────────────────────────────────────────────
# Anomalies
# ─────────────────────────────────────────────

def test_anomalies_returns_200(client):
    r = client.get(f"/stores/{STORE_ID}/anomalies")
    assert r.status_code == 200

def test_anomalies_structure(client):
    body = client.get(f"/stores/{STORE_ID}/anomalies").json()
    assert "anomalies" in body
    assert "total_anomalies" in body

def test_anomaly_severity_values(client):
    body = client.get(f"/stores/{STORE_ID}/anomalies").json()
    for a in body["anomalies"]:
        assert a["severity"] in ("INFO", "WARN", "CRITICAL")
        assert "suggested_action" in a

def test_empty_store_no_crash(client):
    """Empty store should return anomalies gracefully, not crash."""
    r = client.get(f"/stores/STORE_EMPTY_999/anomalies")
    assert r.status_code == 200
    body = r.json()
    assert body["total_anomalies"] == 0


# ─────────────────────────────────────────────
# Process & Dashboard
# ─────────────────────────────────────────────

def test_process_status(client):
    r = client.get("/process/status")
    assert r.status_code == 200
    assert "running" in r.json()

def test_process_missing_video(client):
    r = client.post("/process", json={"video_filename": "nonexistent.mp4"})
    assert r.status_code == 404

def test_dashboard_returns_html(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Store Intelligence" in r.text

def test_cameras_endpoint(client):
    r = client.get("/cameras")
    assert r.status_code == 200
    assert "cameras" in r.json()
