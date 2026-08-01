-- Hazard detection transparency (ChatGPT review): separate the single generic
-- confidence into its evidence components, store geometry validity + rejection
-- reasons, and keep robust multi-frame tilt statistics instead of a single
-- noisy angle. All additive and idempotent.

-- Per-observation evidence components
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS detector_confidence DOUBLE PRECISION;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS validation_confidence DOUBLE PRECISION;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS geometry_confidence DOUBLE PRECISION;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS temporal_confidence DOUBLE PRECISION;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS final_confidence DOUBLE PRECISION;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS angle_is_valid BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS rejection_reasons TEXT;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS validation_version VARCHAR(40);
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS track_id VARCHAR(60);
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS base_occluded BOOLEAN;
ALTER TABLE hazard_observations ADD COLUMN IF NOT EXISTS occlusion_reason VARCHAR(80);

-- Per-hazard robust aggregates
ALTER TABLE hazards ADD COLUMN IF NOT EXISTS tilt_stddev_deg DOUBLE PRECISION;
ALTER TABLE hazards ADD COLUMN IF NOT EXISTS valid_frame_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE hazards ADD COLUMN IF NOT EXISTS geometry_confidence DOUBLE PRECISION;
ALTER TABLE hazards ADD COLUMN IF NOT EXISTS final_confidence DOUBLE PRECISION;
