# Buqata StreetScan Pilot

פיילוט למיפוי נכסי תשתית ומפגעים באמצעות טלפון חכם המותקן על רכב מועצה.

## Stack

- Frontend: React + Vite + TypeScript
- Backend: FastAPI + SQLAlchemy
- Database: PostgreSQL
- Deployment: Ubuntu + Nginx + systemd
- No Docker

## Infrastructure layers

- Telecommunications / telephones
- Electricity
- Water
- Sewage
- Drainage
- Tunnels / conduits / crossings
- Roads and public-space assets

Visible assets can be captured from video and GPS. Underground infrastructure is managed as GIS layers imported from engineering data, surveys, or field verification.

## Main pilot capabilities

- Start and stop a route from a mobile web app
- Capture GPS points
- Upload short video clips during the route
- Offline queue in the browser
- Asset inventory by infrastructure layer
- Draft detections and human validation
- Draft support tickets linked to assets and coordinates
- GeoJSON-ready API
- Dashboard and map placeholder
- Ubuntu deployment scripts

## Quick start

### PostgreSQL

```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
sudo -u postgres psql
```

```sql
CREATE USER streetscan WITH PASSWORD 'change_me';
CREATE DATABASE streetscan OWNER streetscan;
\q
```

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# tables are created automatically on first startup (Base.metadata.create_all)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev -- --host 0.0.0.0
```

Open from a phone on the same network:

```text
https://SERVER_IP:5173
```

Note the **https** — the dev server uses a self-signed certificate (via
`@vitejs/plugin-basic-ssl`) because mobile browsers block the camera and
high-accuracy geolocation on insecure origins. Accept the certificate warning
once on the phone. API calls go through the Vite `/api` proxy to the backend
on port 8000. For production use a real certificate (see the Ubuntu guide).

## Ubuntu deployment

See `deploy/UBUNTU_DEPLOYMENT.md`.

## Important pilot limitations

1. Phone GPS is suitable for approximate asset positioning, not cadastral or engineering-grade coordinates.
2. Underground pipes, sewage lines, telephone ducts and tunnels cannot be inferred reliably from street video alone.
3. AI detections should remain drafts until validated by a municipal employee.
4. Faces and license plates should be blurred before long-term retention.
