"""
detection/pipeline.py

Multi-camera detection and tracking pipeline for Store Intelligence System.
Supports 5 CCTV cameras (CAM 1–5) covering different zones of the
Brigade Road, Bangalore store.

Design decisions (see CHOICES.md):
- YOLOv8n for speed vs accuracy trade-off on CPU
- Centroid tracker per camera (independent tracking contexts)
- Only CAM 3 (entry camera) generates entry/exit events
- Other cameras generate zone_enter events for funnel tracking
- 30-second re-entry window prevents double-counting
- Staff excluded via cash-counter zone dwell (CAM 4)
- All cameras write to the same events.json (merged with camera_id tag)
"""

import cv2
import json
import uuid
import time
import logging
import numpy as np
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict, field
from collections import defaultdict
from typing import Optional

from detection.camera_config import detect_camera_id, get_camera_config

logger = logging.getLogger(__name__)

# Staff detection threshold
STAFF_MIN_DWELL_SEC = 300    # 5 minutes at cash counter = staff
REENTRY_WINDOW_SEC  = 30    # re-crossing entry line within 30s = same visit


@dataclass
class PersonTrack:
    track_id: str
    camera_id: str
    first_seen: float
    last_seen: float
    centroid_x: float
    centroid_y: float
    crossed_entry: bool = False
    crossed_entry_time: Optional[float] = None
    exited: bool = False
    exit_time: Optional[float] = None
    is_staff: bool = False
    zone_dwell: dict = field(default_factory=lambda: defaultdict(float))
    _last_zone: Optional[str] = field(default=None, repr=False)


@dataclass
class StoreEvent:
    event_id: str
    event_type: str          # entry | exit | zone_enter | anomaly
    track_id: str
    camera_id: str
    timestamp: str
    frame_number: int
    confidence: float
    zone: Optional[str]
    metadata: dict

    def to_dict(self):
        return asdict(self)


