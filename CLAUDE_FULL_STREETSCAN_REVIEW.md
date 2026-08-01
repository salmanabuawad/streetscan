# Claude Code — Full StreetScan Review and Required Fixes

## Context

I reviewed the uploaded `buqata-streetscan` source code together with the exported results from `route1_export`.

The current output is not production-ready. The largest problem is not the UI. It is that the detection pipeline creates hazards from very weak open-vocabulary detections and uses invalid geometry for leaning-pole measurement.

This task is a full correction pass. Do not patch only the visible 27° example.

---

# 1. Verified problems in the exported results

## Hazard quality statistics

The export contains:

- 21 hazards
- 43 hazard observations
- 16 candidate assets

Hazard confidence statistics:

- Minimum: 0.1107
- Median: 0.1883
- Maximum: 0.556
- 20 of 21 hazards are below 0.50 confidence
- All 21 hazards are below 0.70 confidence

Observation confidence statistics:

- Minimum: 0.1107
- Median: 0.1585
- Maximum: 0.556
- 42 of 43 observations are below 0.50 confidence
- All 43 observations are below 0.70 confidence

Despite this, the system created:

- 3 hazards with status `OPEN`
- 13 hazards with status `PENDING_REVIEW`
- 5 leaning-pole hazards with status `SUSPECTED`

This is incorrect. A detection with confidence around 0.11–0.20 must not become an active municipal hazard.

## Leaning-pole results

The five exported leaning-pole hazards are:

| Confidence | Reported tilt | Base visible | Status |
|---:|---:|---|---|
| 0.268 | 13.0° | true | SUSPECTED |
| 0.307 | 3.9° | true | SUSPECTED |
| 0.556 | 20.2° | true | SUSPECTED |
| 0.295 | 27.0° | true | SUSPECTED |
| 0.479 | 13.1° | true | SUSPECTED |

These results are not trustworthy.

The annotated images show several cases where:

- the bounding box covers a large part of the road or sky;
- the detected object is not tightly isolated;
- cables cross the bounding box;
- the line used for tilt appears to follow a cable or unrelated edge;
- the true pole base is not visible;
- the camera/dashboard occludes the bottom of the scene;
- the same physical scene is repeatedly classified as several hazards.

## Hazard category output

The route produced:

- 11 `overflowing_bin`
- 5 `leaning_pole`
- 2 `pothole`
- 2 `construction_debris`
- 1 `construction_materials`

The annotated output does not visually justify this quantity of confirmed or reviewable hazards. The open-vocabulary detector is being used as if it were a trained municipal hazard classifier.

---

# 2. Root cause in the source code

## 2.1 Invalid tilt algorithm

File:

```text
backend/app/tilt.py
```

Current behavior:

```python
pole_lines = _near_vertical_lines(cgray, max_from_vertical=40.0)
pole_lines.sort(key=lambda t: -t[1])
pole_ang, _len, (...) = pole_lines[0]
```

The algorithm takes the longest near-vertical Hough line inside a padded crop and assumes it is the pole axis.

This is invalid because the longest line may be:

- a cable;
- a building edge;
- the edge of the bounding box region;
- a road-side structure;
- a windshield/dashboard edge;
- only one side of a pole rather than its centerline.

The code has no pole mask, no segmentation, no centerline extraction, no overlap validation, and no robust geometry check.

## 2.2 Incorrect `base_visible`

Current code:

```python
base_visible = y2 <= h - 6
```

This only checks whether the bounding box touches the image bottom.

It does not check whether:

- the pole-ground connection is visible;
- a vehicle hides the pole;
- the dashboard hides the lower scene;
- a wall or vegetation hides the base;
- the object is cropped above ground;
- the detector box itself is inaccurate.

This explains why all exported leaning-pole hazards incorrectly show `base_visible: true`.

## 2.3 Weak confidence is converted into hazards

The hazard category default minimum confidence is around 0.10.

The open-vocabulary detector adapter accepts detections when:

```python
confidence >= category.min_confidence
```

The export confirms that many hazards were created with confidence close to 0.11.

A generic OWL-ViT score cannot be interpreted as enough evidence for a municipal hazard at these thresholds.

## 2.4 Leaning-pole confidence is not geometry confidence

Current code:

```python
confidence = a.confidence * (0.6 + 0.4 * res["confidence_factor"])
```

`confidence_factor` is only:

