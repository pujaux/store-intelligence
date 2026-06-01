"""
app/ingestion.py
Event ingestion, deduplication and storage.
Idempotent by event_id — safe to call twice with same payload.
"""
import json
import logging
from pathlib import Path
from typing import List
from datetime import datetime

logger = logging.getLogger(__name__)

EVENTS_FILE = Path("events/events.json")


class IngestionService:
    def __init__(self):
        self._events: dict = {}  # event_id -> event dict (dedup key)
        self._load_existing()

    def _load_existing(self):
        if EVENTS_FILE.exists():
            with open(EVENTS_FILE) as f:
                events = json.load(f)
            for e in events:
                self._events[e["event_id"]] = e
            logger.info(f"Loaded {len(self._events)} existing events")

    def ingest(self, events: List[dict]) -> dict:
        accepted, rejected, duplicate = 0, 0, 0
        errors = []

        for event in events:
            try:
                eid = event.get("event_id")
                if not eid:
                    rejected += 1
                    errors.append({"error": "missing event_id", "event": event})
                    continue

                if eid in self._events:
                    duplicate += 1
                    continue

                # Validate required fields
                required = ["store_id", "camera_id", "visitor_id",
                           "event_type", "timestamp", "confidence"]
                missing = [f for f in required if f not in event]
                if missing:
                    rejected += 1
                    errors.append({"error": f"missing fields: {missing}",
                                  "event_id": eid})
                    continue

                self._events[eid] = event
                accepted += 1

            except Exception as ex:
                rejected += 1
                errors.append({"error": str(ex)})

        self._persist()
        return {
            "accepted": accepted,
            "rejected": rejected,
            "duplicate": duplicate,
            "errors": errors
        }

    def get_events(self, store_id: str = None,
                   event_type: str = None) -> List[dict]:
        events = list(self._events.values())
        if store_id:
            events = [e for e in events if e.get("store_id") == store_id]
        if event_type:
            events = [e for e in events if e.get("event_type") == event_type]
        return events

    def _persist(self):
        EVENTS_FILE.parent.mkdir(exist_ok=True)
        with open(EVENTS_FILE, "w") as f:
            json.dump(list(self._events.values()), f, indent=2)


# Singleton
_service = None

def get_ingestion_service() -> IngestionService:
    global _service
    if _service is None:
        _service = IngestionService()
    return _service
