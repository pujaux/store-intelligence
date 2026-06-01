# Store Intelligence System
**Purplle Tech Challenge 2026 — Round 2**  
Brigade Road, Bangalore Store

---

## Quickstart

```bash
# 1. Clone and enter project
git clone https://github.com/pujaux/Store-intelligence
cd store-intelligence

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

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe |
| `/metrics` | GET | Footfall, conversion rate, GMV, dwell time |
| `/funnel` | GET | 5-stage visitor conversion funnel |
| `/anomalies` | GET | Detected anomalies (overcrowding, long dwell) |
| `/events` | GET | Raw detection events (filter by type, paginate) |
| `/process` | POST | Trigger video processing |
| `/process/status` | GET | Pipeline status |
| `/dashboard` | GET | Live HTML dashboard |

Interactive docs: http://localhost:8000/docs

---

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

---

## Project Structure

```
store-intelligence/
├── app/
│   ├── main.py          # FastAPI application + all endpoints
│   └── event_store.py   # Event loading, metrics, funnel, anomalies
├── detection/
│   └── pipeline.py      # YOLOv8 detection + centroid tracking
├── tests/
│   └── test_api.py      # API integration tests
├── footage/             # Place CCTV video files here
├── events/              # Pipeline writes events.json here
├── DESIGN.md            # System architecture
├── CHOICES.md           # Engineering decisions
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Key Design Decisions

See [CHOICES.md](CHOICES.md) for full rationale. Summary:

- **YOLOv8n** — fast person detection, sufficient for fixed-camera store CCTV
- **Centroid tracker** — lightweight, no extra model dependencies, works for static cameras
- **Virtual line crossing** — entry/exit detection via left-right centroid transition
- **30s re-entry window** — prevents double-counting customers who briefly step out
- **POS enrichment** — Stage 5 (purchase) uses real Brigade Road sales data for accuracy

See [DESIGN.md](DESIGN.md) for full system architecture.
