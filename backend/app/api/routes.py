from datetime import datetime, timezone
from pathlib import Path
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_password, hash_password, create_token
from app.db.session import get_db
from app.api.deps import get_current_user, require_role, ROLE_RANK
from app.models.entities import (
    Route, GPSPoint, VideoSegment, CapturedImage, Asset, Detection, Ticket, Business, TrainingSample,
    DetectionStatus, InfrastructureLayer, User, UserRole,
)
from app.schemas.common import (
    RouteCreate, RouteOut, GPSPointCreate, AssetCreate, AssetOut, DetectionOut,
    TicketCreate, TicketOut, SegmentOut, ImageOut, LoginIn, LoginOut, UserCreate, UserOut,
    BusinessOut, BusinessEdit, TrainingSampleOut, BBoxIn,
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
        layer="other",
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

@router.get("/detections", response_model=list[DetectionOut], dependencies=[DRIVER])
def list_detections(db: Session = Depends(get_db)):
    return db.scalars(select(Detection).order_by(Detection.created_at.desc()).limit(500)).all()

@router.post("/detections/{detection_id}/approve", response_model=AssetOut, dependencies=[VALIDATOR])
def approve_detection(detection_id: int, db: Session = Depends(get_db)):
    detection = db.get(Detection, detection_id)
    if not detection:
        raise HTTPException(404, "Detection not found")
    detection.status = DetectionStatus.APPROVED
    asset = Asset(
        name=f"Detected {detection.proposed_asset_type}",
        asset_type=detection.proposed_asset_type,
        layer=detection.proposed_layer,
        latitude=detection.latitude,
        longitude=detection.longitude,
        source="ai_validated",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset

@router.get("/detections/{detection_id}/snapshot", dependencies=[DRIVER])
def detection_snapshot(detection_id: int, db: Session = Depends(get_db)):
    detection = db.get(Detection, detection_id)
    if not detection or not detection.snapshot_path:
        raise HTTPException(404, "Snapshot not found")
    path = Path(detection.snapshot_path)
    if not path.is_file():
        raise HTTPException(404, "Snapshot file missing")
    return FileResponse(path, media_type="image/jpeg")

@router.post("/detections/{detection_id}/reject", response_model=DetectionOut, dependencies=[VALIDATOR])
def reject_detection(detection_id: int, db: Session = Depends(get_db)):
    detection = db.get(Detection, detection_id)
    if not detection:
        raise HTTPException(404, "Detection not found")
    detection.status = DetectionStatus.REJECTED
    db.commit()
    db.refresh(detection)
    return detection

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
        if points:
            tracks.append({
                "route_id": route.id,
                "vehicle_name": route.vehicle_name,
                "points": [[p.latitude, p.longitude] for p in points],
            })
    return {
        "assets": [{
            "id": a.id, "name": a.name, "asset_type": a.asset_type,
            "layer": a.layer.value, "status": a.status.value,
            "lat": a.latitude, "lng": a.longitude, "underground": a.underground,
        } for a in assets],
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


@router.get("/overview", dependencies=[DRIVER])
def overview(db: Session = Depends(get_db)):
    """Rich aggregate for the command-center dashboard."""
    routes = db.scalars(select(Route).order_by(Route.id.desc())).all()
    active = sum(1 for r in routes if r.active)

    # total surveyed distance across all routes (haversine over ordered points)
    distance_km = 0.0
    for r in routes:
        pts = db.scalars(
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
