"""Background worker: runs YOLO on uploaded video segments and creates draft detections.

Run with:  python -m app.worker
Deployed as the streetscan-worker systemd service.

The pilot uses a pretrained COCO model, so only street furniture that COCO
knows about is detected (fire hydrants, stop signs, traffic lights, benches,
parking meters). When a custom model trained on municipal assets (poles,
cabinets, manholes, water meters...) is available, point MODEL_PATH at its
weights and extend CLASS_MAP — the rest of the pipeline stays the same.
"""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import VideoSegment, GPSPoint, Detection, InfrastructureLayer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("streetscan.worker")

# COCO class name -> (infrastructure layer, asset type)
CLASS_MAP: dict[str, tuple[str, str]] = {
    "fire hydrant": ("water", "fire_hydrant"),
    "stop sign": ("road", "stop_sign"),
    "traffic light": ("road", "traffic_light"),
    "bench": ("public_space", "bench"),
    "parking meter": ("road", "parking_meter"),
}

# Two sightings of the same class within this window are treated as one object.
CLUSTER_GAP_S = 5.0
# A GPS point further than this from the frame time gives no coordinates.
GPS_TOLERANCE_S = 60.0


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class YoloDetector:
    """Thin wrapper so tests can substitute a fake detector."""

    def __init__(self, model_path: str):
        from ultralytics import YOLO  # imported lazily: heavy dependency
        self.model = YOLO(model_path)

    def detect(self, frame) -> list[tuple[str, float, tuple[int, int, int, int]]]:
        """Return (class_name, confidence, (x1, y1, x2, y2)) per detection."""
        results = self.model.predict(frame, verbose=False, conf=settings.detection_confidence)
        out = []
        for r in results:
            names = r.names
            for b in r.boxes:
                x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].tolist())
                out.append((names[int(b.cls[0])], float(b.conf[0]), (x1, y1, x2, y2)))
        return out


def save_snapshot(frame, box, label: str) -> str:
    snap_dir = Path(settings.upload_dir) / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    x1, y1, x2, y2 = box
    annotated = frame.copy()
    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 200, 255), 3)
    cv2.putText(annotated, label, (x1, max(y1 - 8, 16)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
    path = snap_dir / f"{uuid.uuid4().hex}.jpg"
    cv2.imwrite(str(path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return str(path)


def nearest_gps(points: list[GPSPoint], ts: datetime) -> GPSPoint | None:
    if not points:
        return None
    best = min(points, key=lambda p: abs((to_naive_utc(p.captured_at) - ts).total_seconds()))
    if abs((to_naive_utc(best.captured_at) - ts).total_seconds()) > GPS_TOLERANCE_S:
        return None
    return best


def cluster_sightings(sightings: list[dict]) -> list[dict]:
    """Group repeated sightings of the same class; keep the best-confidence one
    per cluster so a hydrant seen in 10 consecutive frames yields one draft."""
    kept: list[dict] = []
    by_class: dict[str, list[dict]] = {}
    for s in sightings:
        by_class.setdefault(s["name"], []).append(s)
    for items in by_class.values():
        items.sort(key=lambda s: s["t"])
        cluster: list[dict] = []
        for s in items:
            if cluster and s["t"] - cluster[-1]["t"] > CLUSTER_GAP_S:
                kept.append(max(cluster, key=lambda c: c["conf"]))
                cluster = []
            cluster.append(s)
        if cluster:
            kept.append(max(cluster, key=lambda c: c["conf"]))
    return kept


def open_capture(path: str) -> tuple[cv2.VideoCapture, int | None]:
    """Open a video and honour its rotation metadata. Phones record
    sensor-native landscape and mark portrait via metadata; without this,
    frames arrive sideways and detection quality collapses."""
    cap = cv2.VideoCapture(path)
    rotate_code = None
    if cap.isOpened():
        applied = cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
        meta = int(cap.get(cv2.CAP_PROP_ORIENTATION_META) or 0)
        if not applied and meta:
            rotate_code = {
                90: cv2.ROTATE_90_CLOCKWISE,
                180: cv2.ROTATE_180,
                270: cv2.ROTATE_90_COUNTERCLOCKWISE,
            }.get(meta)
    return cap, rotate_code


# screen.orientation.angle -> cv2 rotation that uprights the frame.
# angle 90 = device rotated counterclockwise, so content sits clockwise
# in the fixed buffer and needs a counterclockwise correction.
HINT_ROTATE = {
    90: cv2.ROTATE_90_COUNTERCLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_CLOCKWISE,
}


def process_segment(db, segment: VideoSegment, detector) -> int:
    cap, rotate_code = open_capture(segment.filename)
    if not cap.isOpened():
        log.warning("cannot open segment %s (%s)", segment.id, segment.filename)
        return 0
    if rotate_code is None and segment.orientation_hint:
        rotate_code = HINT_ROTATE.get(segment.orientation_hint)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 0:
        fps = 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if total_frames > 0 else 0.0
    # captured_at is stamped when the segment finishes recording
    start_time = to_naive_utc(segment.captured_at) - timedelta(seconds=duration)

    stride = max(1, int(round(fps * settings.frame_stride_s)))
    sightings: list[dict] = []
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % stride == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                if rotate_code is not None:
                    frame = cv2.rotate(frame, rotate_code)
                for name, conf, box in detector.detect(frame):
                    if name in CLASS_MAP and conf >= settings.detection_confidence:
                        sightings.append({"t": idx / fps, "name": name, "conf": conf,
                                          "frame": frame.copy(), "box": box})
        idx += 1
    cap.release()

    gps_points = db.scalars(
        select(GPSPoint).where(GPSPoint.route_id == segment.route_id)
    ).all()

    created = 0
    for s in cluster_sightings(sightings):
        layer, asset_type = CLASS_MAP[s["name"]]
        frame_time = start_time + timedelta(seconds=s["t"])
        point = nearest_gps(gps_points, frame_time)
        snapshot = save_snapshot(s["frame"], s["box"], f"{asset_type} {s['conf']:.2f}")
        db.add(Detection(
            route_id=segment.route_id,
            video_segment_id=segment.id,
            proposed_asset_type=asset_type,
            proposed_layer=InfrastructureLayer(layer),
            confidence=round(s["conf"], 3),
            latitude=point.latitude if point else None,
            longitude=point.longitude if point else None,
            snapshot_path=snapshot,
        ))
        created += 1
    return created


def run_once(detector) -> int:
    """Process all pending segments; returns number of detections created."""
    total = 0
    with SessionLocal() as db:
        pending = db.scalars(
            select(VideoSegment).where(VideoSegment.processed.is_(False))
            .order_by(VideoSegment.id).limit(20)
        ).all()
        for segment in pending:
            try:
                n = process_segment(db, segment, detector)
                log.info("segment %s -> %d detections", segment.id, n)
                total += n
            except Exception:
                log.exception("segment %s failed; skipping", segment.id)
            segment.processed = True  # never reprocess a poison segment
            db.commit()
    return total


def main():
    log.info("loading model %s", settings.model_path)
    detector = YoloDetector(settings.model_path)
    log.info("worker started (poll every %ss)", settings.worker_poll_s)
    while True:
        try:
            run_once(detector)
        except Exception:
            log.exception("worker cycle failed")
        time.sleep(settings.worker_poll_s)


if __name__ == "__main__":
    main()
