# StreetScan Detection Engines — Architecture

StreetScan turns a phone/dashcam on a municipal vehicle into two independent
detection products. They share one foundation (model adapters + config-driven
categories + one loaded model) but answer different questions and are wired,
tuned, deployed and reasoned about **separately**.

```
                       ┌─────────────────────────────────────────┐
   frames / GPS  ─────▶│         Shared foundation                │
   (Route,            │  model-adapter interface (engine/base.py) │
    CapturedImage,    │  config categories (DB) · model registry  │
    VideoSegment)     │  one OWL-ViT model, shared across engines │
                       └───────────────┬───────────────┬──────────┘
                                       │               │
                      ┌────────────────▼──┐    ┌────────▼────────────────┐
                      │ ASSET DETECTION    │    │ HAZARD DETECTION        │
                      │ ENGINE             │    │ ENGINE                  │
                      │  sub-engines:      │    │  detectors: OWL-ViT     │
                      │   electricity      │    │  analyzers: tilt,       │
                      │   communication    │    │   image-quality         │
                      │   water            │    │  lifecycle service      │
                      │   sewage/drainage  │    │  (dedup/severity/status)│
                      │   road             │    └────────┬────────────────┘
                      │   public_space     │             │
                      └─────────┬──────────┘             │
                                ▼                        ▼
                        Assets → GIS map          Hazards → review, map,
                        (+ ITM export to           department routing,
                         Maale Hermon GIS)         lifecycle tracking
```

## 1. Asset Detection Engine

**Question:** *where is the municipal infrastructure, and what/where does it map
to in GIS?* Output → `assets` (poles, cabinets, lighting, manholes…), exported
to the official Maale Hermon GIS schema in ITM (see `gis_export.py`).

### Sub-engines (by infrastructure domain)

The Asset Engine is **not a monolith** — it is a set of domain sub-engines, each
scoped to an `infrastructure_layer`, each with its own categories/prompts and (in
future) its own model. Registry: `app/engine/domains.py` → `ASSET_SUBENGINES`.

| Sub-engine | layer(s) | example categories |
|---|---|---|
| electricity (חשמל) | `electricity` | electricity_pole, transformer, switchgear |
| communication (תקשורת) | `telecom` | telecom_pole, telecom_cabinet, junction_box |
| water (מים) | `water` | hydrant, water_valve, water_meter |
| sewage/drainage (ביוב וניקוז) | `sewage`, `drainage` | manhole, storm_drain |
| road (כבישים ותמרור) | `road` | sign, guard_rail |
| public_space (מרחב ציבורי) | `public_space` | bench, bus_station, bin |

Adding or splitting a domain is **configuration** (AssetCategory rows), not code.
`domains.build_asset_subengine(db, domain, detector)` composes the engine scoped
to one domain, sharing a detector instance (one model, scoped prompts).

Detectors today: a trained YOLO municipal model (`buqata_v1`, continuous) plus an
OWL-ViT open-vocabulary adapter (on-demand, for categories YOLO can't see).

## 2. Hazard Detection Engine

**Question:** *is there a hazard in the road / public space that needs action?*
Output → `hazards` with a full lifecycle (see `models/hazards.py`,
`hazard_service.py`, `api/hazards_routes.py`).

Components:
- **Detectors** — OWL-ViT zero-shot over `hazard_categories` prompts (potholes,
  garbage, manholes, bins, signs, debris, …). Shares the *same loaded OWL-ViT
  model* as the Asset Engine's open-vocab detector (one model, two prompt sets).
- **Analyzers** — capabilities beyond object detection:
  - `tilt.py` — leaning-pole tilt vs the scene's own vertical structures
    (perspective-robust; excludes the measured pole from the baseline).
  - `assess_image_quality` — gates night / blur / glare frames.
- **Lifecycle service** (`hazard_service.ingest_observation`) — position estimate
  (camera offset from vehicle GPS), severity, per-category dedup, status
  transitions (suspected → open → in_progress → likely_fixed → closed / reopened).

### Cross-engine flow: leaning poles

The leaning-pole feature deliberately spans both engines: the **Asset Engine's
electricity/communication sub-engine** detects the pole; the **Hazard Engine's
tilt analyzer** judges the lean and, if it exceeds the per-type threshold, opens
a `leaning_pole` hazard (status `suspected`, never auto-declared dangerous from
one frame — field inspection required).

## 3. Shared foundation

- **Model-adapter interface** (`engine/base.py`) — `AssetAnalysisEngine` ABC +
  adapter Protocols (`ObjectDetector`, `OCREngine`, `SegmentationEngine`,
  `EmbeddingEngine`, `ConditionEngine`). Any detector implementing the Protocol
  drops in without touching engine code.
- **Config-driven categories** — `asset_categories` / `hazard_categories` rows
  carry prompts, thresholds, active detector, department, dedup radius, tilt
  thresholds. Behaviour changes are DB edits, not deploys.
- **Model registry** — `model_versions` records each detector (type, file, version,
  supported categories, metrics, active flag) for activation / rollback.
- **One model in memory** — the ~1GB OWL-ViT model is loaded once and shared
  across the asset open-vocab pass and the hazard pass; unloaded when both queues
  drain (memory-safe on the shared 4GB box).

## 4. Pluggable engines (predefined interface, dynamic upload — roadmap)

The goal is to **add new engines/sub-engines without core changes**. The contract
that makes this possible already exists:

1. **Implement the detector Protocol** (`engine/base.py`) — a class exposing
   `name`, `version`, and `detect(image, categories) -> list[DetectedAsset]`.
2. **Declare its categories** — insert `asset_categories` / `hazard_categories`
   rows (prompts, thresholds, `active_detector` = the new detector's key, layer /
   department). This is what routes frames to the new engine.
3. **Register the model** — a `model_versions` row (type, file_path, version,
   supported_categories); flip `active` to promote or roll back.

**Today:** a new engine is added by dropping in an adapter + seeding its config +
registering the model. **Next:** a dynamic loader that accepts an uploaded engine
package (adapter module + weights + category manifest) at runtime, validates it
against the Protocol, and registers it — so new municipal domains (e.g. a trained
pothole model, a water-leak detector) can be onboarded operationally without a
redeploy. The interface above is the stable contract that loader will target.

## Where things live

| Concern | File |
|---|---|
| Engine interface / adapters | `backend/app/engine/base.py`, `engine/adapters/` |
| Engine composition | `backend/app/engine/pipeline.py` |
| Asset sub-engine registry | `backend/app/engine/domains.py` |
| Worker orchestration (asset + hazard passes) | `backend/app/worker.py` |
| Hazard lifecycle | `backend/app/hazard_service.py`, `models/hazards.py` |
| Tilt analyzer | `backend/app/tilt.py` |
| Category config seeds | `seed_categories.py`, `seed_hazards.py` |
| GIS export (asset output) | `backend/app/gis_export.py` |
