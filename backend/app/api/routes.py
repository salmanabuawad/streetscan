from datetime import datetime, timezone
from pathlib import Path
import uuid
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select, func, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.entities import Route, GPSPoint, VideoSegment, Asset, Detection, Ticket, DetectionStatus, InfrastructureLayer
from app.schemas.common import RouteCreate, RouteOut, GPSPointCreate, AssetCreate, AssetOut, DetectionOut, TicketCreate, TicketOut, SegmentOut

router = APIRouter()

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

@router.post("/routes", response_model=RouteOut)
def create_route(payload: RouteCreate, db: Session = Depends(get_db)):
    route = Route(vehicle_name=payload.vehicle_name, driver_name=payload.driver_name)
    db.add(route)
    db.commit()
    db.refresh(route)
    return route

@router.post("/routes/{route_id}/stop", response_model=RouteOut)
def stop_route(route_id: int, db: Session = Depends(get_db)):
    route = db.get(Route, route_id)
    if not route:
        raise HTTPException(404, "Route not found")
    route.active = False
    route.ended_at = datetime.utcnow()
    db.commit()
    db.refresh(route)
    return route

@router.get("/routes", response_model=list[RouteOut])
def list_routes(db: Session = Depends(get_db)):
    return db.scalars(select(Route).order_by(Route.started_at.desc()).limit(100)).all()

@router.post("/gps")
def add_gps_point(payload: GPSPointCreate, db: Session = Depends(get_db)):
    point = GPSPoint(
        route_id=payload.route_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_m=payload.accuracy_m,
        speed_mps=payload.speed_mps,
        captured_at=to_naive_utc(payload.captured_at) if payload.captured_at else datetime.utcnow(),
    )
    db.add(point)
    db.commit()
    return {"id": point.id}

@router.post("/video-segments")
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

@router.get("/routes/{route_id}/segments", response_model=list[SegmentOut])
def list_route_segments(route_id: int, db: Session = Depends(get_db)):
    if not db.get(Route, route_id):
        raise HTTPException(404, "Route not found")
    return db.scalars(
        select(VideoSegment).where(VideoSegment.route_id == route_id)
        .order_by(VideoSegment.captured_at)
    ).all()

@router.get("/video-segments/{segment_id}/stream")
def stream_video_segment(segment_id: int, db: Session = Depends(get_db)):
    segment = db.get(VideoSegment, segment_id)
    if not segment:
        raise HTTPException(404, "Segment not found")
    path = Path(segment.filename)
    if not path.is_file():
        raise HTTPException(404, "Video file missing")
    # FileResponse handles Range requests, so <video> seeking works.
    return FileResponse(path, media_type=segment.mime_type or "video/webm")

@router.delete("/video-segments/{segment_id}")
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

@router.delete("/routes/{route_id}")
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
    db.execute(update(Detection).where(Detection.route_id == route_id)
               .values(route_id=None))
    for filename in db.scalars(
        select(VideoSegment.filename).where(VideoSegment.route_id == route_id)
    ):
        Path(filename).unlink(missing_ok=True)
    db.delete(route)  # cascades gps_points + video_segments
    db.commit()
    return {"deleted": route_id, "segments_deleted": len(segment_ids)}

@router.post("/assets", response_model=AssetOut)
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)):
    asset = Asset(**payload.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset

@router.get("/assets", response_model=list[AssetOut])
def list_assets(layer: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Asset).order_by(Asset.created_at.desc())
    if layer:
        try:
            stmt = stmt.where(Asset.layer == InfrastructureLayer(layer))
        except ValueError:
            raise HTTPException(400, f"Unknown layer '{layer}'")
    return db.scalars(stmt.limit(500)).all()

@router.get("/detections", response_model=list[DetectionOut])
def list_detections(db: Session = Depends(get_db)):
    return db.scalars(select(Detection).order_by(Detection.created_at.desc()).limit(500)).all()

@router.post("/detections/{detection_id}/approve", response_model=AssetOut)
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

@router.get("/detections/{detection_id}/snapshot")
def detection_snapshot(detection_id: int, db: Session = Depends(get_db)):
    detection = db.get(Detection, detection_id)
    if not detection or not detection.snapshot_path:
        raise HTTPException(404, "Snapshot not found")
    path = Path(detection.snapshot_path)
    if not path.is_file():
        raise HTTPException(404, "Snapshot file missing")
    return FileResponse(path, media_type="image/jpeg")

@router.post("/detections/{detection_id}/reject", response_model=DetectionOut)
def reject_detection(detection_id: int, db: Session = Depends(get_db)):
    detection = db.get(Detection, detection_id)
    if not detection:
        raise HTTPException(404, "Detection not found")
    detection.status = DetectionStatus.REJECTED
    db.commit()
    db.refresh(detection)
    return detection

@router.post("/tickets", response_model=TicketOut)
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

@router.get("/tickets", response_model=list[TicketOut])
def list_tickets(db: Session = Depends(get_db)):
    return db.scalars(select(Ticket).order_by(Ticket.created_at.desc()).limit(500)).all()

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    asset_count = db.scalar(select(func.count()).select_from(Asset)) or 0
    route_count = db.scalar(select(func.count()).select_from(Route)) or 0
    detection_count = db.scalar(select(func.count()).select_from(Detection)) or 0
    ticket_count = db.scalar(select(func.count()).select_from(Ticket)) or 0
    return {
        "assets": asset_count,
        "routes": route_count,
        "detections": detection_count,
        "tickets": ticket_count,
        "layers": [
            "telecom", "electricity", "water", "sewage",
            "drainage", "tunnel", "road", "public_space"
        ],
    }
