# Instructions for Claude Code

The repository has already received an immediate safety correction. Continue from the current code; do not revert it.

## Corrections already applied

- AI detections below 0.50 are kept as training-only observations.
- Leaning-pole detections require at least 0.65 confidence.
- AI can no longer create an `OPEN` municipal hazard directly.
- The old longest-Hough-line tilt rule was removed.
- Tilt now requires two long parallel pole-like edges plus an independent scene-vertical reference.
- Invalid geometry produces no stored/displayed angle.
- `base_visible` is conservatively false until ground contact is actually validated.
- Representative pole tilt uses the median of valid observations, not the maximum.
- Five stable observations with standard deviation <=2° are required before escalating a leaning-pole item to staff review.
- Basic tilt regression tests were added.

## Your required next work

1. Run all backend and frontend tests and fix regressions.
2. Add a real pole instance-segmentation model. The current parallel-edge method is only a conservative fallback.
3. Store geometry confidence, angle validity, rejection reasons, track ID, valid-frame count and tilt standard deviation in the database and API.
4. Add a proper migration for all new fields; do not rely on `create_all` for existing PostgreSQL installations.
5. Implement ByteTrack or equivalent frame tracking so repeated video frames are one track.
6. Do not count frames from one drive as independent scan days.
7. Replace coarse fixed-distance geolocation with image-bearing and range uncertainty.
8. Add category-specific validators for overflowing bins, potholes and construction debris.
9. Keep raw AI detections out of the normal operational hazard list.
10. Update the UI to distinguish detector confidence, geometry confidence, temporal support and human confirmation.
11. Reprocess the supplied route export and produce a before/after report.

## Non-negotiable rules

- Never restore AI auto-open behavior.
- Never display an angle unless `angle_is_valid` is true.
- Never infer `base_visible` from bounding-box position alone.
- Never use the largest observed tilt as the representative tilt.
- Never use an arbitrary full-image or crop Hough line as the pole axis.
- Never solve false positives only by changing one global threshold.

Read `CLAUDE_FULL_STREETSCAN_REVIEW.md` for the full architectural review and acceptance criteria.