```python
line_length / crop_height
```

A long cable or building edge therefore increases confidence. This is the opposite of proper validation.

## 2.5 Single-frame results are presented as meaningful tilt

The code comments say that multiple frames should strengthen confidence, but the implementation stores each single-frame Hough estimate as a suspected hazard.

There is no actual track-level median, standard deviation, frame agreement, or stable object association before creating the hazard.

## 2.6 Deduplication is too permissive

Repeated detections from the same drive appear to be merged or retained using time and approximate location, but the system does not establish visual identity.

It may:

- merge separate bins into one hazard;
- split one pole into several hazards;
- merge unrelated detections because they occur close in time;
- treat multiple weak observations as confirmation.

## 2.7 Asset detection boxes are poor inputs for tilt

The candidate asset output includes better confidence than hazard detections, but many boxes are still extremely large or include cables and background.

Tilt analysis must not run on an asset box unless the box passes pole-specific validation.

---

# 3. Required architecture changes

## 3.1 Separate three concepts

Do not treat every model output as a hazard.

Use three stages:

```text
Raw detection
    ↓
Validated observation
    ↓
Confirmed/suspected municipal issue
```

Required states:

```text
RAW_DETECTION
REJECTED
NEEDS_BETTER_VIEW
VALIDATED_OBSERVATION
MULTI_FRAME_CANDIDATE
PENDING_REVIEW
CONFIRMED_HAZARD
```

A low-confidence model prediction should remain a raw detection or training sample.

## 3.2 Separate confidence fields

Replace one generic `confidence` value with:

```json
{
  "detector_confidence": 0.62,
  "object_quality_confidence": 0.74,
  "geometry_confidence": 0.18,
  "temporal_consistency_confidence": 0.00,
  "final_confidence": 0.21
}
```

Do not multiply arbitrary values without calibration.

## 3.3 Category-specific validation

Each category needs its own validation logic.

Examples:

### Overflowing bin

Require:

- a bin instance;
- visible waste extending beyond the container;
- bin-fill or overflow classifier;
- minimum visible-bin ratio;
- no false match on fences, cars, or yellow roadside objects.

### Pothole

Require:

- road-surface region;
- shape/texture evidence;
- exclusion of shadows, patches, gravel, dashboard, and roadside dirt;
- preferably depth or multi-frame persistence.

### Construction debris/materials

Require:

- object located on road, sidewalk, or public right-of-way;
- not inside a private construction lot;
- not ordinary exposed soil;
- spatial persistence across more than one scan for long-term enforcement.

### Leaning pole

Require pole segmentation, valid centerline geometry, camera-roll correction, and multi-frame agreement.

---

# 4. Correct leaning-pole implementation

## 4.1 Do not use whole-crop Hough line selection

Remove this decision rule:

```python
longest near-vertical Hough line = pole axis
```

Hough lines may be used only as secondary evidence after the pole is isolated.

## 4.2 Preferred pole geometry pipeline

Implement:

1. Detect pole instance.
2. Obtain a segmentation mask.
3. Remove cable-like thin connected components.
4. Sample horizontal mask slices.
5. Compute the median center X for each slice.
6. Remove outliers.
7. Fit a robust centerline with RANSAC.
8. Validate that the centerline stays inside the mask.
9. Estimate top and true visible bottom.
10. Correct for camera roll.
11. Aggregate across frames.

Required result:

```ts
type PoleGeometryResult = {
  detectorConfidence: number;
  maskQuality: number;
  centerlineInsideMaskRatio: number;
  visiblePoleRatio: number;
  rawAxisAngleDeg: number | null;
  verticalReferenceAngleDeg: number | null;
  correctedTiltDeg: number | null;
  geometryConfidence: number;
  angleIsValid: boolean;
  topVisible: boolean;
  baseVisible: boolean;
  baseOccluded: boolean;
  occlusionReason?: string;
  rejectionReasons: string[];
};
```

## 4.3 Camera vertical reference

Use this order:

1. IMU roll from camera/vehicle;
2. calibrated camera extrinsics;
3. stable vertical vanishing-point estimation from multiple scene structures;
4. no angle result when no reliable reference exists.

Do not use the pole itself as the vertical baseline.

## 4.4 Base visibility

`base_visible` must require actual visual evidence of the pole-ground connection.

Implement a conservative rule:

