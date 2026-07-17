# StreetScan Local Asset Engine Upgrade

## Implemented

- Fully local OWL-ViT open-vocabulary infrastructure analysis.
- CUDA auto-detection with CPU fallback.
- Prompt batching to reduce RAM spikes on CPU-only servers.
- Stable Transformers post-processing using integer query labels.
- Route analysis jobs with queue, running, completed, and failed states.
- Persistent job progress and candidate counts.
- Clear worker/model-load errors in the Hebrew AI Detection screen.
- Protection against duplicate active jobs for the same route.
- Safe reruns: replaces only non-approved OWL-ViT drafts and preserves approved assets/audit history.
- The route must contain images before analysis can start.
- Production frontend rebuilt successfully.

## Production flow

1. Open **זיהויי AI**.
2. Choose a route with captured images.
3. Click **ניתוח תמונות וחילוץ נכסים**.
4. The systemd worker loads OWL-ViT locally and processes the route asynchronously.
5. Candidate assets appear with image, category, confidence, model, layer, and validation actions.
6. Approving a candidate creates an official GIS asset; rejecting/correcting it creates training feedback.

## Server dependencies

Install the normal backend requirements and the AI requirements. On a CPU server, install CPU PyTorch first as documented in `backend/requirements-ai.txt`. No paid API is used and no municipal image is sent outside the server.

## Validation performed

- All Python backend modules compiled successfully.
- TypeScript compilation succeeded.
- Vite production build succeeded.
