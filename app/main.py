"""
app/main.py

Store Intelligence System — FastAPI application.
Purplle Tech Challenge 2026 — Round 2

Endpoints:
  POST /events/ingest                    — ingest events (idempotent by event_id)
  GET  /stores/{store_id}/metrics        — real-time store KPIs
  GET  /stores/{store_id}/funnel         — conversion funnel
  GET  /stores/{store_id}/heatmap        — zone visit heatmap
  GET  /stores/{store_id}/anomalies      — detected anomalies
  GET  /health                           — service health + stale feed detection
  GET  /dashboard                        — live HTML dashboard
  POST /process                          — trigger CCTV processing
  GET  /process/status                   — pipeline status
  GET  /cameras                          — camera configuration
"""

import os
import uuid
import logging
import asyncio
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.ingestion import get_ingestion_service
from app.metrics import compute_metrics, compute_funnel, compute_heatmap
from app.anomalies import detect_anomalies
from app.health import get_health

# ─────────────────────────────────────────────
# Logging — structured with trace_id
# ─────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)
logger = logging.getLogger(__name__)

FOOTAGE_DIR = os.getenv("FOOTAGE_DIR", "./footage")
EVENTS_DIR  = os.getenv("EVENTS_DIR",  "./events")
STORE_ID    = "STORE_BLR_002"

# ─────────────────────────────────────────────
# App lifecycle
# ─────────────────────────────────────────────

processing_status = {"running": False, "last_run": None, "last_result": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Store Intelligence System...")
    Path(FOOTAGE_DIR).mkdir(parents=True, exist_ok=True)
    Path(EVENTS_DIR).mkdir(parents=True, exist_ok=True)
    get_ingestion_service()   # initialise singleton
    logger.info("System ready.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Store Intelligence API",
    description="End-to-end retail store analytics from CCTV footage — Purplle Tech Challenge 2026",
    version="1.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
# Middleware — structured logging per request
# ─────────────────────────────────────────────

@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())[:8]
    start = datetime.utcnow()
    response = await call_next(request)
    latency = (datetime.utcnow() - start).total_seconds() * 1000
    logger.info(
        f"trace_id={trace_id} method={request.method} "
        f"path={request.url.path} status={response.status_code} "
        f"latency_ms={latency:.1f}"
    )
    return response


# ─────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────

class ProcessRequest(BaseModel):
    video_filename: Optional[str] = None
    confidence: float = 0.4


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.get("/health")
def health():
    """
    Service health check.
    Returns STALE_FEED warning if any store has no events in last 10 minutes.
    """
    svc = get_ingestion_service()
    return get_health(svc.get_events())


@app.post("/events/ingest")
def ingest_events(request: dict):
    """
    Ingest a batch of up to 500 events.
    Idempotent by event_id — safe to call twice with same payload.
    Returns partial success on malformed events.
    """
    events = request.get("events", [])
    if len(events) > 500:
        raise HTTPException(400, "Batch size exceeds 500 events")

    svc = get_ingestion_service()
    result = svc.ingest(events)
    logger.info(
        f"ingest: accepted={result['accepted']} "
        f"rejected={result['rejected']} duplicate={result['duplicate']}"
    )
    return result


@app.get("/stores/{store_id}/metrics")
def get_metrics(store_id: str):
    """
    Real-time store KPIs:
    unique visitors, conversion rate, avg dwell per zone,
    queue depth, abandonment rate.
    Excludes is_staff=true events. Handles zero-purchase stores.
    """
    svc = get_ingestion_service()
    events = svc.get_events()
    # Load from events.json if store events not yet ingested via API
    if not any(e.get("store_id") == store_id for e in events):
        events = _load_raw_events()
    return compute_metrics(events, store_id)


@app.get("/stores/{store_id}/funnel")
def get_funnel(store_id: str):
    """
    Conversion funnel: Entry → Zone Visit → Billing → Purchase.
    Session is the unit (visitor_id). Re-entries do not double-count.
    """
    svc = get_ingestion_service()
    events = svc.get_events()
    if not any(e.get("store_id") == store_id for e in events):
        events = _load_raw_events()
    return compute_funnel(events, store_id)


@app.get("/stores/{store_id}/heatmap")
def get_heatmap(store_id: str):
    """
    Zone visit frequency + avg dwell, normalised 0-100.
    Includes data_confidence flag if fewer than 20 sessions in window.
    """
    svc = get_ingestion_service()
    events = svc.get_events()
    if not any(e.get("store_id") == store_id for e in events):
        events = _load_raw_events()
    return compute_heatmap(events, store_id)


@app.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str):
    """
    Active anomalies: queue spike, overcrowding, dead zone, high abandonment.
    Severity: INFO / WARN / CRITICAL. Includes suggested_action per anomaly.
    """
    svc = get_ingestion_service()
    events = svc.get_events()
    if not any(e.get("store_id") == store_id for e in events):
        events = _load_raw_events()
    return detect_anomalies(events, store_id)