```python
base_visible = (
    bottom_endpoint_detected
    and ground_contact_region_visible
    and not occluded
    and not truncated
    and base_confidence >= 0.70
)
```

If uncertain, return false.

## 4.5 Angle validity

Use:

```python
angle_is_valid = (
    detector_confidence >= 0.65
    and mask_quality >= 0.70
    and centerline_inside_mask_ratio >= 0.85
    and visible_pole_ratio >= 0.60
    and geometry_confidence >= 0.70
    and vertical_reference_angle is not None
)
```

When invalid:

```json
{
  "corrected_tilt_deg": null,
  "angle_is_valid": false,
  "status": "needs_better_view"
}
```

Do not display a numeric angle.

## 4.6 Multi-frame confirmation

Track the same pole over consecutive frames using:

- object tracking;
- visual embedding similarity;
- projected map position;
- category;
- movement-consistent geometry.

Calculate:

```json
{
  "valid_frame_count": 12,
  "median_tilt_deg": 6.1,
  "tilt_stddev_deg": 0.8,
  "multi_frame_confirmed": true
}
```

Minimum requirements before creating a leaning-pole review item:

```text
at least 5 valid frames
median geometry confidence >= 0.70
tilt standard deviation <= 2.0°
median corrected tilt >= configured threshold
```

A single frame may only create `NEEDS_BETTER_VIEW`, not `SUSPECTED`.

---

# 5. Confidence and status rules

Use conservative defaults.

## Raw open-vocabulary detections

```text
< 0.30: store only as rejected/raw training data
0.30–0.50: raw candidate, hidden from normal hazard list
0.50–0.70: validation queue only
>= 0.70: eligible for category-specific validation
```

These thresholds must be configurable per category and calibrated against a labelled local validation set.

## Final hazard rules

```ts
if (!categoryValidationPassed) {
  status = "REJECTED";
}

if (finalConfidence < 0.50) {
  status = "RAW_DETECTION";
}

if (finalConfidence >= 0.50 && finalConfidence < 0.75) {
  status = "PENDING_REVIEW";
}

if (finalConfidence >= 0.75 && multiFrameConfirmed) {
  status = "PENDING_REVIEW";
}

status = "OPEN" only after staff approval;
```

AI must never directly create `OPEN` hazards.

---

# 6. Fix severity logic

Severity and confidence are different.

A possible severe hazard with weak evidence must be:

```json
{
  "severity_if_confirmed": "HIGH",
  "confidence": 0.28,
  "status": "NEEDS_BETTER_VIEW"
}
```

Do not display it as a strong red municipal hazard until evidence is sufficient.

---

# 7. Fix deduplication

Current time-and-distance deduplication is not enough.

Add:

- visual similarity of crops;
- track ID within the same video;
- category consistency;
- object position relative to vehicle;
- heading-aware map projection;
- route direction;
- frame sequence;
- maximum physical movement for static assets.

For a static pole, all observations should map to one `asset_track_id`.

For bins, avoid merging different nearby bins based only on time.

Store:

```json
{
  "track_id": "...",
  "same_pass_observation_count": 8,
  "distinct_scan_days": 1,
  "visual_similarity": 0.91
}
```

Multiple frames from the same drive do not count as independent confirmation.

Use:

```text
same-pass frames = temporal support
different scan days = persistence support
```

---

# 8. Correct geolocation

The code currently projects every front-camera detection approximately 6 metres ahead.

This is too crude for municipal asset mapping.

At minimum estimate object bearing from the horizontal image coordinate:

```text
bearing offset = normalized_x × horizontal_FOV / 2
```

Then estimate range using:

- object size priors;
- road-ground intersection;
- depth model;
- stereo/multi-camera;
- multi-frame triangulation.

Store uncertainty ellipse, not only one point.

Do not merge objects using a projected point with false precision.

---

# 9. UI corrections

## Hazard cards

Show separate values:

- AI detector confidence
- validation confidence
- temporal support
- evidence quality
- number of frames
- number of independent scan days

Do not show `27°` when angle validation failed.

Use Hebrew messages:

```text
זוהה אובייקט אפשרי, אך איכות הראיות אינה מספיקה לפתיחת מפגע.
```

```text
זוהה עמוד, אך לא ניתן למדוד את הנטייה בצורה אמינה.
```

```text
הזיהוי מבוסס על פריים יחיד ודורש צילום נוסף.
```

## Debug view

Show:

