from datetime import datetime
from pydantic import BaseModel, ConfigDict
from app.models.entities import InfrastructureLayer, AssetStatus, DetectionStatus

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
    captured_at: datetime | None = None

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

class DetectionOut(BaseModel):
    id: int
    route_id: int | None
    video_segment_id: int | None
    proposed_asset_type: str
    proposed_layer: InfrastructureLayer
    confidence: float
    latitude: float | None
    longitude: float | None
    status: DetectionStatus
    snapshot_path: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

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
