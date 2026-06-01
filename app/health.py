"""
app/health.py
Health endpoint — service status, last event timestamp, stale feed detection.
"""
from datetime import datetime, timedelta
from typing import List
from collections import defaultdict


def get_health(events: List[dict]) -> dict:
    now = datetime.utcnow()
    
    # Last event per store
    store_last_event = defaultdict(lambda: None)
    for e in events:
        sid = e.get("store_id")
        ts_str = e.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", ""))
            if store_last_event[sid] is None or ts > store_last_event[sid]:
                store_last_event[sid] = ts
        except:
            pass

    store_status = {}
    for store_id, last_ts in store_last_event.items():
        if last_ts is None:
            feed_status = "NO_DATA"
        elif (now - last_ts).seconds > 600:
            feed_status = "STALE_FEED"
        else:
            feed_status = "OK"

        store_status[store_id] = {
            "last_event_timestamp": last_ts.isoformat() + "Z" if last_ts else None,
            "feed_status": feed_status,
            "lag_seconds": int((now - last_ts).total_seconds()) if last_ts else None
        }

    overall = "healthy"
    if any(s["feed_status"] == "STALE_FEED" for s in store_status.values()):
        overall = "degraded"

    return {
        "status": overall,
        "timestamp": now.isoformat() + "Z",
        "version": "1.0.0",
        "stores": store_status,
        "total_events_ingested": len(events)
    }
