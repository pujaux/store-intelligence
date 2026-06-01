# Store Intelligence System
**Purplle Tech Challenge 2026 — Round 2**  
Brigade Road, Bangalore Store (ST1008)

---

## Quickstart

```bash
# 1. Clone and enter project
git clone https://github.com/pujaux/Store-intelligence
cd Store-intelligence

# 2. Add your CCTV footage
cp /path/to/footage.mp4 footage/

# 3. Start the system
docker compose up

# 4. Open the dashboard
open http://localhost:8000/dashboard

# 5. Process your footage
curl -X POST http://localhost:8000/process \
  -H "Content-Type: application/json" \
  -d '{"video_filename": "footage.mp4"}'
```

---
---

## Screenshots

### Live Dashboard
<img width="1366" height="629" alt="Screenshot (2034)" src="https://github.com/user-attachments/assets/b72e619d-29d8-471c-bc6e-57b861963542" />


### Conversion Funnel & Zone Heatmap
<img width="1366" height="600" alt="Screenshot (2035)" src="https://github.com/user-attachments/assets/8a3e2568-e45b-4484-a7df-5860ac7a9e3b" />


### API Documentation
<img width="1366" height="636" alt="Screenshot (2036)" src="https://github.com/user-attachments/assets/ee7a3ca2-524e-4a64-92cd-24b80cbdc9fa" />
<img width="1366" height="643" alt="Screenshot (2037)" src="https://github.com/user-attachments/assets/8f854dc0-d624-4050-a2a1-cdee9ad605c9" />


### Tests Passing
<img width="1366" height="662" alt="Screenshot (2073)" src="https://github.com/user-attachments/assets/d58efebe-03f1-496a-a624-153114075f8c" />


---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe + STALE_FEED detection |
| `/stores/{store_id}/metrics` | GET | Footfall, conversion rate, GMV, dwell time |
| `/stores/{store_id}/funnel` | GET | 5-stage visitor conversion funnel |
| `/stores/{store_id}/heatmap` | GET | Zone visit frequency, normalised 0-100 |
| `/stores/{store_id}/anomalies` | GET | Anomalies with severity + suggested action |
| `/events/ingest` | POST | Ingest events (idempotent, batch up to 500) |
| `/events` | GET | Raw detection events (filter by type, paginate) |
| `/process` | POST | Trigger video processing pipeline |
| `/process/status` | GET | Pipeline status |
| `/dashboard` | GET | Live HTML dashboard |

Store ID for this deployment: `STORE_BLR_002`

Example: `http://localhost:8000/stores/STORE_BLR_002/metrics`

Interactive docs: `http://localhost:8000/docs`

---

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

---

## Project Structure
Store-intelligence/
├── app/
│   ├── main.py            # FastAPI application + all endpoints
│   ├── event_store.py     # Event loading, metrics, funnel, anomalies
│   ├── anomalies.py       # Anomaly detection logic
│   ├── health.py          # Health endpoint + STALE_FEED check
│   ├── ingestion.py       # Event ingest + deduplication
│   ├── metrics.py         # Real-time metric computation
│   └── models.py          # Pydantic event schema
├── detection/
│   ├── pipeline.py        # YOLOv8 detection + centroid tracking
│   └── camera_config.py   # Camera zone configuration
├── tests/
│   └── test_api.py        # API integration tests (29 passing)
├── footage/               # Place CCTV video files here
├── events/                # Pipeline writes events.json here
├── logs/                  # Application logs
├── pipeline/              # Pipeline utilities
├── pos_transactions.csv   # Real Brigade Road POS data (24 orders, ₹44,920 GMV)
├── store_layout.json      # Zone definitions for ST1008
├── DESIGN.md              # System architecture + AI-Assisted Decisions
├── CHOICES.md             # Engineering decisions + trade-offs
├── docker-compose.yml
├── Dockerfile
└── requirements.txt

---

## Key Design Decisions

See [CHOICES.md](CHOICES.md) for full rationale. Summary:

- **YOLOv8n** — fast person detection, sufficient for fixed-camera store CCTV
- **Centroid tracker** — lightweight, no extra model dependencies, works for static cameras
- **Virtual line crossing** — entry/exit detection via left-right centroid transition
- **30s re-entry window** — prevents double-counting customers who briefly step out
- **POS enrichment** — Stage 5 (purchase) uses real Brigade Road sales data for accuracy
- **Store-specific endpoints** — `/stores/{store_id}/metrics` pattern for multi-store support
- **Idempotent ingest** — `POST /events/ingest` deduplicates by event_id

See [DESIGN.md](DESIGN.md) for full system architecture.
