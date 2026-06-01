"""
detection/camera_config.py

Camera-to-zone mapping for Brigade Road, Bangalore store.
5 cameras mapped to store layout zones based on CCTV footage thumbnails
and the Brigade Road floor plan (Brigade_Road_Store_layout.xlsx).

Store layout (left to right):
  Entry door → FOH (main floor) → Makeup Unit (island) → Cash Counter → PMU / Back

Camera assignments (inferred from footage thumbnails):
  CAM 1 — Wide aisle view, left side shelves (back wall brands: EB Korean, Face Shop etc.)
  CAM 2 — Product aisle / mid-floor (Swiss Beauty, Renee, Alps Goodness shelf area)
  CAM 3 — Entry/exit zone (overhead, green-tinted — typical entry cam)
  CAM 4 — Cash counter / billing area (right side)
  CAM 5 — Makeup Unit island / FOH center

Each camera config defines:
  - role: what this camera is responsible for
  - entry_line_x_ratio: if this camera covers entry, where to draw the virtual line
  - zones_covered: which store zones are visible
  - is_entry_cam: whether to count entries/exits from this cam
  - is_overhead: overhead cameras have different person detection parameters
"""

CAMERA_CONFIGS = {
    "CAM 1": {
        "role": "back_wall_shelves",
        "description": "Left wall shelves — EB Korean, The Face Shop, Good Vibes, DermDoc",
        "zones_covered": ["back_wall", "foh"],
        "is_entry_cam": False,
        "is_overhead": False,
        "entry_line_x_ratio": None,
        "staff_zone_x_ratio": None,
        "detect_zone_events": True,
        "zone_name": "back_wall",
    },
    "CAM 2": {
        "role": "product_aisle",
        "description": "Mid-floor product shelves — Swiss Beauty, Renee, Alps Goodness, Streax",
        "zones_covered": ["foh"],
        "is_entry_cam": False,
        "is_overhead": False,
        "entry_line_x_ratio": None,
        "staff_zone_x_ratio": None,
        "detect_zone_events": True,
        "zone_name": "foh",
    },
    "CAM 3": {
        "role": "entry_exit",
        "description": "Store entry/exit door — primary footfall counting camera",
        "zones_covered": ["entry", "foh"],
        "is_entry_cam": True,          # ← PRIMARY entry counting camera
        "is_overhead": True,           # overhead mount, adjust bbox expectations
        "entry_line_x_ratio": 0.45,   # line at center of this camera's frame
        "staff_zone_x_ratio": None,
        "detect_zone_events": False,
        "zone_name": "entry",
    },
    "CAM 4": {
        "role": "cash_counter",
        "description": "Cash counter / billing zone — right side of store",
        "zones_covered": ["cash_counter"],
        "is_entry_cam": False,
        "is_overhead": False,
        "entry_line_x_ratio": None,
        "staff_zone_x_ratio": 0.5,    # right half = staff area for this cam
        "detect_zone_events": True,
        "zone_name": "cash_counter",
    },
    "CAM 5": {
        "role": "makeup_unit",
        "description": "Central makeup island (F.O.H) — engagement zone",
        "zones_covered": ["makeup_unit", "foh"],
        "is_entry_cam": False,
        "is_overhead": False,
        "entry_line_x_ratio": None,
        "staff_zone_x_ratio": None,
        "detect_zone_events": True,
        "zone_name": "makeup_unit",
    },
}

# Canonical file name patterns to auto-detect camera from filename
CAMERA_FILENAME_PATTERNS = {
    "CAM 1": ["cam1", "cam_1", "camera1", "camera_1", "CAM 1", "CAM1", "footagecam 1", "footagecam1"],
    "CAM 2": ["cam2", "cam_2", "camera2", "camera_2", "CAM 2", "CAM2", "footagecam 2", "footagecam2"],
    "CAM 3": ["cam3", "cam_3", "camera3", "camera_3", "CAM 3", "CAM3", "footagecam 3", "footagecam3"],
    "CAM 4": ["cam4", "cam_4", "camera4", "camera_4", "CAM 4", "CAM4", "footagecam 4", "footagecam4"],
    "CAM 5": ["cam5", "cam_5", "camera5", "camera_5", "CAM 5", "CAM5", "footagecam 5", "footagecam5"],
}


def detect_camera_id(filename: str) -> str:
    """Auto-detect camera ID from filename. Defaults to CAM 1."""
    fname = filename.lower()
    for cam_id, patterns in CAMERA_FILENAME_PATTERNS.items():
        for p in patterns:
            if p.lower() in fname:
                return cam_id
    return "CAM 1"  # fallback


def get_camera_config(cam_id: str) -> dict:
    return CAMERA_CONFIGS.get(cam_id, CAMERA_CONFIGS["CAM 1"])