- detector box;
- segmentation mask;
- centerline;
- corrected vertical;
- rejected cable lines;
- base visibility region;
- confidence components;
- frame track ID.

The normal user view should not show misleading raw geometry.

## Filters

Default hazard list should exclude:

- `RAW_DETECTION`;
- `REJECTED`;
- confidence below 0.50;
- single-frame unvalidated candidates.

Add a separate AI-review workspace.

---

# 10. Model strategy

OWL-ViT is useful for bootstrapping labels, not as the final production detector for every category.

Use exported human-reviewed data to train category-specific models.

Recommended approach:

- YOLO segmentation/detection for poles, bins, signs, vehicles;
- road-surface segmentation;
- dedicated pothole/debris classifier;
- pole segmentation model;
- temporal tracking such as ByteTrack;
- optional monocular depth;
- local labelled Buq’ata dataset.

Open-vocabulary detections should be used to propose annotations for staff review.

---

# 11. Database/schema migration

Add fields to observations:

```text
detector_confidence
validation_confidence
geometry_confidence
temporal_confidence
final_confidence
angle_is_valid
vertical_reference_deg
camera_roll_deg
mask_path
track_id
valid_frame_count
tilt_stddev_deg
base_occluded
occlusion_reason
rejection_reasons
validation_version
```

Do not overwrite the worst or maximum angle as the hazard angle.

For leaning poles store:

```text
median_valid_tilt
max_valid_tilt
latest_valid_tilt
tilt_stddev
```

The current code uses:

```python
hz.tilt_degrees = max(previous, new)
```

This biases the result toward the largest error. Replace it with robust aggregation over valid observations.

---

# 12. Automated tests required

## Tilt tests

1. Straight pole crossed by diagonal cable
2. Straight pole with camera roll
3. Leaning pole with visible base
4. Pole base hidden by dashboard
5. Pole base hidden by car
6. Building edge inside detector box
7. Pole partially outside frame
8. Large bounding box containing several objects
9. Cable longer than pole edge
10. Multi-frame stable tilt
11. Multi-frame unstable tilt
12. One bad frame among valid frames

## Hazard lifecycle tests

1. 0.11 confidence does not create hazard
2. 0.30 confidence remains raw
3. 0.55 detection enters validation only
4. AI never directly creates `OPEN`
5. repeated frames from same clip do not count as independent scans
6. different objects close in time are not merged only by time
7. same object across frames receives one track ID
8. maximum noisy tilt does not replace median valid tilt
9. invalid geometry does not expose an angle
10. rejected observation is retained for training but hidden from operational list

## Category tests

Add labelled positive and negative examples for:

- overflowing bins;
- ordinary bins;
- roadside soil;
- private construction yard;
- road pothole;
- shadow/road patch;
- straight utility pole;
- leaning utility pole.

---

# 13. Acceptance criteria for the supplied route export

After reprocessing the same route:

1. No hazard with final confidence below 0.50 appears in the operational hazard list.
2. No AI-created hazard has status `OPEN`.
3. The 27° leaning-pole result is removed or becomes `NEEDS_BETTER_VIEW`.
4. Hidden pole bases return `base_visible: false`.
5. Cable lines are not displayed as pole centerlines.
6. Repeated frames from one pass are grouped into tracks.
7. The route should produce far fewer operational hazards than the current 21 unless visually validated.
8. All rejected detections remain available in the AI training/review dataset.
9. Every displayed angle contains `angle_is_valid: true`.
10. The UI clearly distinguishes model suggestion from confirmed municipal hazard.

---

# 14. Implementation order

Perform the work in this order:

1. Add regression tests reproducing the current failures.
2. Prevent low-confidence detections from creating active hazards.
3. Ensure AI never creates `OPEN`.
4. Replace maximum-angle storage with robust valid-angle aggregation.
5. Correct `base_visible`.
6. Replace longest-Hough-line pole axis logic.
7. Add pole segmentation and centerline validation.
8. Add multi-frame tracking and aggregation.
9. Improve deduplication and geolocation.
10. Update API schema and UI.
11. Reprocess the supplied route.
12. Produce a before/after report with:
    - raw detections;
    - rejected detections;
    - review candidates;
    - confirmed hazards;
    - reasons for rejection.

Do not solve this by increasing or decreasing one global threshold only. The system needs category-specific validation and separation between raw AI output and operational hazards.