@app.post("/process")
async def process_video(req: ProcessRequest, background_tasks: BackgroundTasks):
    """
    Trigger CCTV footage processing.
    Omit video_filename to process all cameras in footage/ directory.
    """
    if processing_status["running"]:
        raise HTTPException(409, "Processing already in progress")

    if req.video_filename:
        video_path = Path(FOOTAGE_DIR) / req.video_filename
        if not video_path.exists():
            raise HTTPException(404, f"Video not found: {req.video_filename}")
        background_tasks.add_task(_run_pipeline_single, str(video_path), req.confidence)
        return {"status": "started", "mode": "single", "video": req.video_filename}
    else:
        background_tasks.add_task(_run_pipeline_all, req.confidence)
        return {"status": "started", "mode": "all_cameras", "footage_dir": FOOTAGE_DIR}


@app.get("/process/status")
def process_status():
    return processing_status


@app.get("/cameras")
def get_cameras():
    """Camera configuration and zone mapping."""
    from detection.camera_config import CAMERA_CONFIGS
    return {"cameras": CAMERA_CONFIGS}


# Legacy endpoints for dashboard compatibility
@app.get("/metrics")
def legacy_metrics():
    return get_metrics(STORE_ID)

@app.get("/funnel")
def legacy_funnel():
    return get_funnel(STORE_ID)

@app.get("/anomalies")
def legacy_anomalies():
    return get_anomalies(STORE_ID)

@app.get("/events")
def get_events_raw(
    event_type: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000)
):
    svc = get_ingestion_service()
    events = svc.get_events(event_type=event_type)
    return {"events": events[-limit:], "count": len(events)}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(_dashboard_html())


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _load_raw_events():
    """Load events from events.json (produced by detection pipeline)."""
    import json
    path = Path(EVENTS_DIR) / "events.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


async def _run_pipeline_single(video_path: str, confidence: float):
    global processing_status
    processing_status["running"] = True
    try:
        from detection.pipeline import StorePipeline
        pipeline = StorePipeline(FOOTAGE_DIR, EVENTS_DIR, confidence)
        result = await asyncio.to_thread(pipeline.run_single, video_path)
        get_ingestion_service().__init__()  # reload
        processing_status["last_result"] = result
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        processing_status["last_result"] = {"error": str(e)}
    finally:
        processing_status["running"] = False
        processing_status["last_run"] = datetime.utcnow().isoformat()


async def _run_pipeline_all(confidence: float):
    global processing_status
    processing_status["running"] = True
    try:
        from detection.pipeline import StorePipeline
        pipeline = StorePipeline(FOOTAGE_DIR, EVENTS_DIR, confidence)
        result = await asyncio.to_thread(pipeline.run)
        processing_status["last_result"] = result
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)
        processing_status["last_result"] = {"error": str(e)}
    finally:
        processing_status["running"] = False
        processing_status["last_run"] = datetime.utcnow().isoformat()


# ─────────────────────────────────────────────
# Dashboard HTML
# ─────────────────────────────────────────────

