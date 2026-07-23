"""Background worker (streetscan-worker systemd service). Run: python -m app.worker

Each cycle processes pending captures through:
  * business OCR (Tesseract) on stop stills + video frames
  * the AssetAnalysisEngine — YOLO adapter continuously on new images, and the
    OWL-ViT open-vocabulary adapter on demand (button-triggered route jobs)
The active detection model is settings.model_path (a registered municipal model
when trained; falls back to pretrained weights otherwise).
"""
import logging
import math
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
from sqlalchemy import select, func

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.entities import (
    VideoSegment, CapturedImage, GPSPoint, Business,
    AssetCategory, CandidateAsset, CandidateStatus, AnalysisJob,
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

# Detectors score on incomparable scales: OWL-ViT open-vocab logits sit near
# 0.05, a trained YOLO reports 0.3-1.0. Banding both with one threshold made
# every YOLO hit "high" and the band useless for review triage.
BANDS = {"owlvit": (0.10, 0.06), "yolo": (0.70, 0.45)}


def _band(score: float, detector: str = "yolo") -> str:
    high, med = BANDS.get(detector.split("-")[0].lower(), BANDS["yolo"])
    return "high" if score >= high else "medium" if score >= med else "low"


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
            confidence_band=_band(a.confidence, a.detector_name), processing_ms=result.processing_ms,
            status=CandidateStatus.PENDING_VALIDATION,
        ))
    return len(result.assets)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("streetscan.worker")

# A GPS point further than this from the frame time gives no coordinates.
GPS_TOLERANCE_S = 60.0


def to_naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def nearest_gps(points: list[GPSPoint], ts: datetime) -> GPSPoint | None:
    # only consider fixes inside the survey area; a stray far-away fix must not
    # geolocate an asset (it would land the asset ~165km off the map)
    points = [p for p in points if settings.in_survey_area(p.latitude, p.longitude)]
    if not points:
        return None
    best = min(points, key=lambda p: abs((to_naive_utc(p.captured_at) - ts).total_seconds()))
    if abs((to_naive_utc(best.captured_at) - ts).total_seconds()) > GPS_TOLERANCE_S:
        return None
    return best


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


def run_once() -> int:
    """Process pending captures: business OCR (images + video frames) and the
    asset-analysis engine (YOLO continuous + OWL-ViT on demand)."""
    total = 0
    with SessionLocal() as db:
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

        # Engine pass over video frames. The clips are the richest source of
        # assets AND they carry a GPS track, so unlike the imported stills these
        # detections land on the map. One segment per cycle keeps memory flat.
        for segment in db.scalars(
            select(VideoSegment).where(VideoSegment.processed.is_(False))
            .order_by(VideoSegment.id).limit(1)
        ).all():
            try:
                n = process_segment_engine(db, segment, _ENGINE[0])
                log.info("segment %s -> %d candidate assets (video engine)", segment.id, n)
                total += n
            except Exception:
                log.exception("engine analysis on segment %s failed; skipping", segment.id)
            segment.processed = True
            db.commit()

        # On-demand OWL-ViT infrastructure detection (button-triggered). Loads
        # the ~1GB model only when there's work; unloads when the queue drains
        # so the shared box gets its memory back.
        ov_pending = db.scalars(
            select(CapturedImage).where(CapturedImage.openvocab_processed.is_(False))
            .order_by(CapturedImage.id).limit(5)
        ).all()
        if ov_pending:
            route_ids = {image.route_id for image in ov_pending}
            for job in db.scalars(select(AnalysisJob).where(
                AnalysisJob.job_type == "route",
                AnalysisJob.target_route_id.in_(route_ids),
                AnalysisJob.status.in_(["queued", "running"]),
            )).all():
                job.status = "running"
            db.commit()

            if _OVENGINE[0] is None:
                log.info("loading OWL-ViT for on-demand infrastructure detection...")
                try:
                    _OVENGINE[0] = build_openvocab_engine(db)
                except Exception as exc:
                    log.exception("failed loading OWL-ViT")
                    for job in db.scalars(select(AnalysisJob).where(
                        AnalysisJob.job_type == "route",
                        AnalysisJob.target_route_id.in_(route_ids),
                        AnalysisJob.status.in_(["queued", "running"]),
                    )).all():
                        job.status = "failed"
                        job.detail = f"OWL-ViT load failed: {type(exc).__name__}: {exc}"
                        job.finished_at = datetime.utcnow()
                    # Mark this batch processed so a bad model installation does
                    # not create an endless hot loop. Re-run after fixing deps.
                    for image in ov_pending:
                        image.openvocab_processed = True
                    db.commit()
                    return total

            for image in ov_pending:
                try:
                    n = process_image_engine(db, image, _OVENGINE[0])
                    if n:
                        log.info("image %s -> %d infrastructure candidates (owlvit)", image.id, n)
                        total += n
                except Exception as exc:
                    log.exception("owlvit analysis on image %s failed; skipping", image.id)
                    job = db.scalar(select(AnalysisJob).where(
                        AnalysisJob.job_type == "route",
                        AnalysisJob.target_route_id == image.route_id,
                        AnalysisJob.status.in_(["queued", "running"]),
                    ).order_by(AnalysisJob.id.desc()))
                    if job:
                        job.detail = f"Image {image.id} failed: {type(exc).__name__}: {exc}"
                image.openvocab_processed = True
                db.commit()

            # Persist progress and close jobs whose route queue is empty.
            for route_id in route_ids:
                job = db.scalar(select(AnalysisJob).where(
                    AnalysisJob.job_type == "route",
                    AnalysisJob.target_route_id == route_id,
                    AnalysisJob.status.in_(["queued", "running"]),
                ).order_by(AnalysisJob.id.desc()))
                if not job:
                    continue
                remaining = db.scalar(select(func.count()).select_from(CapturedImage).where(
                    CapturedImage.route_id == route_id,
                    CapturedImage.openvocab_processed.is_(False),
                )) or 0
                job.done = max(0, job.total - remaining)
                job.candidates_created = db.scalar(select(func.count()).select_from(CandidateAsset).where(
                    CandidateAsset.route_id == route_id,
                    CandidateAsset.detector_name == "owlvit",
                )) or 0
                if remaining == 0:
                    job.status = "done"
                    job.finished_at = datetime.utcnow()
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


