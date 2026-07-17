from __future__ import annotations
import enum
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, Text, Boolean, Integer, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.session import Base

class InfrastructureLayer(str, enum.Enum):
    TELECOM = "telecom"
    ELECTRICITY = "electricity"
    WATER = "water"
    SEWAGE = "sewage"
    DRAINAGE = "drainage"
    TUNNEL = "tunnel"
    ROAD = "road"
    PUBLIC_SPACE = "public_space"

class AssetStatus(str, enum.Enum):
    ACTIVE = "active"
    DAMAGED = "damaged"
    MISSING = "missing"
    UNKNOWN = "unknown"

class DetectionStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"

class UserRole(str, enum.Enum):
    DRIVER = "driver"
    VALIDATOR = "validator"
    ADMIN = "admin"

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(120))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.DRIVER)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Route(Base):
    __tablename__ = "routes"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_name: Mapped[str] = mapped_column(String(120))
    driver_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    gps_points: Mapped[list["GPSPoint"]] = relationship(back_populates="route", cascade="all, delete-orphan")
    video_segments: Mapped[list["VideoSegment"]] = relationship(back_populates="route", cascade="all, delete-orphan")

class GPSPoint(Base):
    __tablename__ = "gps_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    route: Mapped["Route"] = relationship(back_populates="gps_points")

class VideoSegment(Base):
    __tablename__ = "video_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    # screen.orientation.angle at record time (0/90/180/270). Phone browsers
    # keep the camera buffer orientation fixed while the device rotates, so
    # the worker needs this hint to upright the frames before inference.
    orientation_hint: Mapped[int] = mapped_column(Integer, default=0)
    route: Mapped["Route"] = relationship(back_populates="video_segments")

class CapturedImage(Base):
    """High-resolution stills from the adaptive capture engine (slow speeds
    and stop bursts) — sharper input than video frames for detection and OCR."""
    __tablename__ = "captured_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), default="interval")  # interval | stop_burst | manual
    blur_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)       # YOLO
    ocr_processed: Mapped[bool] = mapped_column(Boolean, default=False)   # Tesseract

class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(180))
    asset_type: Mapped[str] = mapped_column(String(120))
    layer: Mapped[InfrastructureLayer] = mapped_column(Enum(InfrastructureLayer))
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.UNKNOWN)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    underground: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(80), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Detection(Base):
    __tablename__ = "detections"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id"), nullable=True)
    video_segment_id: Mapped[int | None] = mapped_column(ForeignKey("video_segments.id"), nullable=True)
    image_id: Mapped[int | None] = mapped_column(ForeignKey("captured_images.id"), nullable=True)
    proposed_asset_type: Mapped[str] = mapped_column(String(120))
    proposed_layer: Mapped[InfrastructureLayer] = mapped_column(Enum(InfrastructureLayer))
    confidence: Mapped[float] = mapped_column(Float)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[DetectionStatus] = mapped_column(Enum(DetectionStatus), default=DetectionStatus.DRAFT)
    snapshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Business(Base):
    """A storefront/business recognized from a sign via OCR, pending validation.
    Approved businesses become part of the municipal point-of-interest layer."""
    __tablename__ = "businesses"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id"), nullable=True)
    image_id: Mapped[int | None] = mapped_column(ForeignKey("captured_images.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(80), default="unknown")
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    languages: Mapped[str | None] = mapped_column(String(40), nullable=True)  # e.g. "ar,he"
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[DetectionStatus] = mapped_column(Enum(DetectionStatus), default=DetectionStatus.DRAFT)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str] = mapped_column(String(120))
    priority: Mapped[str] = mapped_column(String(40), default="medium")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    detection_id: Mapped[int | None] = mapped_column(ForeignKey("detections.id"), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
