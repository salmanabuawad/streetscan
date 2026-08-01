# Immediate Safety Fixes Applied

- Raised production AI observation floor to 0.50.
- Raised leaning-pole floor to 0.65.
- Prevented AI detections from creating `OPEN` hazards.
- Replaced longest-Hough-line tilt estimation with a conservative parallel-edge midpoint method.
- Required an independent scene-vertical reference and geometry validation.
- Invalid tilt geometry now produces no angle.
- `base_visible` is no longer inferred from bounding-box position.
- Replaced maximum-angle aggregation with median aggregation.
- Added stable multi-frame escalation rule: at least five valid observations and <=2° standard deviation.
- Added regression tests for cable interference, oversized boxes and base visibility.
- Added `CLAUDE_INSTRUCTIONS.md` for the next implementation phase.

## Validation

- Python compilation: passed.
- Tilt regression tests: 3 passed.
- Frontend TypeScript compilation started successfully, but Vite bundling could not run in this container because the uploaded `node_modules` is missing Rollup's Linux optional native package. Reinstall frontend dependencies on the target machine before building.
