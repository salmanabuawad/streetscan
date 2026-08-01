"""Road-hazard detection & management domain (מפגעים).

A parallel domain to the infrastructure-asset flow, built on the same proven
shape: config-driven categories -> per-frame observations -> deduplicated
lifecycle entity (Hazard) -> human review -> department assignment -> tracking
-> close. Reuses Route/VideoSegment/CapturedImage/GPSPoint/User/ModelVersion.

Positions use plain lat/lng floats with Python distance (village-scale, matches
the rest of the app); PostGIS is a future optimisation, not required here.
"""
from __future__ import annotations
import enum
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, Text, Boolean, Integer, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class HazardStatus(str, enum.Enum):
    SUSPECTED = "suspected"             # detected but needs field inspection (e.g. leaning pole)
    PENDING_REVIEW = "pending_review"   # medium confidence — awaits staff approval
    OPEN = "open"                       # confirmed active hazard
    IN_PROGRESS = "in_progress"         # assigned / under treatment
    LIKELY_FIXED = "likely_fixed"       # not seen on a recent scan — needs verification
    CLOSED = "closed"                   # verified resolved
    REOPENED = "reopened"               # seen again after closing
    REJECTED = "rejected"               # false positive


class HazardSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HazardSource(str, enum.Enum):
    AI = "ai"
    STAFF = "staff"
    RESIDENT = "resident"
    HOTLINE = "hotline"


