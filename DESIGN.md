# DESIGN.md — Store Intelligence System
**Brigade Road, Bangalore | Purplle Tech Challenge 2026 — Round 2**

---

## 1. System Overview

The Store Intelligence System converts raw CCTV footage into actionable retail analytics. It is designed as a production-ready, containerized pipeline that starts with video and ends with business metrics.

```
┌──────────────────────────────────────────────────────────────────┐
│                        INPUT LAYER                               │
│   CCTV Footage (MP4/AVI)  +  POS Sales Data (CSV)               │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                   DETECTION PIPELINE                             │
│                                                                  │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│   │  YOLOv8n     │───▶│  Centroid    │───▶│  Event           │  │
│   │  Person      │    │  Tracker     │    │  Generator       │  │
│   │  Detection   │    │  (ID assign) │    │  (entry/exit/    │  │
│   └──────────────┘    └──────────────┘    │   zone/anomaly)  │  │
│                                           └──────────────────┘  │
└────────────────────────┬─────────────────────────────────────────┘
                         │  events.json
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    BUSINESS LOGIC LAYER                          │
│                                                                  │
│   EventStore (in-memory, reloadable)                            │
│   ├── Footfall metrics                                           │
│   ├── Conversion funnel (5 stages)                               │
│   ├── Anomaly detection                                          │
│   └── POS data enrichment (Brigade Road CSV)                     │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                     API + DASHBOARD LAYER                        │
│                                                                  │
│   FastAPI REST API                                               │
│   ├── GET /metrics       — store KPIs                            │
│   ├── GET /funnel        — conversion funnel                     │
│   ├── GET /anomalies     — detected issues                       │
│   ├── GET /events        — raw events                            │
│   ├── POST /process      — trigger pipeline                      │
│   └── GET /dashboard     — live HTML dashboard                   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Detection Pipeline Architecture

### 2.1 Person Detection — YOLOv8n
- **Model:** YOLOv8 nano (`yolov8n.pt`) — COCO-pretrained
- **Class filter:** Person only (class 0)
- **Confidence threshold:** 0.4 (tunable via API)
- **Frame skip:** Every 2nd frame processed (speed trade-off)

### 2.2 Tracking — Centroid Tracker
A lightweight centroid-based tracker is used instead of DeepSORT/ByteTrack.

**Rationale:** DeepSORT requires a separate re-ID model (100MB+), which increases cold-start time and Docker image size. For a single fixed-camera store, centroid tracking achieves comparable results because:
- Camera angle is static (no perspective shift)
- People move predictably (entry → browse → exit)
- Max ~20 people in frame simultaneously

**Assignment algorithm:**
1. Compute Euclidean distance matrix between existing tracks and new detections
2. Assign using greedy nearest-centroid matching
3. Max distance threshold = 100px (filters cross-aisle mismatches)
4. Deregister tracks missing for >40 frames (≈1.3 seconds at 30fps)

### 2.3 Entry/Exit Detection — Virtual Line Crossing
- A vertical virtual line is placed at **x = 12% of frame width** (just inside the entry door, per store layout)
- **Entry event:** centroid crosses left→right
- **Exit event:** centroid crosses right→left
- **Re-entry guard:** same track_id crossing again within 30 seconds = same visit, not counted twice

### 2.4 Staff Filtering
People spending >5 continuous minutes in the **cash counter zone** (x > 85% of frame) are marked as staff and excluded from visitor metrics.

### 2.5 Zone Mapping
Derived from the Brigade Road store layout (see `Brigade_Road_Store_layout.xlsx`):

| Zone | Screen X range | Description |
|------|---------------|-------------|
| `entry` | 0–15% | Entry/exit door area |
| `foh` | 15–75% | Front of House (main floor) |
| `makeup_unit` | 35–65% | Central makeup island |
| `cash_counter` | 78–95% | Right-side billing counter |
| `back_wall` | 0–15% | Rear shelving (premium brands) |

---

## 3. Business Logic — Conversion Funnel

The funnel is computed from detection events enriched with real POS data:

```
Stage 1: Entered store         (CCTV: entry event count)
Stage 2: Browsed FOH           (CCTV: zone_enter foh events)
Stage 3: Engaged with product  (CCTV: zone_enter makeup_unit events)
Stage 4: Reached cash counter  (CCTV: zone_enter cash_counter events)
Stage 5: Purchased             (POS: actual transaction count — ground truth)
```

**Why combine CCTV + POS?** Stage 5 (purchase) is definitively known from the POS system. Using CCTV alone for this would introduce false negatives (customers who pay without triggering the detection zone). Ground truth from POS makes the conversion rate accurate.

---

## 4. Anomaly Detection

Two anomaly types are detected:

| Anomaly | Trigger | Severity |
|---------|---------|----------|
| **Overcrowding** | >15 simultaneous visitors in FOH | High/Medium |
| **Long dwell** | Visitor in store >30 minutes without purchase | Low |

Additional anomalies planned (not in v1): staff absence from counter, unusual exit patterns.

---

## 5. Data Flow

```
footage/*.mp4
      │
      ▼ StorePipeline.run()
events/events.json   ←──── written after pipeline completes
      │
      ▼ EventStore.load()
in-memory list of dicts
      │
      ├──▶ /metrics   (aggregated)
      ├──▶ /funnel    (staged)
      └──▶ /anomalies (filtered)
```

The EventStore can be reloaded without restarting the server (via `EventStore.reload()`), which is called automatically after each pipeline run.

---

## 6. Production Deployment

```bash
docker compose up      # starts everything
```

- **Port 8000** exposed for API and dashboard
- **Volumes:** `./footage` (input), `./events` (output), `./logs`
- **Health check:** `GET /health` (used by Docker and load balancers)
- **Restart policy:** `unless-stopped`

---

## 7. Known Limitations

- Occlusion: when people overlap in frame, the centroid tracker may merge two tracks temporarily. This is handled by the 40-frame disappearance timeout.
- Re-entry within 30 seconds: the re-entry window is a heuristic. A customer who leaves quickly and returns may be under-counted.
- Staff detection latency: staff are only identified after 5 minutes, so their first few minutes may contribute to visitor count.
