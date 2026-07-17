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
from app.models.entities import (
    VideoSegment, CapturedImage, GPSPoint, Detection, Business, InfrastructureLayer,
    AssetCategory, CandidateAsset, CandidateStatus,
)
from app import ocr as ocr_mod
from app.engine import DefaultAssetAnalysisEngine, CaptureContext, CategoryPrompt
from app.engine.adapters.yolo import YoloDetector as EngineYoloAdapter
from app.engine.adapters.ocr import LocalOCREngine
from app.engine.hardware import detect_hardware


def build_openvocab_engine(db):
    """On-demand open-vocabulary engine (OWL-ViT) for infrastructure categories
    that fixed-class YOLO can't see. ~1GB peak, loaded only when there's work."""
    from app.engine.adapters.openvocab import OpenVocabDetector
    cats = db.scalars(select(AssetCategory).where(
        AssetCategory.active, AssetCategory.active_detector == "openvocab")).all()
    prompts = [CategoryPrompt(
        name=c.name, infrastructure_layer=c.infrastructure_layer,
        prompts=c.detection_prompts.split("\n"), min_confidence=c.min_confidence,
        active_detector=c.active_detector, requires_validation=c.requires_validation,
    ) for c in cats]
    return DefaultAssetAnalysisEngine(
        detector=OpenVocabDetector(threshold=0.04), categories=prompts, ocr=LocalOCREngine(),
        crops_dir=str(Path(settings.upload_dir) / "crops"),
        annotated_dir=str(Path(settings.upload_dir) / "annotated"),
    )

BAND_HIGH, BAND_MED = 0.10, 0.06


def _band(score: float) -> str:
    return "high" if score >= BAND_HIGH else "medium" if score >= BAND_MED else "low"


def build_engine(db):
    """Compose the model-independent engine from DB config. Continuous on-server
    analysis uses the CPU-safe YOLO adapter (COCO now; a registered municipal
    model later drops in here unchanged)."""
    cats = db.scalars(select(AssetCategory).where(AssetCategory.active)).all()
    prompts = [CategoryPrompt(
        name=c.name, infrastructure_layer=c.infrastructure_layer,
        prompts=c.detection_prompts.split("\n"), min_confidence=c.min_confidence,
        active_detector=c.active_detector, requires_validation=c.requires_validation,
    ) for c in cats]
    hw = detect_hardware()
    if hw.warning:
        log.info("hardware: %s", hw.warning)
    detector = EngineYoloAdapter(model_path=settings.model_path)
    return DefaultAssetAnalysisEngine(
        detector=detector, categories=prompts, ocr=LocalOCREngine(),
        crops_dir=str(Path(settings.upload_dir) / "crops"),
        annotated_dir=str(Path(settings.upload_dir) / "annotated"),
    )


def process_image_engine(db, image: CapturedImage, engine) -> int:
    ctx = CaptureContext(
        image_id=image.id, route_id=image.route_id, capture_type=image.kind,
        latitude=image.latitude, longitude=image.longitude, heading_deg=image.heading_deg,
        quality_score=image.blur_score, source="image",
    )
    result = engine.analyze_image(image.filename, ctx)
    for a in result.assets:
        db.add(CandidateAsset(
            image_id=image.id, route_id=image.route_id, image_sequence=image.id,
            capture_type=image.kind, proposed_category=a.proposed_category,
            infrastructure_layer=a.infrastructure_layer, confidence=a.confidence,
            bbox=",".join(str(round(x, 1)) for x in a.box), crop_path=a.crop_path,
            annotated_path=result.annotated_path, ocr_text=a.ocr_text,
            ocr_language=a.ocr_language, ocr_confidence=a.ocr_confidence,
            condition=a.condition, defect=a.defect, quality_score=image.blur_score,
            detector_name=a.detector_name, detector_version=a.detector_version,
            latitude=image.latitude, longitude=image.longitude,
            confidence_band=_band(a.confidence), processing_ms=result.processing_ms,
            status=CandidateStatus.PENDING_VALIDATION,
        ))
    return len(result.assets)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("streetscan.worker")