def _dashboard_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Store Intelligence — Brigade Road</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800&display=swap');
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Inter', 'Segoe UI', sans-serif;
    min-height: 100vh; color: #1a0030;
    background: radial-gradient(ellipse at 50% 50%, rgba(255,255,255,0.97) 0%, rgba(255,255,255,0.85) 25%, rgba(180,100,255,0.35) 55%, rgba(80,0,120,0.7) 75%, #000 100%);
    background-attachment: fixed;
  }
  header {
    position: relative; z-index: 10;
    background: linear-gradient(135deg, rgba(0,0,0,0.85) 0%, rgba(80,0,140,0.9) 50%, rgba(0,0,0,0.85) 100%);
    padding: 20px 40px; border-bottom: 2px solid rgba(200,120,255,0.6);
    display: flex; align-items: center; gap: 14px;
    box-shadow: 0 4px 40px rgba(140,0,255,0.3);
  }
  header h1 { font-size: 22px; font-weight: 700; color: #fff; }
  header h1 span { color: #d580ff; }
  header .ts { font-size: 12px; color: rgba(255,255,255,0.5); margin-left: auto; }
  .live-dot { width: 9px; height: 9px; border-radius: 50%; background: #22c55e; display: inline-block; margin-right: 4px; box-shadow: 0 0 8px #22c55e; animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  .page { position: relative; z-index: 1; padding: 0 0 40px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 18px; padding: 28px 32px; }
  .card { background: linear-gradient(145deg, rgba(255,255,255,0.92), rgba(240,220,255,0.75)); border: 1px solid rgba(160,80,255,0.3); border-radius: 16px; padding: 22px 20px; box-shadow: 0 4px 24px rgba(120,0,200,0.12); transition: transform 0.2s; }
  .card:hover { transform: translateY(-3px); }
  .card h3 { font-size: 10px; text-transform: uppercase; color: #7c3aed; letter-spacing: 1.5px; font-weight: 600; margin-bottom: 10px; }
  .card .val { font-size: 34px; font-weight: 800; color: #1a0030; line-height: 1; }
  .card .sub { font-size: 11px; color: #7c5c99; margin-top: 6px; }
  .section { padding: 0 32px 28px; }
  .section h2 { font-size: 18px; font-weight: 700; margin-bottom: 18px; color: #1a0030; display: flex; align-items: center; gap: 10px; }
  .section h2::before { content: ''; display: inline-block; width: 4px; height: 20px; background: linear-gradient(180deg, #a855f7, #7c3aed); border-radius: 2px; }
  .stage { background: linear-gradient(135deg, rgba(255,255,255,0.88), rgba(245,230,255,0.7)); border: 1px solid rgba(160,80,255,0.2); border-radius: 10px; padding: 14px 18px; margin-bottom: 10px; display: flex; align-items: center; gap: 16px; }
  .stage-label { flex: 1; font-size: 13px; font-weight: 500; color: #2d0050; }
  .stage-bar-wrap { flex: 2; background: rgba(120,0,200,0.08); border-radius: 99px; height: 9px; overflow: hidden; }
  .stage-bar { height: 9px; border-radius: 99px; background: linear-gradient(90deg, #a855f7, #7c3aed); transition: width 0.6s; }
  .stage-count { width: 50px; text-align: right; font-weight: 700; color: #1a0030; }
  .stage-pct { width: 48px; text-align: right; font-size: 12px; color: #7c5c99; }
  .anomaly { background: linear-gradient(135deg, rgba(255,255,255,0.88), rgba(255,230,230,0.7)); border-left: 4px solid #ef4444; border-radius: 10px; padding: 14px 18px; margin-bottom: 10px; font-size: 13px; color: #2d0050; }
  .anomaly.WARN { border-color: #f59e0b; }
  .anomaly.INFO { border-color: #3b82f6; }
  .anomaly strong { display: block; margin-bottom: 4px; }
  .no-data { color: #7c5c99; font-style: italic; font-size: 13px; }
  .heatmap-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
  .zone-card { background: rgba(255,255,255,0.85); border-radius: 10px; padding: 14px; border: 1px solid rgba(160,80,255,0.2); }
  .zone-name { font-weight: 600; font-size: 13px; color: #1a0030; margin-bottom: 8px; }
  .zone-bar-wrap { background: rgba(120,0,200,0.08); border-radius: 99px; height: 6px; overflow: hidden; margin-bottom: 6px; }
  .zone-bar { height: 6px; border-radius: 99px; background: linear-gradient(90deg, #a855f7, #7c3aed); }
  .zone-stats { font-size: 11px; color: #7c5c99; }
</style>
</head>
<body>
<header>
  <span class="live-dot"></span>
  <h1>Store Intelligence — <span>Brigade Road</span>, Bangalore</h1>
  <div class="ts" id="ts">Loading...</div>
</header>
<div class="page">
  <div class="grid" id="metrics-grid">
    <div class="card"><h3>Footfall</h3><div class="val" id="footfall">—</div><div class="sub">Unique visitors today</div></div>
    <div class="card"><h3>In Store Now</h3><div class="val" id="instore">—</div><div class="sub">Current occupancy</div></div>
    <div class="card"><h3>Conversion Rate</h3><div class="val" id="conv">—</div><div class="sub">Visitors → Purchase</div></div>
    <div class="card"><h3>Total GMV</h3><div class="val" id="gmv">—</div><div class="sub">₹ Revenue today</div></div>
    <div class="card"><h3>Avg Basket</h3><div class="val" id="aov">—</div><div class="sub">₹ per transaction</div></div>
    <div class="card"><h3>Queue Depth</h3><div class="val" id="queue">—</div><div class="sub">Billing counter</div></div>
  </div>
  <div class="section">
    <h2>Conversion Funnel</h2>
    <div id="funnel-stages"></div>
  </div>
  <div class="section">
    <h2>Zone Heatmap</h2>
    <div class="heatmap-grid" id="heatmap"></div>
  </div>
  <div class="section">
    <h2>Anomalies</h2>
    <div id="anomaly-list"><div class="no-data">No anomalies detected</div></div>
  </div>
</div>
<script>
const STORE = 'STORE_BLR_002';
async function refresh() {
  try {
    const [m, f, h, a] = await Promise.all([
      fetch(`/stores/${STORE}/metrics`).then(r=>r.json()),
      fetch(`/stores/${STORE}/funnel`).then(r=>r.json()),
      fetch(`/stores/${STORE}/heatmap`).then(r=>r.json()),
      fetch(`/stores/${STORE}/anomalies`).then(r=>r.json()),
    ]);
    document.getElementById('ts').textContent = 'Last updated: ' + new Date().toLocaleTimeString();
    document.getElementById('footfall').textContent = m.unique_visitors ?? '—';
    document.getElementById('instore').textContent = m.currently_in_store ?? '—';
    document.getElementById('conv').textContent = (m.conversion_rate_pct ?? '—') + '%';
    document.getElementById('gmv').textContent = '₹' + (m.total_gmv_inr?.toLocaleString() ?? '—');
    document.getElementById('aov').textContent = '₹' + (m.avg_basket_value_inr ?? '—');
    document.getElementById('queue').textContent = m.queue_depth ?? '0';
    const fc = document.getElementById('funnel-stages');
    fc.innerHTML = '';
    (f.funnel || []).forEach(s => {
      const pct = Math.min(s.pct_of_top, 100);
      fc.innerHTML += `<div class="stage">
        <div class="stage-label">${s.label}</div>
        <div class="stage-bar-wrap"><div class="stage-bar" style="width:${pct}%"></div></div>
        <div class="stage-count">${s.count}</div>
        <div class="stage-pct">${s.pct_of_top}%</div>
      </div>`;
    });
    const hc = document.getElementById('heatmap');
    hc.innerHTML = '';
    (h.zones || []).slice(0, 8).forEach(z => {
      hc.innerHTML += `<div class="zone-card">
        <div class="zone-name">${z.zone_id}</div>
        <div class="zone-bar-wrap"><div class="zone-bar" style="width:${z.normalised_score}%"></div></div>
        <div class="zone-stats">${z.visit_count} visits · ${z.avg_dwell_seconds}s avg dwell · <span style="color:${z.data_confidence==='HIGH'?'#22c55e':'#f59e0b'}">${z.data_confidence}</span></div>
      </div>`;
    });
    const ac = document.getElementById('anomaly-list');
    if (!a.anomalies?.length) {
      ac.innerHTML = '<div class="no-data">No anomalies detected</div>';
    } else {
      ac.innerHTML = a.anomalies.map(x => `
        <div class="anomaly ${x.severity}">
          <strong>${x.severity}: ${x.type}</strong>
          ${x.description}<br>
          <small>💡 ${x.suggested_action}</small>
        </div>`).join('');
    }
  } catch(e) { console.error(e); }
}
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""
