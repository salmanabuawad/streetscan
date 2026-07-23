from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, func, update, delete
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_password, hash_password, create_token
from app.db.session import get_db
from app.api.deps import get_current_user, require_role, ROLE_RANK
from fastapi import Response
from app.models.entities import (
    Route, GPSPoint, VideoSegment, CapturedImage, Asset, Detection, Ticket, Business, TrainingSample,
    DetectionStatus, InfrastructureLayer, User, UserRole,
    AssetCategory, CandidateAsset, ProposedAsset, TrainingFeedback, CandidateStatus, ModelVersion,
    AnalysisJob,
)
from app.schemas.common import (
    RouteCreate, RouteOut, GPSPointCreate, AssetCreate, AssetOut,
    TicketCreate, TicketOut, SegmentOut, ImageOut, LoginIn, LoginOut, UserCreate, UserOut,
    BusinessOut, BusinessEdit, TrainingSampleOut, BBoxIn, VideoAnnotationIn,
)

router = APIRouter()

DRIVER = Depends(require_role("driver"))
VALIDATOR = Depends(require_role("validator"))
ADMIN = Depends(require_role("admin"))

@router.post("/auth/login", response_model=LoginOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not user.active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Wrong username or password")
    return LoginOut(token=create_token(user.id, user.username, user.role.value),
                    username=user.username, display_name=user.display_name, role=user.role.value)

@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user

@router.post("/users", response_model=UserOut, dependencies=[ADMIN])
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(409, "Username taken")
    try:
        role = UserRole(payload.role)
    except ValueError:
        raise HTTPException(400, f"Unknown role '{payload.role}'")
    user = User(username=payload.username, password_hash=hash_password(payload.password),
                display_name=payload.display_name, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@router.get("/users", response_model=list[UserOut], dependencies=[ADMIN])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.id)).all()

