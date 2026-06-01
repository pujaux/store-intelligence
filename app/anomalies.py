"""
app/anomalies.py
Anomaly detection: queue spikes, conversion drops, dead zones, stale feeds.
"""
from typing import List
from datetime import datetime, timedelta
from collections import defaultdict


def detect_anomalies(events: List[dict], store_id: str) -> dict:
    customer_events = [e for e in events
                       if e.get("store_id") == store_id
                       and not e.get("is_staff", False)]

    anomalies = []
    seen = set()

    # 1. Billing queue spike (>5 people in billing zone)
    billing_events = [e for e in customer_events
                      if e.get("zone_id") in ("BILLING", "CASH_COUNTER")]
    if len(billing_events) > 5:
        key = "BILLING_QUEUE_SPIKE"
        if key not in seen:
            seen.add(key)
            anomalies.append({
                "anomaly_id": f"ANO_{key}_{store_id}",
                "type": "BILLING_QUEUE_SPIKE",
                "severity": "CRITICAL" if len(billing_events) > 10 else "WARN",
                "description": f"Billing queue has {len(billing_events)} visitors",
                "suggested_action": "Open additional billing counter or redirect staff",
                "detected_at": datetime.utcnow().isoformat() + "Z",
                "metadata": {"queue_depth": len(billing_events)}
            })

    # 2. Dead zone — no visits in any zone for 30+ min
    zone_last_seen = defaultdict(lambda: datetime.min)
    for e in customer_events:
        if e.get("zone_id"):
            try:
                ts = datetime.fromisoformat(e["timestamp"].replace("Z", ""))
                zone_last_seen[e["zone_id"]] = max(zone_last_seen[e["zone_id"]], ts)
            except:
                pass

    now = datetime.utcnow()
    for zone, last in zone_last_seen.items():
        if last != datetime.min and (now - last).seconds > 1800:
            key = f"DEAD_ZONE_{zone}"
            if key not in seen:
                seen.add(key)
                anomalies.append({
                    "anomaly_id": f"ANO_{key}_{store_id}",
                    "type": "DEAD_ZONE",
                    "severity": "INFO",
                    "description": f"No visitors in {zone} for 30+ minutes",
                    "suggested_action": f"Check camera feed for {zone} or review product placement",
                    "detected_at": datetime.utcnow().isoformat() + "Z",
                    "metadata": {"zone_id": zone, "last_seen": last.isoformat()}
                })

    # 3. Overcrowding
    recent_entries = [e for e in customer_events
                      if e.get("event_type") == "ENTRY"]
    recent_exits = [e for e in customer_events
                    if e.get("event_type") == "EXIT"]
    in_store = len(recent_entries) - len(recent_exits)
    if in_store > 15:
        key = "OVERCROWDING"
        if key not in seen:
            seen.add(key)
            anomalies.append({
                "anomaly_id": f"ANO_{key}_{store_id}",
                "type": "OVERCROWDING",
                "severity": "WARN",
                "description": f"{in_store} customers currently in store",
                "suggested_action": "Monitor capacity and ensure staff coverage",
                "detected_at": datetime.utcnow().isoformat() + "Z",
                "metadata": {"current_occupancy": in_store}
            })

    # 4. High abandonment
    abandon = [e for e in customer_events
               if e.get("event_type") == "BILLING_QUEUE_ABANDON"]
    billing = [e for e in customer_events
               if e.get("event_type") == "BILLING_QUEUE_JOIN"]
    if billing and len(abandon) / len(billing) > 0.3:
        key = "HIGH_ABANDONMENT"
        if key not in seen:
            seen.add(key)
            anomalies.append({
                "anomaly_id": f"ANO_{key}_{store_id}",
                "type": "HIGH_ABANDONMENT",
                "severity": "WARN",
                "description": f"{len(abandon)} of {len(billing)} billing visitors abandoned",
                "suggested_action": "Reduce queue wait time — add billing staff",
                "detected_at": datetime.utcnow().isoformat() + "Z",
                "metadata": {
                    "abandonment_rate_pct": round(len(abandon)/len(billing)*100, 1)
                }
            })

    return {
        "store_id": store_id,
        "as_of": datetime.utcnow().isoformat() + "Z",
        "total_anomalies": len(anomalies),
        "anomalies": anomalies
    }