# A driving camera sees the same pole in many consecutive frames. Two hits of
# the same category within this distance are treated as one physical asset.
SAME_ASSET_M = 12.0
# Sample rate for the engine pass — at ~30 km/h this is a frame every ~12 m.
ENGINE_FRAME_STRIDE_S = 1.5


def meters_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Flat-earth approximation — fine at street scale."""
    dlat = (a[0] - b[0]) * 111_320.0
    dlng = (a[1] - b[1]) * 111_320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


def process_segment_engine(db, segment: VideoSegment, engine) -> int:
    """Run the asset engine over sampled frames of a clip and persist candidates
    positioned from the route's GPS track. Frames with no GPS fix within
    tolerance are skipped: an unmappable candidate is what we already have too
    many of."""
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
    if not gps_points:
        return 0

    stride = max(1, int(round(fps * ENGINE_FRAME_STRIDE_S)))
    kept: list[tuple[str, float, float]] = []
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
                    frame_time = start_time + timedelta(seconds=idx / fps)
                    point = nearest_gps(gps_points, frame_time)
                    if point is not None:
                        created += _engine_frame(db, segment, frame, point, kept, engine)
        idx += 1
    cap.release()
    return created


def _engine_frame(db, segment: VideoSegment, frame, point: GPSPoint,
                  kept: list[tuple[str, float, float]], engine) -> int:
    ctx = CaptureContext(
        image_id=None, route_id=segment.route_id, capture_type="video",
        latitude=point.latitude, longitude=point.longitude,
        heading_deg=point.heading_deg, quality_score=None, source="video",
    )
    result = engine.analyze_image("", ctx, frame=frame)
    created = 0
    for a in result.assets:
        here = (point.latitude, point.longitude)
        if any(cat == a.proposed_category and meters_between(here, (lat, lng)) < SAME_ASSET_M
               for cat, lat, lng in kept):
            continue
        kept.append((a.proposed_category, point.latitude, point.longitude))
        db.add(CandidateAsset(
            route_id=segment.route_id, capture_type="video",
            proposed_category=a.proposed_category,
            infrastructure_layer=a.infrastructure_layer, confidence=a.confidence,
            bbox=",".join(str(round(x, 1)) for x in a.box), crop_path=a.crop_path,
            annotated_path=result.annotated_path, ocr_text=a.ocr_text,
            ocr_language=a.ocr_language, ocr_confidence=a.ocr_confidence,
            condition=a.condition, defect=a.defect,
            detector_name=a.detector_name, detector_version=a.detector_version,
            latitude=point.latitude, longitude=point.longitude,
            confidence_band=_band(a.confidence, a.detector_name), processing_ms=result.processing_ms,
            status=CandidateStatus.PENDING_VALIDATION,
        ))
        created += 1
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
    log.info("worker started (poll every %ss); engine model %s",
             settings.worker_poll_s, settings.model_path)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("worker cycle failed")
        time.sleep(settings.worker_poll_s)


if __name__ == "__main__":
    main()
