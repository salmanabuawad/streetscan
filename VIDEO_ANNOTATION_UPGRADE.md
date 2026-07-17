# Video Annotation and Custom Asset Types

## Added

- Open any uploaded video segment from the **Video** tab.
- Play, pause, seek, and step one second backward or forward.
- Freeze the current frame and draw a YOLO-format bounding box around an asset.
- Save the extracted server-side frame as a durable training sample.
- Store the exact video segment ID and timestamp in the sample metadata.
- Select an existing asset type or enter a new free-text machine type, such as `solar_panel`.
- Enter a free-text display name/description for every asset.
- Select the infrastructure layer for new types.
- Use custom types in image upload and captured-street-image annotation as well.

## API

`POST /video-segments/{segment_id}/annotate`

JSON body:

```json
{
  "timestamp_s": 4.25,
  "asset_type": "solar_panel",
  "asset_name": "Solar panel above municipal building",
  "layer": "electricity",
  "bbox_cx": 0.55,
  "bbox_cy": 0.42,
  "bbox_w": 0.18,
  "bbox_h": 0.22
}
```

The backend extracts the selected frame with OpenCV, applies the recorded orientation hint, saves a high-quality JPEG under `uploads/training/video_frames`, and creates a `training_samples` record.

## Deployment note

OpenCV must be installed on the backend. It is already part of the AI requirements. Rebuild/copy the frontend `dist` directory and restart the API service after deployment.