# COCO class name -> (infrastructure layer, asset type)
CLASS_MAP: dict[str, tuple[str, str]] = {
    # Pilot focus is electricity, telecom and stores. COCO has no classes for
    # utility poles, telecom cabinets or storefronts, so YOLO object detection
    # stays quiet here until a custom-trained model is dropped in via
    # settings.model_path. Storefront/business recognition runs via the OCR
    # path (see ocr_worker), not this map.
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


def clip_duration_s(cap, fps: float) -> float:
    """Segment length in seconds, guarding against webm files whose frame
    count is unreliable/garbage (MediaRecorder writes no duration header) —
    a huge value would overflow timedelta. Falls back to 0 (frames ~= end
    time) when the count is implausible for a ~15s clip."""
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps if total > 0 else 0.0
    return duration if 0 <= duration <= 600 else 0.0


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
    duration = clip_duration_s(cap, fps)
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


def process_image(db, image: CapturedImage, detector) -> int:
    """High-res stills come with their own GPS/heading, so detection is a
    single inference — no clustering or time matching needed."""
    frame = cv2.imread(image.filename)
    if frame is None:
        log.warning("cannot read image %s (%s)", image.id, image.filename)
        return 0
    created = 0
    for name, conf, box in detector.detect(frame):
        if name not in CLASS_MAP or conf < settings.detection_confidence:
            continue
        layer, asset_type = CLASS_MAP[name]
        snapshot = save_snapshot(frame, box, f"{asset_type} {conf:.2f}")
        db.add(Detection(
            route_id=image.route_id,
            image_id=image.id,
            proposed_asset_type=asset_type,
            proposed_layer=InfrastructureLayer(layer),
            confidence=round(conf, 3),
            latitude=image.latitude,
            longitude=image.longitude,
            snapshot_path=snapshot,
        ))
        created += 1
    return created


def run_once(detector) -> int:
    """Process all pending segments and images; returns detections created."""
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

        pending_images = db.scalars(
            select(CapturedImage).where(CapturedImage.processed.is_(False))
            .order_by(CapturedImage.id).limit(50)
        ).all()
        for image in pending_images:
            try:
                n = process_image(db, image, detector)
                log.info("image %s -> %d detections", image.id, n)
                total += n
            except Exception:
                log.exception("image %s failed; skipping", image.id)
            image.processed = True
            db.commit()

        # OCR pass: read storefront signs into draft businesses.
        for image in db.scalars(
            select(CapturedImage).where(CapturedImage.ocr_processed.is_(False))
            .order_by(CapturedImage.id).limit(50)
        ).all():
            try:
                n = process_image_ocr(db, image)
                if n:
                    log.info("image %s -> %d business drafts", image.id, n)
            except Exception:
                log.exception("ocr on image %s failed; skipping", image.id)
            image.ocr_processed = True
            db.commit()

        # OCR pass over video frames — the clips capture far more signage
        # than the stop stills.
        for segment in db.scalars(
            select(VideoSegment).where(VideoSegment.ocr_processed.is_(False))
            .order_by(VideoSegment.id).limit(20)
        ).all():
            try:
                n = process_segment_ocr(db, segment)
                if n:
                    log.info("segment %s -> %d business drafts (video OCR)", segment.id, n)
            except Exception:
                log.exception("video OCR on segment %s failed; skipping", segment.id)
            segment.ocr_processed = True
            db.commit()

        # Production asset-analysis engine: automatically analyze every new
        # captured image and persist candidate assets (continuous, on-server).
        if _ENGINE[0] is None:
            _ENGINE[0] = build_engine(db)
        for image in db.scalars(
            select(CapturedImage).where(CapturedImage.engine_processed.is_(False))
            .order_by(CapturedImage.id).limit(40)
        ).all():
            try:
                n = process_image_engine(db, image, _ENGINE[0])
                if n:
                    log.info("image %s -> %d candidate assets (engine)", image.id, n)
                    total += n
            except Exception:
                log.exception("engine analysis on image %s failed; skipping", image.id)
            image.engine_processed = True
            db.commit()

        # On-demand OWL-ViT infrastructure detection (button-triggered). Loads
        # the ~1GB model only when there's work; unloads when the queue drains
        # so the shared box gets its memory back.
        ov_pending = db.scalars(
            select(CapturedImage).where(CapturedImage.openvocab_processed.is_(False))
            .order_by(CapturedImage.id).limit(5)
        ).all()
        if ov_pending:
            if _OVENGINE[0] is None:
                log.info("loading OWL-ViT for on-demand infrastructure detection...")
                _OVENGINE[0] = build_openvocab_engine(db)
            for image in ov_pending:
                try:
                    n = process_image_engine(db, image, _OVENGINE[0])
                    if n:
                        log.info("image %s -> %d infrastructure candidates (owlvit)", image.id, n)
                        total += n
                except Exception:
                    log.exception("owlvit analysis on image %s failed; skipping", image.id)
                image.openvocab_processed = True
                db.commit()
        elif _OVENGINE[0] is not None:
            import gc
            _OVENGINE[0] = None
            gc.collect()
            log.info("OWL-ViT queue drained; model unloaded (memory freed)")
    return total


_ENGINE: list = [None]     # lazily-built YOLO engine (continuous)
_OVENGINE: list = [None]   # lazily-built OWL-ViT engine (on-demand, unloaded when idle)


def blur_variance(frame) -> float:
    return cv2.Laplacian(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()


def norm_name(name: str) -> str:
    return "".join(name.lower().split())


def _keep_ocr(result: dict) -> bool:
    # cut noise: keep a recognized category, or a confident, wordy name
    return (result["category"] != "unknown"
            or (result["confidence"] >= 0.6 and len(result["name"]) >= 5))


def process_segment_ocr(db, segment: VideoSegment) -> int:
    """Sample frames from a video, OCR sign text, create draft businesses.
    Skips blurry frames, dedups repeats within the clip, keeps only recognized
    categories or confident multi-letter names."""
    cap, rotate_code = open_capture(segment.filename)
    if not cap.isOpened():
        return 0
    if rotate_code is None and segment.orientation_hint:
        rotate_code = HINT_ROTATE.get(segment.orientation_hint)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if fps <= 0:
        fps = 30.0
    duration = clip_duration_s(cap, fps)
    start_time = to_naive_utc(segment.captured_at) - timedelta(seconds=duration)
    gps_points = db.scalars(select(GPSPoint).where(GPSPoint.route_id == segment.route_id)).all()

    stride = max(1, int(round(fps * settings.ocr_frame_stride_s)))
    seen: set[str] = set()
    created = 0
    idx = 0
    while True:
        if not cap.grab():
            break
        if idx % stride == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None:
                if rotate_code is not None:
                    frame = cv2.rotate(frame, rotate_code)
                if blur_variance(frame) >= settings.ocr_blur_min:
                    result = ocr_mod.run_ocr_image(frame)
                    if result and _keep_ocr(result):
                        key = norm_name(result["name"])
                        if key not in seen:
                            seen.add(key)
                            frame_time = start_time + timedelta(seconds=idx / fps)
                            point = nearest_gps(gps_points, frame_time)
                            snap_dir = Path(settings.upload_dir) / "snapshots"
                            snap_dir.mkdir(parents=True, exist_ok=True)
                            snap = str(snap_dir / f"vbiz_{uuid.uuid4().hex}.jpg")
                            cv2.imwrite(snap, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                            db.add(Business(
                                route_id=segment.route_id, name=result["name"],
                                category=result["category"], ocr_text=result["text"],
                                languages=result["languages"], confidence=result["confidence"],
                                latitude=point.latitude if point else None,
                                longitude=point.longitude if point else None,
                                snapshot_path=snap,
                            ))
                            created += 1
        idx += 1
    cap.release()
    return created


def process_image_ocr(db, image: CapturedImage) -> int:
    result = ocr_mod.run_ocr(image.filename)
    if not result:
        return 0
    snapshot = None
    frame = cv2.imread(image.filename)
    if frame is not None:
        snap_dir = Path(settings.upload_dir) / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snapshot = str(snap_dir / f"biz_{uuid.uuid4().hex}.jpg")
        cv2.imwrite(snapshot, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    db.add(Business(
        route_id=image.route_id,
        image_id=image.id,
        name=result["name"],
        category=result["category"],
        ocr_text=result["text"],
        languages=result["languages"],
        confidence=result["confidence"],
        latitude=image.latitude,
        longitude=image.longitude,
        heading_deg=image.heading_deg,
        snapshot_path=snapshot,
    ))
    return 1


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