class CentroidTracker:
    def __init__(self, max_disappeared=40, max_distance=100):
        self.next_id = 0
        self.objects = {}
        self.disappeared = {}
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance

    def register(self, centroid):
        tid = f"T{self.next_id:04d}"
        self.objects[tid] = centroid
        self.disappeared[tid] = 0
        self.next_id += 1
        return tid

    def deregister(self, tid):
        del self.objects[tid]
        del self.disappeared[tid]

    def update(self, rects):
        if not rects:
            for tid in list(self.disappeared):
                self.disappeared[tid] += 1
                if self.disappeared[tid] > self.max_disappeared:
                    self.deregister(tid)
            return {}

        centroids = np.array(
            [((r[0]+r[2])//2, (r[1]+r[3])//2) for r in rects], dtype="float"
        )

        if not self.objects:
            for c in centroids:
                self.register(tuple(c))
        else:
            ids = list(self.objects.keys())
            obj_c = np.array(list(self.objects.values()), dtype="float")
            D = np.linalg.norm(obj_c[:, None] - centroids[None, :], axis=2)
            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]
            used_rows, used_cols = set(), set()

            for row, col in zip(rows, cols):
                if row in used_rows or col in used_cols:
                    continue
                if D[row, col] > self.max_distance:
                    continue
                tid = ids[row]
                self.objects[tid] = tuple(centroids[col])
                self.disappeared[tid] = 0
                used_rows.add(row); used_cols.add(col)

            for row in set(range(len(ids))) - used_rows:
                self.disappeared[ids[row]] += 1
                if self.disappeared[ids[row]] > self.max_disappeared:
                    self.deregister(ids[row])

            for col in set(range(len(centroids))) - used_cols:
                self.register(tuple(centroids[col]))

        return dict(self.objects)


class CameraPipeline:
    """
    Processes a single camera's footage file.
    Camera role determines which events are generated.
    """

    def __init__(self, footage_path: str, camera_id: str,
                 events: list, visitor_ids: set,
                 exit_times: dict, confidence: float = 0.4):
        self.footage_path = footage_path
        self.camera_id = camera_id
        self.config = get_camera_config(camera_id)
        self.events = events           # shared list (appended to)
        self.visitor_ids = visitor_ids # shared set
        self.exit_times = exit_times   # shared dict (cross-camera re-entry guard)
        self.confidence = confidence

        self.tracker = CentroidTracker()
        self.tracks: dict[str, PersonTrack] = {}

        self._frame_w = 0
        self._frame_h = 0
        self._fps = 30
        self._frame_num = 0

    def run(self) -> dict:
        try:
            from ultralytics import YOLO
            model = YOLO("yolov8n.pt")
        except Exception as e:
            logger.error(f"[{self.camera_id}] YOLO load failed: {e}")
            raise

        cap = cv2.VideoCapture(self.footage_path)
        if not cap.isOpened():
            raise ValueError(f"Cannot open: {self.footage_path}")

        self._fps = cap.get(cv2.CAP_PROP_FPS) or 30
        self._frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        logger.info(f"[{self.camera_id}] {self.config['role']} | "
                    f"{self._frame_w}x{self._frame_h}@{self._fps:.0f}fps")

        skip = 2
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            self._frame_num += 1
            if self._frame_num % skip != 0:
                continue
            self._process_frame(frame, model)

        cap.release()
        self._finalize_exits()

        entries = [e for e in self.events
                   if e.camera_id == self.camera_id and e.event_type == "entry"]
        return {
            "camera_id": self.camera_id,
            "role": self.config["role"],
            "frames_processed": self._frame_num // skip,
            "entries_counted": len(entries),
            "events_generated": len([e for e in self.events
                                     if e.camera_id == self.camera_id]),
        }

    def _process_frame(self, frame, model):
        now = self._frame_num / self._fps
        cfg = self.config

        results = model(frame, classes=[0], conf=self.confidence, verbose=False)
        boxes = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                boxes.append((x1, y1, x2, y2))

        tracked = self.tracker.update(boxes)

        for tid_local, (cx, cy) in tracked.items():
            cx, cy = int(cx), int(cy)
            # Prefix track ID with camera to avoid cross-camera collisions
            tid = f"{self.camera_id.replace(' ', '')}_{tid_local}"

            if tid not in self.tracks:
                self.tracks[tid] = PersonTrack(
                    track_id=tid, camera_id=self.camera_id,
                    first_seen=now, last_seen=now,
                    centroid_x=cx, centroid_y=cy,
                )

            track = self.tracks[tid]
            prev_x = track.centroid_x
            track.centroid_x = cx
            track.centroid_y = cy
            track.last_seen = now
            zone = cfg["zone_name"]
            track.zone_dwell[zone] += (1 / self._fps) * 2

            # ── Staff detection (CAM 4 — cash counter) ──
            if cfg["role"] == "cash_counter":
                staff_x = int(self._frame_w * (cfg.get("staff_zone_x_ratio") or 0.5))
                if cx > staff_x and track.zone_dwell.get(zone, 0) > STAFF_MIN_DWELL_SEC:
                    if not track.is_staff:
                        track.is_staff = True
                        logger.debug(f"[{self.camera_id}] Staff: {tid}")
            if track.is_staff:
                continue

            # ── Entry/Exit counting (CAM 3 only) ──
            if cfg["is_entry_cam"] and cfg.get("entry_line_x_ratio"):
                entry_x = int(self._frame_w * cfg["entry_line_x_ratio"])

                if not track.crossed_entry:
                    if prev_x < entry_x <= cx:
                        last_exit = self.exit_times.get(tid)
                        if last_exit and (now - last_exit) < REENTRY_WINDOW_SEC:
                            logger.debug(f"[{self.camera_id}] Re-entry skip: {tid}")
                        else:
                            track.crossed_entry = True
                            track.crossed_entry_time = now
                            self.visitor_ids.add(tid)
                            self._emit("entry", tid, now, "entry", 0.9)

                if track.crossed_entry and not track.exited:
                    if prev_x > entry_x >= cx:
                        track.exited = True
                        track.exit_time = now
                        self.exit_times[tid] = now
                        dwell = now - (track.crossed_entry_time or track.first_seen)
                        self._emit("exit", tid, now, "entry", 0.9,
                                   metadata={"dwell_seconds": round(dwell, 1)})

            # ── Zone events (non-entry cameras) ──
            elif cfg["detect_zone_events"]:
                prev_zone = track._last_zone
                if zone != prev_zone:
                    self._emit("zone_enter", tid, now, zone, 0.8)
                track._last_zone = zone

            # ── Anomaly: overcrowding ──
            in_frame = len([t for t in self.tracks.values()
                            if not t.is_staff and abs(t.last_seen - now) < 3])
            if in_frame > 12:
                self._emit("anomaly", tid, now, zone, 1.0,
                           metadata={"type": "overcrowding", "count": in_frame,
                                     "camera": self.camera_id})

    def _finalize_exits(self):
        end = self._frame_num / self._fps
        for tid, track in self.tracks.items():
            if (self.config["is_entry_cam"]
                    and track.crossed_entry
                    and not track.exited
                    and not track.is_staff):
                track.exited = True
                dwell = end - (track.crossed_entry_time or track.first_seen)
                self._emit("exit", tid, end, "entry", 0.7,
                           metadata={"dwell_seconds": round(dwell, 1),
                                     "note": "inferred_at_video_end"})

    def _emit(self, event_type, track_id, ts, zone,
              confidence=0.9, metadata=None):
        self.events.append(StoreEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            track_id=track_id,
            camera_id=self.camera_id,
            timestamp=datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ"),
            frame_number=self._frame_num,
            confidence=confidence,
            zone=zone,
            metadata=metadata or {},
        ))