def to_naive_utc(dt: datetime) -> datetime:
    """Store everything as naive UTC so DB comparisons never mix aware/naive."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

def parse_client_timestamp(value: str | None) -> datetime:
    """Parse an ISO timestamp from the browser (JS toISOString ends with 'Z',
    which datetime.fromisoformat rejects on Python < 3.11)."""
    if not value:
        return datetime.utcnow()
    try:
        return to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return datetime.utcnow()

@router.get("/health")
def health():
    return {"status": "ok"}

@router.post("/routes", response_model=RouteOut, dependencies=[DRIVER])
def create_route(payload: RouteCreate, db: Session = Depends(get_db)):
    route = Route(vehicle_name=payload.vehicle_name, driver_name=payload.driver_name)
    db.add(route)
    db.commit()
    db.refresh(route)
    return route

@router.post("/routes/{route_id}/stop", response_model=RouteOut, dependencies=[DRIVER])
def stop_route(route_id: int, db: Session = Depends(get_db)):
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(404, "Route not found")
    route.active = False
    route.ended_at = datetime.utcnow()
    db.commit()
    db.refresh(route)
    return route

@router.get("/routes", response_model=list[RouteOut], dependencies=[DRIVER])
def list_routes(db: Session = Depends(get_db)):
    return db.scalars(select(Route).order_by(Route.started_at.desc()).limit(100)).all()

@router.post("/gps", dependencies=[DRIVER])
def add_gps_point(payload: GPSPointCreate, db: Session = Depends(get_db)):
    point = GPSPoint(
        route_id=payload.route_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_m=payload.accuracy_m,
        speed_mps=payload.speed_mps,
        heading_deg=payload.heading_deg,
        captured_at=to_naive_utc(payload.captured_at) if payload.captured_at else datetime.utcnow(),
    )
    db.add(point)
    db.commit()
    return {"id": point.id}

@router.post("/video-segments", dependencies=[DRIVER])
async def upload_video_segment(
    route_id: int = Form(...),
    captured_at: str | None = Form(None),
    orientation: int = Form(0),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(404, "Route not found")

    upload_dir = Path(settings.upload_dir) / str(route_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "segment.webm").suffix or ".webm"
    filename = f"{uuid.uuid4().hex}{ext}"
    target = upload_dir / filename

    content = await file.read()
    target.write_bytes(content)

    segment = VideoSegment(
        route_id=route_id,
        filename=str(target),
        mime_type=file.content_type or "video/webm",
        size_bytes=len(content),
        captured_at=parse_client_timestamp(captured_at),
        orientation_hint=orientation if orientation in (0, 90, 180, 270) else 0,
    )
    db.add(segment)
    db.commit()
    db.refresh(segment)
    return {"id": segment.id, "filename": filename, "size_bytes": len(content)}

@router.post("/images", dependencies=[DRIVER])
async def upload_image(
    route_id: int = Form(...),
    captured_at: str | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    heading_deg: float | None = Form(None),
    speed_mps: float | None = Form(None),
    kind: str = Form("interval"),
    blur_score: float | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(404, "Route not found")
    img_dir = Path(settings.upload_dir) / str(route_id) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "photo.jpg").suffix or ".jpg"
    target = img_dir / f"{uuid.uuid4().hex}{ext}"
    content = await file.read()
    target.write_bytes(content)
    image = CapturedImage(
        route_id=route_id, filename=str(target), size_bytes=len(content),
        captured_at=parse_client_timestamp(captured_at),
        latitude=latitude, longitude=longitude, heading_deg=heading_deg,
        speed_mps=speed_mps, blur_score=blur_score,
        kind=kind if kind in ("interval", "stop_burst", "manual") else "interval",
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return {"id": image.id, "size_bytes": len(content)}

@router.get("/captured-images", response_model=list[ImageOut], dependencies=[DRIVER])
def list_captured_images(limit: int = 200, db: Session = Depends(get_db)):
    """All captured street stills, newest first — the in-domain frames to
    annotate for training (proven far more useful than close-up photos)."""
    return db.scalars(
        select(CapturedImage).order_by(CapturedImage.id.desc()).limit(min(limit, 500))
    ).all()

@router.post("/captured-images/{image_id}/annotate", response_model=TrainingSampleOut, dependencies=[DRIVER])
async def annotate_captured_image(
    image_id: int,
    payload: BBoxIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Turn a box drawn on a real street frame into a training sample (copies
    the frame into the training set so it survives if the capture is deleted)."""
    image = db.get(CapturedImage, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    if not payload.asset_type:
        raise HTTPException(400, "asset_type required")
    src = Path(image.filename)
    if not src.is_file():
        raise HTTPException(404, "Image file missing")
    train_dir = Path(settings.upload_dir) / "training"
    train_dir.mkdir(parents=True, exist_ok=True)
    target = train_dir / f"{uuid.uuid4().hex}{src.suffix or '.jpg'}"
    target.write_bytes(src.read_bytes())
    sample = TrainingSample(
        filename=str(target),
        asset_name=(payload.asset_name or payload.asset_type).strip(),
        asset_type=payload.asset_type.strip(),
        layer=(payload.layer or "other").strip(),
        latitude=image.latitude, longitude=image.longitude, uploaded_by=user.id,
        bbox_cx=payload.bbox_cx, bbox_cy=payload.bbox_cy,
        bbox_w=payload.bbox_w, bbox_h=payload.bbox_h,
    )
    db.add(sample)
    db.commit()
    db.refresh(sample)
    return sample

@router.get("/routes/{route_id}/images", response_model=list[ImageOut], dependencies=[DRIVER])
def list_route_images(route_id: int, db: Session = Depends(get_db)):
    if not db.get(Route, route_id):
        raise HTTPException(404, "Route not found")
    return db.scalars(
        select(CapturedImage).where(CapturedImage.route_id == route_id)
        .order_by(CapturedImage.captured_at)
    ).all()

@router.get("/images/{image_id}/file", dependencies=[DRIVER])
def image_file(image_id: int, db: Session = Depends(get_db)):
    image = db.get(CapturedImage, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    path = Path(image.filename)
    if not path.is_file():
        raise HTTPException(404, "Image file missing")
    return FileResponse(path, media_type="image/jpeg")

@router.delete("/images/{image_id}", dependencies=[VALIDATOR])
def delete_image(image_id: int, db: Session = Depends(get_db)):
    image = db.get(CapturedImage, image_id)
    if not image:
        raise HTTPException(404, "Image not found")
    db.execute(update(Detection).where(Detection.image_id == image_id).values(image_id=None))
    Path(image.filename).unlink(missing_ok=True)
    db.delete(image)
    db.commit()
    return {"deleted": image_id}

@router.post("/video-segments/{segment_id}/annotate", response_model=TrainingSampleOut, dependencies=[DRIVER])
def annotate_video_frame(
    segment_id: int,
    payload: VideoAnnotationIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Extract the selected video frame and save the user's bounding box as a
    durable training sample. The label may be an existing category or a new
    free-text type; model training later treats asset_type as the class key."""
    segment = db.get(VideoSegment, segment_id)
    if not segment:
        raise HTTPException(404, "Video segment not found")
    source = Path(segment.filename)
    if not source.is_file():
        raise HTTPException(404, "Video file missing")
    if payload.timestamp_s < 0:
        raise HTTPException(400, "timestamp_s must be non-negative")
    if not payload.asset_type or not payload.asset_type.strip():
        raise HTTPException(400, "asset_type required")
    for value in (payload.bbox_cx, payload.bbox_cy, payload.bbox_w, payload.bbox_h):
        if value < 0 or value > 1:
            raise HTTPException(400, "Bounding-box values must be normalized between 0 and 1")
    if payload.bbox_w <= 0 or payload.bbox_h <= 0:
        raise HTTPException(400, "Bounding box must have positive width and height")

    try:
        import cv2
    except ImportError as exc:
        raise HTTPException(503, "OpenCV is required for video-frame annotation") from exc

    capture = cv2.VideoCapture(str(source))
    try:
        if not capture.isOpened():
            raise HTTPException(422, "Could not open video segment")
        duration_ms = capture.get(cv2.CAP_PROP_FRAME_COUNT) / max(capture.get(cv2.CAP_PROP_FPS), 1) * 1000
        requested_ms = payload.timestamp_s * 1000
        if duration_ms > 0 and requested_ms > duration_ms + 250:
            raise HTTPException(400, "Timestamp is outside the video duration")
        capture.set(cv2.CAP_PROP_POS_MSEC, requested_ms)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise HTTPException(422, "Could not extract the selected frame")
    finally:
        capture.release()

    # Apply the phone orientation hint so the saved training frame matches UI view.
    if segment.orientation_hint == 90:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    elif segment.orientation_hint == 180:
        frame = cv2.rotate(frame, cv2.ROTATE_180)
    elif segment.orientation_hint == 270:
        frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

    train_dir = Path(settings.upload_dir) / "training" / "video_frames"
    train_dir.mkdir(parents=True, exist_ok=True)
    target = train_dir / f"segment_{segment_id}_{int(payload.timestamp_s * 1000)}_{uuid.uuid4().hex}.jpg"
    if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise HTTPException(500, "Failed to save extracted frame")

    metadata = f"source=video;segment_id={segment_id};timestamp_s={payload.timestamp_s:.3f}"
    if payload.notes:
        metadata += f";notes={payload.notes.strip()}"
    sample = TrainingSample(
        filename=str(target),
        asset_name=(payload.asset_name or payload.asset_type).strip(),
        asset_type=payload.asset_type.strip(),
        layer=(payload.layer or "other").strip(),
        notes=metadata,
        uploaded_by=user.id,
        bbox_cx=payload.bbox_cx, bbox_cy=payload.bbox_cy,
        bbox_w=payload.bbox_w, bbox_h=payload.bbox_h,
    )
    db.add(sample)
    db.commit()
    db.refresh(sample)
    return sample


@router.get("/routes/{route_id}/segments", response_model=list[SegmentOut], dependencies=[DRIVER])
def list_route_segments(route_id: int, db: Session = Depends(get_db)):
    if not db.get(Route, route_id):
        raise HTTPException(404, "Route not found")
    return db.scalars(
        select(VideoSegment).where(VideoSegment.route_id == route_id)
        .order_by(VideoSegment.captured_at)
    ).all()

@router.get("/video-segments/{segment_id}/stream", dependencies=[DRIVER])
def stream_video_segment(segment_id: int, db: Session = Depends(get_db)):
    segment = db.get(VideoSegment, segment_id)
    if not segment:
        raise HTTPException(404, "Segment not found")
    path = Path(segment.filename)
    if not path.is_file():
        raise HTTPException(404, "Video file missing")
    # FileResponse handles Range requests, so <video> seeking works.
    return FileResponse(path, media_type=segment.mime_type or "video/webm")

@router.delete("/video-segments/{segment_id}", dependencies=[VALIDATOR])
def delete_video_segment(segment_id: int, db: Session = Depends(get_db)):
    segment = db.get(VideoSegment, segment_id)
    if not segment:
        raise HTTPException(404, "Segment not found")
    # Detections stay (they may already be approved assets); just unlink.
    db.execute(update(Detection).where(Detection.video_segment_id == segment_id)
               .values(video_segment_id=None))
    Path(segment.filename).unlink(missing_ok=True)
    db.delete(segment)
    db.commit()
    return {"deleted": segment_id}

@router.delete("/routes/{route_id}", dependencies=[VALIDATOR])
def delete_route(route_id: int, db: Session = Depends(get_db)):
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(404, "Route not found")
    if route.active:
        raise HTTPException(400, "Stop the route before deleting it")
    segment_ids = db.scalars(
        select(VideoSegment.id).where(VideoSegment.route_id == route_id)
    ).all()
    if segment_ids:
        db.execute(update(Detection).where(Detection.video_segment_id.in_(segment_ids))
                   .values(video_segment_id=None))
    image_ids = db.scalars(
        select(CapturedImage.id).where(CapturedImage.route_id == route_id)
    ).all()
    if image_ids:
        db.execute(update(Detection).where(Detection.image_id.in_(image_ids))
                   .values(image_id=None))
    db.execute(update(Detection).where(Detection.route_id == route_id)
               .values(route_id=None))
    for filename in db.scalars(
        select(VideoSegment.filename).where(VideoSegment.route_id == route_id)
    ):
        Path(filename).unlink(missing_ok=True)
    for filename in db.scalars(
        select(CapturedImage.filename).where(CapturedImage.route_id == route_id)
    ):
        Path(filename).unlink(missing_ok=True)
    db.execute(CapturedImage.__table__.delete().where(CapturedImage.route_id == route_id))
    db.delete(route)  # cascades gps_points + video_segments
    db.commit()
    return {"deleted": route_id, "segments_deleted": len(segment_ids)}

@router.post("/assets", response_model=AssetOut, dependencies=[VALIDATOR])
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)):
    asset = Asset(**payload.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset

@router.get("/assets", response_model=list[AssetOut], dependencies=[DRIVER])
def list_assets(layer: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Asset).order_by(Asset.created_at.desc())
    if layer:
        try:
            stmt = stmt.where(Asset.layer == InfrastructureLayer(layer))
        except ValueError:
            raise HTTPException(400, f"Unknown layer '{layer}'")
    return db.scalars(stmt.limit(500)).all()

@router.post("/tickets", response_model=TicketOut, dependencies=[VALIDATOR])
def create_ticket(payload: TicketCreate, db: Session = Depends(get_db)):
    if payload.asset_id and not db.get(Asset, payload.asset_id):
        raise HTTPException(404, "Asset not found")
    if payload.detection_id and not db.get(Detection, payload.detection_id):
        raise HTTPException(404, "Detection not found")
    ticket = Ticket(**payload.model_dump())
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket

@router.get("/tickets", response_model=list[TicketOut], dependencies=[DRIVER])
def list_tickets(db: Session = Depends(get_db)):
    return db.scalars(select(Ticket).order_by(Ticket.created_at.desc()).limit(500)).all()

@router.get("/businesses", response_model=list[BusinessOut], dependencies=[DRIVER])
def list_businesses(status: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Business).order_by(Business.created_at.desc())
    if status:
        try:
            stmt = stmt.where(Business.status == DetectionStatus(status))
        except ValueError:
            raise HTTPException(400, f"Unknown status '{status}'")
    return db.scalars(stmt.limit(1000)).all()

@router.get("/businesses/{business_id}/snapshot", dependencies=[DRIVER])
def business_snapshot(business_id: int, db: Session = Depends(get_db)):
    biz = db.get(Business, business_id)
    if not biz or not biz.snapshot_path:
        raise HTTPException(404, "Snapshot not found")
    path = Path(biz.snapshot_path)
    if not path.is_file():
        raise HTTPException(404, "Snapshot file missing")
    return FileResponse(path, media_type="image/jpeg")

@router.patch("/businesses/{business_id}", response_model=BusinessOut, dependencies=[VALIDATOR])
def edit_business(business_id: int, payload: BusinessEdit, db: Session = Depends(get_db)):
    biz = db.get(Business, business_id)
    if not biz:
        raise HTTPException(404, "Business not found")
    if payload.name is not None:
        biz.name = payload.name
    if payload.category is not None:
        biz.category = payload.category
    db.commit()
    db.refresh(biz)
    return biz

@router.post("/businesses/{business_id}/approve", response_model=BusinessOut, dependencies=[VALIDATOR])
def approve_business(business_id: int, db: Session = Depends(get_db)):
    biz = db.get(Business, business_id)
    if not biz:
        raise HTTPException(404, "Business not found")
    biz.status = DetectionStatus.APPROVED
    db.commit()
    db.refresh(biz)
    return biz

@router.post("/businesses/{business_id}/reject", response_model=BusinessOut, dependencies=[VALIDATOR])
def reject_business(business_id: int, db: Session = Depends(get_db)):
    biz = db.get(Business, business_id)
    if not biz:
        raise HTTPException(404, "Business not found")
    biz.status = DetectionStatus.REJECTED
    db.commit()
    db.refresh(biz)
    return biz

@router.post("/training-samples", response_model=TrainingSampleOut, dependencies=[DRIVER])
async def add_training_sample(
    asset_name: str = Form(...),
    asset_type: str = Form(...),
    layer: str = Form("other"),
    notes: str | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    bbox_cx: float | None = Form(None),
    bbox_cy: float | None = Form(None),
    bbox_w: float | None = Form(None),
    bbox_h: float | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    train_dir = Path(settings.upload_dir) / "training"
    train_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "sample.jpg").suffix or ".jpg"
    target = train_dir / f"{uuid.uuid4().hex}{ext}"
    target.write_bytes(await file.read())
    sample = TrainingSample(
        filename=str(target), asset_name=asset_name.strip(), asset_type=asset_type.strip(),
        layer=layer, notes=notes, latitude=latitude, longitude=longitude, uploaded_by=user.id,
        bbox_cx=bbox_cx, bbox_cy=bbox_cy, bbox_w=bbox_w, bbox_h=bbox_h,
    )
    db.add(sample)
    db.commit()
    db.refresh(sample)
    return sample

@router.get("/training-samples", response_model=list[TrainingSampleOut], dependencies=[DRIVER])
def list_training_samples(db: Session = Depends(get_db)):
    return db.scalars(select(TrainingSample).order_by(TrainingSample.created_at.desc()).limit(1000)).all()

@router.get("/training-samples/summary", dependencies=[DRIVER])
def training_summary(db: Session = Depends(get_db)):
    rows = db.execute(
        select(TrainingSample.asset_type, func.count()).group_by(TrainingSample.asset_type)
    ).all()
    return {"total": sum(c for _, c in rows), "by_type": {t: c for t, c in rows}}

@router.patch("/training-samples/{sample_id}", response_model=TrainingSampleOut, dependencies=[DRIVER])
def edit_training_sample(sample_id: int, payload: BBoxIn, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    sample = db.get(TrainingSample, sample_id)
    if not sample:
        raise HTTPException(404, "Sample not found")
    if sample.uploaded_by != user.id and ROLE_RANK[user.role.value] < ROLE_RANK["validator"]:
        raise HTTPException(403, "Can only edit your own samples")
    sample.bbox_cx, sample.bbox_cy = payload.bbox_cx, payload.bbox_cy
    sample.bbox_w, sample.bbox_h = payload.bbox_w, payload.bbox_h
    if payload.asset_type:
        sample.asset_type = payload.asset_type
    if payload.asset_name is not None:
        sample.asset_name = payload.asset_name
    db.commit()
    db.refresh(sample)
    return sample

@router.get("/training-samples/{sample_id}/file", dependencies=[DRIVER])
def training_sample_file(sample_id: int, db: Session = Depends(get_db)):
    sample = db.get(TrainingSample, sample_id)
    if not sample:
        raise HTTPException(404, "Sample not found")
    path = Path(sample.filename)
    if not path.is_file():
        raise HTTPException(404, "Image file missing")
    return FileResponse(path, media_type="image/jpeg")

@router.delete("/training-samples/{sample_id}", dependencies=[DRIVER])
def delete_training_sample(sample_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sample = db.get(TrainingSample, sample_id)
    if not sample:
        raise HTTPException(404, "Sample not found")
    # a driver may remove their own samples; validators may remove any
    if sample.uploaded_by != user.id and ROLE_RANK[user.role.value] < ROLE_RANK["validator"]:
        raise HTTPException(403, "Can only delete your own samples")
    Path(sample.filename).unlink(missing_ok=True)
    db.delete(sample)
    db.commit()
    return {"deleted": sample_id}

@router.get("/map-data", dependencies=[DRIVER])
def map_data(db: Session = Depends(get_db)):
    """Everything the GIS map needs in one call: located assets, located
    detections, and per-route GPS tracks."""
    assets = db.scalars(
        select(Asset).where(Asset.latitude.is_not(None), Asset.longitude.is_not(None))
        .order_by(Asset.id).limit(2000)
    ).all()
    detections = db.scalars(
        select(Detection).where(Detection.latitude.is_not(None), Detection.longitude.is_not(None))
        .order_by(Detection.id).limit(2000)
    ).all()
    businesses = db.scalars(
        select(Business).where(Business.latitude.is_not(None), Business.longitude.is_not(None))
        .order_by(Business.id).limit(2000)
    ).all()
    routes = db.scalars(select(Route).order_by(Route.id.desc()).limit(20)).all()
    tracks = []
    for route in routes:
        points = db.scalars(
            select(GPSPoint).where(GPSPoint.route_id == route.id)
            .order_by(GPSPoint.captured_at).limit(5000)
        ).all()
        # drop stray fixes outside Buqata, then break the line wherever the
        # signal jumped so we never draw a straight segment across a gap the
        # vehicle didn't actually drive
        pts = [[p.latitude, p.longitude] for p in points
               if settings.in_survey_area(p.latitude, p.longitude)]
        for seg in _split_track(pts):
            tracks.append({
                "route_id": route.id,
                "vehicle_name": route.vehicle_name,
                "points": seg,
            })
    return {
        "assets": [{
            "id": a.id, "name": a.name, "asset_type": a.asset_type,
            "layer": a.layer.value, "status": a.status.value,
            "lat": a.latitude, "lng": a.longitude, "underground": a.underground,
            "source": a.source, "notes": a.notes,
            # link back to the detection frame so the map can show the evidence
            "candidate_id": _candidate_id_from_notes(a.notes),
        } for a in assets if settings.in_survey_area(a.latitude, a.longitude)],
        "detections": [{
            "id": d.id, "asset_type": d.proposed_asset_type, "layer": d.proposed_layer.value,
            "confidence": d.confidence, "status": d.status.value,
            "lat": d.latitude, "lng": d.longitude,
        } for d in detections],
        "businesses": [{
            "id": b.id, "name": b.name, "category": b.category,
            "confidence": b.confidence, "status": b.status.value,
            "lat": b.latitude, "lng": b.longitude,
        } for b in businesses],
        "tracks": tracks,
    }

def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    from math import radians, sin, cos, asin, sqrt
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


# ===== Asset-analysis engine: categories + candidate assets =====

@router.get("/asset-categories", dependencies=[DRIVER])
def list_categories(db: Session = Depends(get_db)):
    cats = db.scalars(select(AssetCategory).where(AssetCategory.active).order_by(AssetCategory.name)).all()
    return [{"name": c.name, "layer": c.infrastructure_layer, "detector": c.active_detector,
             "min_confidence": c.min_confidence, "department": c.department,
             "prompts": c.detection_prompts.split("\n")} for c in cats]

@router.get("/assets/candidates/summary", dependencies=[DRIVER])
def candidates_summary(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(CandidateAsset)) or 0
    by_band = dict(db.execute(select(CandidateAsset.confidence_band, func.count()).group_by(CandidateAsset.confidence_band)).all())
    by_status = dict(db.execute(select(CandidateAsset.status, func.count()).group_by(CandidateAsset.status)).all())
    by_cat = db.execute(
        select(CandidateAsset.proposed_category, func.count()).group_by(CandidateAsset.proposed_category)
        .order_by(func.count().desc())
    ).all()
    proposed = db.scalar(select(func.count()).select_from(ProposedAsset)) or 0
    return {
        "total_candidates": total,
        "proposed_assets": proposed,
        "by_band": by_band,
        "by_status": {s.value if hasattr(s, "value") else s: n for s, n in by_status.items()},
        "by_category": {c: n for c, n in by_cat},
    }

@router.get("/assets/candidates", dependencies=[DRIVER])
def list_candidates(category: str | None = None, band: str | None = None,
                    status: str | None = None, route_id: int | None = None,
                    detector: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    stmt = select(CandidateAsset).order_by(CandidateAsset.confidence.desc())
    if category:
        stmt = stmt.where(CandidateAsset.proposed_category == category)
    if route_id:
        stmt = stmt.where(CandidateAsset.route_id == route_id)
    if detector:
        stmt = stmt.where(CandidateAsset.detector_name.like(f"{detector}%"))
    if band:
        stmt = stmt.where(CandidateAsset.confidence_band == band)
    if status:
        try:
            stmt = stmt.where(CandidateAsset.status == CandidateStatus(status))
        except ValueError:
            raise HTTPException(400, f"Unknown status '{status}'")
    rows = db.scalars(stmt.limit(min(limit, 1000))).all()
    return [_candidate_out(c) for c in rows]

def _candidate_out(c: CandidateAsset) -> dict:
    return {
        "id": c.id, "proposed_asset_id": c.proposed_asset_id, "image_id": c.image_id,
        "route_id": c.route_id, "category": c.proposed_category, "asset_name": c.asset_name,
        "layer": c.infrastructure_layer,
        "confidence": c.confidence, "band": c.confidence_band, "bbox": c.bbox,
        "ocr_text": c.ocr_text, "condition": c.condition, "quality_score": c.quality_score,
        "latitude": c.latitude, "longitude": c.longitude, "detector": c.detector_name,
        "status": c.status.value if hasattr(c.status, "value") else c.status,
    }

@router.get("/assets/candidates/{cand_id}", dependencies=[DRIVER])
def get_candidate(cand_id: int, db: Session = Depends(get_db)):
    c = db.get(CandidateAsset, cand_id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    return _candidate_out(c)

def _feedback(db, c, ftype, user, **kw):
    db.add(TrainingFeedback(candidate_id=c.id, feedback_type=ftype, user_id=user.id, **kw))


# engine layers are free-form config strings; the GIS Asset layer is a fixed
# enum, so anything outside it lands in public_space rather than failing.
_LAYER_FALLBACK = {"hazard": InfrastructureLayer.ROAD, "building": InfrastructureLayer.PUBLIC_SPACE,
                   "safety": InfrastructureLayer.PUBLIC_SPACE, "other": InfrastructureLayer.PUBLIC_SPACE}


# Consecutive fixes farther apart than this mean a dropped-then-reacquired
# signal, not real travel (a vehicle at 108 km/h moves ~60m between 2s fixes).
# Break the drawn line there rather than cutting a straight gash across terrain.
TRACK_BREAK_M = 120.0


def _haversine_m(a: list[float], b: list[float]) -> float:
    import math
    dlat = (a[0] - b[0]) * 111320.0
    dlng = (a[1] - b[1]) * 111320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


def _split_track(pts: list[list[float]]) -> list[list[list[float]]]:
    """Split a point sequence into contiguous runs, breaking at GPS jumps.
    Single-point runs are dropped (a polyline needs two points)."""
    if not pts:
        return []
    segments, cur = [], [pts[0]]
    for prev, p in zip(pts, pts[1:]):
        if _haversine_m(prev, p) > TRACK_BREAK_M:
            if len(cur) > 1:
                segments.append(cur)
            cur = [p]
        else:
            cur.append(p)
    if len(cur) > 1:
        segments.append(cur)
    return segments


def _candidate_id_from_notes(notes: str | None) -> int | None:
    """Assets promoted from a detection carry 'candidate {id}; ...' in notes."""
    if not notes or not notes.startswith("candidate "):
        return None
    try:
        return int(notes.split(";")[0].replace("candidate ", "").strip())
    except ValueError:
        return None


def _gis_layer(layer: str) -> InfrastructureLayer:
    try:
        return InfrastructureLayer(layer)
    except ValueError:
        return _LAYER_FALLBACK.get(layer, InfrastructureLayer.PUBLIC_SPACE)


def promote_candidate_to_asset(db, c: CandidateAsset) -> Asset:
    """An approved candidate becomes an official GIS asset. Observations grouped
    into one proposed asset share a single asset instead of duplicating it."""
    proposed = db.get(ProposedAsset, c.proposed_asset_id) if c.proposed_asset_id else None
    if proposed and proposed.gis_asset_id:
        existing = db.get(Asset, proposed.gis_asset_id)
        if existing:
            return existing
    asset = Asset(
        name=(c.asset_name or c.proposed_category),
        asset_type=c.proposed_category,
        layer=_gis_layer(c.infrastructure_layer),
        latitude=c.latitude, longitude=c.longitude,
        source="ai_validated",
        notes=f"candidate {c.id}; detector {c.detector_name} {c.confidence}",
    )
    db.add(asset)
    db.flush()
    if proposed:
        proposed.gis_asset_id = asset.id
        proposed.status = CandidateStatus.APPROVED
    return asset

@router.post("/assets/candidates/{cand_id}/approve", dependencies=[VALIDATOR])
def approve_candidate(cand_id: int, category: str | None = Form(None),
                      name: str | None = Form(None), db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Approve, optionally overriding the category and/or setting a free-text
    name. A changed category is recorded as a correction for model training."""
    c = db.get(CandidateAsset, cand_id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    if category and category.strip() and category.strip() != c.proposed_category:
        corrected = category.strip()
        c.proposed_category = corrected
        _feedback(db, c, "correct_category", user, corrected_category=corrected)
    else:
        _feedback(db, c, "approve", user)
    if name is not None and name.strip():
        c.asset_name = name.strip()
    c.status = CandidateStatus.APPROVED
    asset = promote_candidate_to_asset(db, c)   # approved => real GIS asset
    db.commit()
    out = _candidate_out(c)
    out["gis_asset_id"] = asset.id
    return out

@router.post("/assets/candidates/{cand_id}/reject", dependencies=[VALIDATOR])
def reject_candidate(cand_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    c = db.get(CandidateAsset, cand_id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    c.status = CandidateStatus.REJECTED
    _feedback(db, c, "false_positive", user)
    db.commit()
    return _candidate_out(c)

@router.post("/assets/candidates/{cand_id}/correct", dependencies=[VALIDATOR])
def correct_candidate(cand_id: int, category: str = Form(...), db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    c = db.get(CandidateAsset, cand_id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    old = c.proposed_category
    c.proposed_category = category
    c.status = CandidateStatus.APPROVED
    _feedback(db, c, "correct_category", user, corrected_category=category)
    db.commit()
    return {"id": c.id, "was": old, "now": category}

@router.get("/assets/candidates/{cand_id}/image", dependencies=[DRIVER])
def candidate_image(cand_id: int, db: Session = Depends(get_db)):
    """Serve the source frame with the candidate's box drawn — no crop upload
    needed; the captured image already lives on the server."""
    import cv2
    c = db.get(CandidateAsset, cand_id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    if not c.image_id:
        # video-sourced candidate: there is no stored still, but the engine
        # already wrote an annotated frame when it detected this asset
        if c.annotated_path and Path(c.annotated_path).is_file():
            return FileResponse(c.annotated_path, media_type="image/jpeg")
        raise HTTPException(404, "Candidate/image not found")
    img_row = db.get(CapturedImage, c.image_id)
    if not img_row or not Path(img_row.filename).is_file():
        raise HTTPException(404, "Image file missing")
    img = cv2.imread(img_row.filename)
    if img is None:
        raise HTTPException(404, "Unreadable image")
    try:
        x1, y1, x2, y2 = (int(float(v)) for v in c.bbox.split(","))
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 4)
        cv2.putText(img, f"{c.proposed_category} {c.confidence}", (x1, max(y1 - 8, 18)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
    except Exception:
        pass
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return Response(buf.tobytes(), media_type="image/jpeg")

@router.get("/assets/candidates/export/geojson", dependencies=[VALIDATOR])
def export_geojson(only_approved: bool = False, db: Session = Depends(get_db)):
    """Located proposed assets as GeoJSON. Coordinates are approximate (phone
    GPS) and never invented — assets without GPS are omitted."""
    stmt = select(ProposedAsset).where(ProposedAsset.latitude.is_not(None))
    if only_approved:
        stmt = stmt.where(ProposedAsset.status == CandidateStatus.APPROVED)
    feats = []
    for p in db.scalars(stmt).all():
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.longitude, p.latitude]},
            "properties": {
                "proposed_asset_id": p.id, "category": p.category, "layer": p.infrastructure_layer,
                "observations": p.observation_count, "confidence": p.best_confidence,
                "status": p.status.value if hasattr(p.status, "value") else p.status,
                "route_id": p.route_id, "position": "approximate_phone_gps",
            },
        })
    return {"type": "FeatureCollection", "features": feats}

@router.post("/assets/training/export", dependencies=[VALIDATOR])
def export_training_dataset(db: Session = Depends(get_db)):
    """Turn human-validated candidates into a YOLO dataset (the self-improvement
    loop). Uses approved candidates (corrected category wins) with a box."""
    import cv2, shutil
    out = Path(settings.upload_dir) / "training_export"
    if out.exists():
        shutil.rmtree(out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    cands = db.scalars(select(CandidateAsset).where(CandidateAsset.status == CandidateStatus.APPROVED)).all()
    classes = sorted({c.proposed_category for c in cands})
    cid = {c: i for i, c in enumerate(classes)}
    written = 0
    for c in cands:
        if not c.image_id:
            continue
        img_row = db.get(CapturedImage, c.image_id)
        if not img_row or not Path(img_row.filename).is_file():
            continue
        img = cv2.imread(img_row.filename)
        if img is None:
            continue
        h, w = img.shape[:2]
        try:
            x1, y1, x2, y2 = (float(v) for v in c.bbox.split(","))
        except ValueError:
            continue
        cx, cy = ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h)
        bw, bh = (abs(x2 - x1) / w, abs(y2 - y1) / h)
        stem = f"c{c.id}"
        shutil.copy(img_row.filename, out / "images" / f"{stem}.jpg")
        (out / "labels" / f"{stem}.txt").write_text(f"{cid[c.proposed_category]} {cx:.5f} {cy:.5f} {bw:.5f} {bh:.5f}\n")
        written += 1
    names = "\n".join(f"  {i}: {c}" for i, c in enumerate(classes))
    (out / "data.yaml").write_text(f"path: {out}\ntrain: images\nval: images\nnames:\n{names}\n")
    return {"exported_labels": written, "classes": classes, "dataset_path": str(out),
            "format": "yolo", "note": "train with app.train_model pointing at this data.yaml"}

@router.get("/models", dependencies=[VALIDATOR])
def list_models(db: Session = Depends(get_db)):
    return [{"id": m.id, "name": m.name, "type": m.model_type, "version": m.version,
             "active": m.active, "metrics": m.metrics, "created_at": m.created_at}
            for m in db.scalars(select(ModelVersion).order_by(ModelVersion.id.desc())).all()]

@router.post("/models/{model_id}/activate", dependencies=[ADMIN])
def activate_model(model_id: int, db: Session = Depends(get_db)):
    m = db.get(ModelVersion, model_id)
    if not m:
        raise HTTPException(404, "Model not found")
    db.execute(update(ModelVersion).where(ModelVersion.model_type == m.model_type).values(active=False))
    m.active = True
    db.commit()
    return {"activated": m.id, "name": m.name, "type": m.model_type}

# ===== async analysis jobs (worker picks up engine_processed=false) =====

@router.post("/analysis/routes/{route_id}", dependencies=[VALIDATOR])
def analyze_route(route_id: int, db: Session = Depends(get_db)):
    """Button-triggered: run open-vocabulary infrastructure detection (OWL-ViT)
    over a route's images. Async — the worker picks up openvocab_processed=false."""
    if not db.get(Route, route_id):
        raise HTTPException(404, "Route not found")
    # Do not duplicate an active route analysis job.
    active = db.scalar(select(AnalysisJob).where(
        AnalysisJob.target_route_id == route_id,
        AnalysisJob.job_type == "route",
        AnalysisJob.status.in_(["queued", "running"]),
    ).order_by(AnalysisJob.id.desc()))
    if active:
        return {"job_id": active.id, "queued_images": active.total,
                "note": "an analysis job is already active for this route"}

    image_count = db.scalar(select(func.count()).select_from(CapturedImage)
                            .where(CapturedImage.route_id == route_id)) or 0
    if image_count == 0:
        raise HTTPException(400, "Route has no captured images")

    # A rerun replaces only unapproved OWL-ViT drafts. Human-approved assets and
    # their audit trail remain intact.
    db.execute(delete(CandidateAsset).where(
        CandidateAsset.route_id == route_id,
        CandidateAsset.detector_name == "owlvit",
        CandidateAsset.status.in_([
            CandidateStatus.PENDING_VALIDATION,
            CandidateStatus.REJECTED,
            CandidateStatus.INSUFFICIENT_QUALITY,
        ]),
    ))
    db.execute(update(CapturedImage).where(CapturedImage.route_id == route_id)
               .values(openvocab_processed=False))
    job = AnalysisJob(job_type="route", target_route_id=route_id, status="queued", total=image_count)
    db.add(job)
    db.commit()
    db.refresh(job)
    return {"job_id": job.id, "queued_images": image_count,
            "note": "local worker analyzes asynchronously; poll the job"}

@router.post("/analysis/reprocess", dependencies=[ADMIN])
def analyze_reprocess(route_id: int | None = None, db: Session = Depends(get_db)):
    stmt = update(CapturedImage).values(engine_processed=False)
    if route_id:
        stmt = stmt.where(CapturedImage.route_id == route_id)
    n = db.execute(stmt).rowcount
    job = AnalysisJob(job_type="reprocess", target_route_id=route_id, status="queued", total=n)
    db.add(job); db.commit(); db.refresh(job)
    return {"job_id": job.id, "queued_images": n}

@router.get("/analysis/jobs/{job_id}", dependencies=[DRIVER])
@router.get("/analysis/jobs/{job_id}/progress", dependencies=[DRIVER])
def analysis_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    remaining = 0
    if job.target_route_id:
        remaining = db.scalar(select(func.count()).select_from(CapturedImage)
                              .where(CapturedImage.route_id == job.target_route_id,
                                     CapturedImage.openvocab_processed.is_(False))) or 0
    processed = max(0, job.total - remaining)
    candidates = db.scalar(select(func.count()).select_from(CandidateAsset)
                           .where(CandidateAsset.route_id == job.target_route_id,
                                  CandidateAsset.detector_name == "owlvit")) or 0
    status = job.status
    if status not in ("failed", "done"):
        status = "done" if remaining == 0 else ("running" if processed else "queued")
        job.status = status
        job.done = processed
        job.candidates_created = candidates
        if status == "done" and job.finished_at is None:
            job.finished_at = datetime.utcnow()
        db.commit()
    return {"job_id": job.id, "type": job.job_type, "route_id": job.target_route_id,
            "total": job.total, "processed": processed, "remaining": remaining,
            "candidates": candidates, "status": status, "detail": job.detail}

@router.get("/analysis/results/{image_id}", dependencies=[DRIVER])
def analysis_results(image_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(CandidateAsset).where(CandidateAsset.image_id == image_id)).all()
    return {"image_id": image_id, "candidates": [_candidate_out(c) for c in rows]}

@router.post("/assets/candidates/{cand_id}/merge", dependencies=[VALIDATOR])
def merge_candidate(cand_id: int, into: int = Form(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    c, target = db.get(CandidateAsset, cand_id), db.get(CandidateAsset, into)
    if not c or not target:
        raise HTTPException(404, "Candidate not found")
    c.proposed_asset_id = target.proposed_asset_id
    c.status = CandidateStatus.DUPLICATE_OBSERVATION
    _feedback(db, c, "duplicate_correction", user)
    db.commit()
    return {"merged": cand_id, "into": into}

@router.post("/assets/candidates/{cand_id}/split", dependencies=[VALIDATOR])
def split_candidate(cand_id: int, db: Session = Depends(get_db)):
    c = db.get(CandidateAsset, cand_id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    p = ProposedAsset(category=c.proposed_category, infrastructure_layer=c.infrastructure_layer,
                      route_id=c.route_id, observation_count=1, best_confidence=c.confidence,
                      best_candidate_id=c.id, latitude=c.latitude, longitude=c.longitude,
                      status=CandidateStatus.PENDING_VALIDATION)
    db.add(p); db.flush()
    c.proposed_asset_id = p.id
    db.commit()
    return {"split": cand_id, "new_proposed_asset": p.id}

@router.post("/assets/candidates/{cand_id}/link-existing", dependencies=[VALIDATOR])
def link_existing(cand_id: int, asset_id: int = Form(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    c = db.get(CandidateAsset, cand_id)
    if not c:
        raise HTTPException(404, "Candidate not found")
    if not db.get(Asset, asset_id):
        raise HTTPException(404, "GIS asset not found")
    c.status = CandidateStatus.LINKED_TO_EXISTING
    if c.proposed_asset_id:
        p = db.get(ProposedAsset, c.proposed_asset_id)
        if p:
            p.gis_asset_id = asset_id
            p.status = CandidateStatus.LINKED_TO_EXISTING
    _feedback(db, c, "approve", user)
    db.commit()
    return {"candidate": cand_id, "linked_to_asset": asset_id}

@router.get("/assets/proposed", dependencies=[DRIVER])
def list_proposed(category: str | None = None, db: Session = Depends(get_db)):
    stmt = select(ProposedAsset).order_by(ProposedAsset.best_confidence.desc())
    if category:
        stmt = stmt.where(ProposedAsset.category == category)
    rows = db.scalars(stmt.limit(1000)).all()
    return [{"id": p.id, "category": p.category, "layer": p.infrastructure_layer,
             "route_id": p.route_id, "observations": p.observation_count,
             "best_confidence": p.best_confidence, "best_candidate_id": p.best_candidate_id,
             "latitude": p.latitude, "longitude": p.longitude,
             "status": p.status.value if hasattr(p.status, "value") else p.status} for p in rows]

@router.get("/overview", dependencies=[DRIVER])
def overview(db: Session = Depends(get_db)):
    """Rich aggregate for the command-center dashboard."""
    routes = db.scalars(select(Route).order_by(Route.id.desc())).all()
    # "active" = flagged active AND actually moving recently — avoids phantom
    # live routes left un-stopped when the phone app closed mid-session.
    cutoff = datetime.utcnow() - timedelta(minutes=20)
    active = 0
    for r in routes:
        if not r.active:
            continue
        last_gps = db.scalar(
            select(func.max(GPSPoint.captured_at)).where(GPSPoint.route_id == r.id)
        )
        if (last_gps and last_gps >= cutoff) or (not last_gps and r.started_at >= cutoff):
            active += 1

    # total surveyed distance across all routes (haversine over ordered points)
    distance_km = 0.0
    for r in routes:
        pts = db.execute(
            select(GPSPoint.latitude, GPSPoint.longitude)
            .where(GPSPoint.route_id == r.id).order_by(GPSPoint.captured_at)
        ).all()
        for p, q in zip(pts, pts[1:]):
            if None not in (p[0], p[1], q[0], q[1]):
                d = _haversine_km((p[0], p[1]), (q[0], q[1]))
                if d < 0.5:  # skip GPS jumps
                    distance_km += d

    def count(model):
        return db.scalar(select(func.count()).select_from(model)) or 0

    det_by_status = dict(db.execute(
        select(Detection.status, func.count()).group_by(Detection.status)
    ).all())
    biz_rows = db.execute(
        select(Business.category, func.count()).group_by(Business.category)
    ).all()
    train_rows = db.execute(
        select(TrainingSample.asset_type, func.count()).group_by(TrainingSample.asset_type)
    ).all()

    recent_biz = db.scalars(
        select(Business).order_by(Business.created_at.desc()).limit(6)
    ).all()

    return {
        "routes": {"total": len(routes), "active": active},
        "distance_km": round(distance_km, 2),
        "gps_points": count(GPSPoint),
        "images": count(CapturedImage),
        "videos": count(VideoSegment),
        "detections": {
            "total": count(Detection),
            "pending": det_by_status.get(DetectionStatus.DRAFT, 0),
            "approved": det_by_status.get(DetectionStatus.APPROVED, 0),
        },
        "businesses": {
            "total": count(Business),
            "by_category": {c: n for c, n in biz_rows},
        },
        "training": {
            "total": count(TrainingSample),
            "by_type": {t: n for t, n in train_rows},
        },
        "recent_businesses": [
            {"id": b.id, "name": b.name, "category": b.category,
             "confidence": b.confidence, "status": b.status.value}
            for b in recent_biz
        ],
    }


@router.get("/dashboard", dependencies=[DRIVER])
def dashboard(db: Session = Depends(get_db)):
    asset_count = db.scalar(select(func.count()).select_from(Asset)) or 0
    route_count = db.scalar(select(func.count()).select_from(Route)) or 0
    detection_count = db.scalar(select(func.count()).select_from(Detection)) or 0
    ticket_count = db.scalar(select(func.count()).select_from(Ticket)) or 0
    business_count = db.scalar(select(func.count()).select_from(Business)) or 0
    return {
        "assets": asset_count,
        "routes": route_count,
        "detections": detection_count,
        "tickets": ticket_count,
        "businesses": business_count,
        "layers": [
            "telecom", "electricity", "water", "sewage",
            "drainage", "tunnel", "road", "public_space"
        ],
    }
