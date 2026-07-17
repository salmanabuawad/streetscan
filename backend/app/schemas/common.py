from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.models.entities import InfrastructureLayer, AssetStatus, DetectionStatus

class LoginIn(BaseModel):
    username: str
    password: str

class LoginOut(BaseModel):
    token: str
    username: str
    display_name: str
    role: str

class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "driver"

class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    active: bool
    model_config = ConfigDict(from_attributes=True)

class RouteCreate(BaseModel):
    vehicle_name: str
    driver_name: str | None = None

class RouteOut(BaseModel):
    id: int
    vehicle_name: str
    driver_name: str | None
    started_at: datetime
    ended_at: datetime | None
    active: bool
    model_config = ConfigDict(from_attributes=True)

class GPSPointCreate(BaseModel):
    route_id: int
    latitude: float
    longitude: float
    accuracy_m: float | None = None
    speed_mps: float | None = None
    heading_deg: float | None = None
    captured_at: datetime | None = None

class ImageOut(BaseModel):
    id: int
    route_id: int
    size_bytes: int
    captured_at: datetime
    latitude: float | None
    longitude: float | None
    heading_deg: float | None
    speed_mps: float | None
    kind: str
    blur_score: float | None
    processed: bool
    model_config = ConfigDict(from_attributes=True)

class SegmentOut(BaseModel):
    id: int
    route_id: int
    mime_type: str
    size_bytes: int
    captured_at: datetime
    processed: bool
    orientation_hint: int
    model_config = ConfigDict(from_attributes=True)

class AssetCreate(BaseModel):
    external_id: str | None = None
    name: str
    asset_type: str
    layer: InfrastructureLayer
    status: AssetStatus = AssetStatus.UNKNOWN
    latitude: float | None = None
    longitude: float | None = None
    underground: bool = False
    source: str = "manual"
    notes: str | None = None

class AssetOut(AssetCreate):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class TrainingSampleOut(BaseModel):
    id: int
    asset_name: str
    asset_type: str
    layer: str
    notes: str | None
    latitude: float | None
    longitude: float | None
    bbox_cx: float | None
    bbox_cy: float | None
    bbox_w: float | None
    bbox_h: float | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class BBoxIn(BaseModel):
    bbox_cx: float
    bbox_cy: float
    bbox_w: float
    bbox_h: float
    asset_type: str | None = None
    asset_name: str | None = None


class VideoAnnotationIn(BBoxIn):
    timestamp_s: float
    layer: str = "other"
    notes: str | None = None

class BusinessOut(BaseModel):
    id: int
    route_id: int | None
    image_id: int | None
    name: str
    category: str
    ocr_text: str | None
    languages: str | None
    confidence: float
    latitude: float | None
    longitude: float | None
    status: DetectionStatus
    snapshot_path: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class BusinessEdit(BaseModel):
    name: str | None = None
    category: str | None = None

class TicketCreate(BaseModel):
    title: str
    description: str | None = None
    department: str
    priority: str = "medium"
    asset_id: int | None = None
    detection_id: int | None = None
    latitude: float | None = None
    longitude: float | None = None

class TicketOut(BaseModel):
    id: int
    title: str
    description: str | None
    department: str
    priority: str
    status: str
    asset_id: int | None
    detection_id: int | None
    latitude: float | None
    longitude: float | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