class StorePipeline:
    """
    Orchestrates processing of all camera files found in footage_dir.
    Processes each camera sequentially and merges events into one file.
    """

    def __init__(self, footage_dir: str, events_dir: str, confidence: float = 0.4):
        self.footage_dir = Path(footage_dir)
        self.events_dir = Path(events_dir)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.confidence = confidence

        # Shared state across all cameras
        self._events: list[StoreEvent] = []
        self._visitor_ids: set = set()
        self._exit_times: dict = {}

    def run(self) -> dict:
        """Find all video files, process each camera, save merged events."""
        video_files = sorted([
            f for f in self.footage_dir.iterdir()
            if f.suffix.lower() in (".mp4", ".avi", ".mov", ".mkv", ".ts")
        ])

        if not video_files:
            logger.warning(f"No video files found in {self.footage_dir}")
            return {"error": "no_footage_found"}

        logger.info(f"Found {len(video_files)} video file(s): "
                    f"{[f.name for f in video_files]}")

        camera_results = []
        for vf in video_files:
            cam_id = detect_camera_id(vf.name)
            logger.info(f"Processing {vf.name} as {cam_id}")
            try:
                cam = CameraPipeline(
                    footage_path=str(vf),
                    camera_id=cam_id,
                    events=self._events,
                    visitor_ids=self._visitor_ids,
                    exit_times=self._exit_times,
                    confidence=self.confidence,
                )
                result = cam.run()
                camera_results.append(result)
            except Exception as e:
                logger.error(f"Camera {cam_id} failed: {e}", exc_info=True)
                camera_results.append({"camera_id": cam_id, "error": str(e)})

        summary = self._build_summary(camera_results)
        self._save_events()
        return summary

    # Also support single-file mode (used by /process endpoint)
    def run_single(self, footage_path: str) -> dict:
        cam_id = detect_camera_id(Path(footage_path).name)
        cam = CameraPipeline(
            footage_path=footage_path,
            camera_id=cam_id,
            events=self._events,
            visitor_ids=self._visitor_ids,
            exit_times=self._exit_times,
            confidence=self.confidence,
        )
        result = cam.run()
        summary = self._build_summary([result])
        self._save_events()
        return summary

    def _build_summary(self, camera_results: list) -> dict:
        entries = [e for e in self._events if e.event_type == "entry"]
        exits   = [e for e in self._events if e.event_type == "exit"]
        dwell_times = [
            e.metadata.get("dwell_seconds", 0)
            for e in exits if e.metadata.get("dwell_seconds")
        ]
        avg_dwell = round(sum(dwell_times)/len(dwell_times), 1) if dwell_times else 0

        return {
            "cameras_processed": len(camera_results),
            "camera_results": camera_results,
            "total_entries": len(entries),
            "total_exits": len(exits),
            "unique_visitors": len(self._visitor_ids),
            "avg_dwell_seconds": avg_dwell,
            "total_events": len(self._events),
        }

    def _save_events(self):
        path = self.events_dir / "events.json"
        with open(path, "w") as f:
            json.dump([e.to_dict() for e in self._events], f, indent=2)
        logger.info(f"Saved {len(self._events)} events → {path}")