class HazardCategory(Base):
    """Config-driven hazard type — editable by an admin, NOT hard-coded.
    Holds the open-vocabulary prompts, per-type confidence threshold, dedup
    radius, default severity/department and whether AI may auto-open a hazard
    (false for abandoned vehicles and possible-new-business, which always need
    staff confirmation)."""
    __tablename__ = "hazard_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(60), unique=True)          # machine key, e.g. "pothole"
    name_he: Mapped[str] = mapped_column(String(120))
    name_ar: Mapped[str | None] = mapped_column(String(120), nullable=True)
    name_en: Mapped[str | None] = mapped_column(String(120), nullable=True)
    group: Mapped[str] = mapped_column(String(40), default="road")     # road|sidewalk|construction|signage|sanitation|vehicle|business
    detection_prompts: Mapped[str] = mapped_column(Text, default="")   # newline-separated OWL-ViT prompts
    active_detector: Mapped[str] = mapped_column(String(30), default="openvocab")
    min_confidence: Mapped[float] = mapped_column(Float, default=0.10)
    auto_open_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    default_severity: Mapped[HazardSeverity] = mapped_column(Enum(HazardSeverity), default=HazardSeverity.MEDIUM)
    dedup_radius_m: Mapped[float] = mapped_column(Float, default=5.0)
    # tilt thresholds (deg) for leaning-pole categories: <monitor ok, monitor..suspect track,
    # suspect..high suspected, >high high-priority. Per-subtype overrides live in app.tilt.
    tilt_monitor_deg: Mapped[float] = mapped_column(Float, default=3.0)
    tilt_suspect_deg: Mapped[float] = mapped_column(Float, default=5.0)
    tilt_high_deg: Mapped[float] = mapped_column(Float, default=10.0)
    department: Mapped[str | None] = mapped_column(String(80), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_mvp: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class HazardSeverityRule(Base):
    """Admin-tunable escalation for a category's base severity. The service
    applies: start at base -> escalate one level if it blocks a lane / is near a
    sensitive site (school, junction) -> force CRITICAL if it is an electrical /
    fire / fall danger."""
    __tablename__ = "hazard_severity_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_key: Mapped[str | None] = mapped_column(String(60), nullable=True)   # null = applies to all
    base_severity: Mapped[HazardSeverity] = mapped_column(Enum(HazardSeverity), default=HazardSeverity.MEDIUM)
    escalate_if_blocks_path: Mapped[bool] = mapped_column(Boolean, default=True)
    escalate_if_near_sensitive: Mapped[bool] = mapped_column(Boolean, default=True)
    critical_if_danger: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    plate: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(40), default="garbage_truck")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Camera(Base):
    """One camera on a vehicle's roof rig. Position/angle/height feed the hazard
    location estimate; per-camera stats feed the model dashboard."""
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(80))                      # front|left|right|down
    position: Mapped[str] = mapped_column(String(20), default="front")
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    pitch_deg: Mapped[float | None] = mapped_column(Float, nullable=True)   # downward tilt
    yaw_deg: Mapped[float | None] = mapped_column(Float, nullable=True)     # offset from travel direction
    hfov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Hazard(Base):
    """A real-world hazard, deduplicated from many observations. The unit of
    work: it has a lifecycle, severity, department and full observation history."""
    __tablename__ = "hazards"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_key: Mapped[str] = mapped_column(String(60))
    subtype: Mapped[str | None] = mapped_column(String(120), nullable=True)
    route_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # scan the hazard belongs to
    status: Mapped[HazardStatus] = mapped_column(Enum(HazardStatus), default=HazardStatus.PENDING_REVIEW)
    severity: Mapped[HazardSeverity] = mapped_column(Enum(HazardSeverity), default=HazardSeverity.MEDIUM)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    street_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    observation_count: Mapped[int] = mapped_column(Integer, default=1)
    distinct_scan_days: Mapped[int] = mapped_column(Integer, default=1)   # for abandoned-vehicle rule
    missed_scans: Mapped[int] = mapped_column(Integer, default=0)         # consecutive scans not seen
    assigned_department: Mapped[str | None] = mapped_column(String(80), nullable=True)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    source: Mapped[HazardSource] = mapped_column(Enum(HazardSource), default=HazardSource.AI)
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("hazards.id"), nullable=True)
    estimated_size: Mapped[str | None] = mapped_column(String(40), nullable=True)  # small|medium|large
    estimated_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocks_path: Mapped[bool] = mapped_column(Boolean, default=False)
    near_sensitive: Mapped[bool] = mapped_column(Boolean, default=False)
    is_danger: Mapped[bool] = mapped_column(Boolean, default=False)       # electrical/fire/fall
    # leaning-pole tracking: worst tilt seen, whether a base was ever visible, tilt trend
    tilt_degrees: Mapped[float | None] = mapped_column(Float, nullable=True)  # median of valid frames
    tilt_stddev_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid_frame_count: Mapped[int] = mapped_column(Integer, default=0)
    geometry_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_visible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tilt_worsening: Mapped[bool] = mapped_column(Boolean, default=False)
    best_observation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detector_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HazardObservation(Base):
    """One sighting of a hazard in one frame (the AI detection record). Many
    observations dedup into one Hazard; low-confidence ones are kept purely as
    training signal (no active hazard opened)."""
    __tablename__ = "hazard_observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    hazard_id: Mapped[int | None] = mapped_column(ForeignKey("hazards.id"), nullable=True)
    category_key: Mapped[str] = mapped_column(String(60))
    subtype: Mapped[str | None] = mapped_column(String(120), nullable=True)
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id"), nullable=True)
    image_id: Mapped[int | None] = mapped_column(ForeignKey("captured_images.id"), nullable=True)
    video_segment_id: Mapped[int | None] = mapped_column(ForeignKey("video_segments.id"), nullable=True)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id"), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_band: Mapped[str] = mapped_column(String(10), default="low")
    severity: Mapped[HazardSeverity | None] = mapped_column(Enum(HazardSeverity), nullable=True)
    bbox: Mapped[str | None] = mapped_column(String(80), nullable=True)
    crop_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    annotated_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)        # hazard position (estimated)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    vehicle_latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    vehicle_longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    image_quality: Mapped[str | None] = mapped_column(String(20), nullable=True)   # ok|poor|unusable
    quality_flags: Mapped[str | None] = mapped_column(String(160), nullable=True)  # night,rain,glare,dirty_lens...
    # leaning-pole tilt analysis (null for non-pole hazards)
    tilt_degrees: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_deg: Mapped[float | None] = mapped_column(Float, nullable=True)   # scene "vertical" reference used
    base_visible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cables_condition: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ok|suspected_tension|low|slack
    tilt_axis: Mapped[str | None] = mapped_column(String(60), nullable=True)   # "x1,y1,x2,y2" pole axis in image px
    image_score: Mapped[float] = mapped_column(Float, default=0.0)   # best-shot rank within the hazard
    # evidence components (ChatGPT review — one generic score is not enough)
    detector_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    temporal_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    angle_is_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    rejection_reasons: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    track_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    base_occluded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    occlusion_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detector_name: Mapped[str] = mapped_column(String(60), default="openvocab")
    detector_version: Mapped[str] = mapped_column(String(60), default="owlvit")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|duplicate|training_only
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HazardStatusHistory(Base):
    __tablename__ = "hazard_status_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    hazard_id: Mapped[int] = mapped_column(ForeignKey("hazards.id", ondelete="CASCADE"))
    old_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_status: Mapped[str] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HazardAssignment(Base):
    __tablename__ = "hazard_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    hazard_id: Mapped[int] = mapped_column(ForeignKey("hazards.id", ondelete="CASCADE"))
    department: Mapped[str] = mapped_column(String(80))
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="assigned")   # assigned|acknowledged|done
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HazardFeedback(Base):
    """Every staff correction on a hazard observation — the training signal for
    the municipal hazard model (parallel to TrainingFeedback for assets)."""
    __tablename__ = "hazard_feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    observation_id: Mapped[int | None] = mapped_column(ForeignKey("hazard_observations.id"), nullable=True)
    hazard_id: Mapped[int | None] = mapped_column(ForeignKey("hazards.id"), nullable=True)
    feedback_type: Mapped[str] = mapped_column(String(40))   # confirm|false_positive|correct_category|correct_severity|correct_location|missed
    corrected_category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    corrected_severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    detector_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Who viewed / changed / downloaded what — required for the privacy layer."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(40))          # view|update|download|export|assign|close
    entity: Mapped[str] = mapped_column(String(40))          # hazard|observation|image
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
