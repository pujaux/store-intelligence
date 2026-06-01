"""
app/models.py
Pydantic models matching the exact event schema from the problem statement.
"""
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
import uuid


class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: Optional[int] = None


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str  # ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY
    timestamp: str
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float
    metadata: EventMetadata = Field(default_factory=EventMetadata)


class IngestRequest(BaseModel):
    events: List[StoreEvent]


class IngestResponse(BaseModel):
    accepted: int
    rejected: int
    duplicate: int
    errors: List[dict] = []
