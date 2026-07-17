#!/usr/bin/env bash
# Idempotent server setup for scan.kortexd.com.
# Expects the repo staged at /tmp/streetscan-stage (backend, frontend/dist, deploy, scripts).
set -euo pipefail

DEPLOY_ROOT=/opt/buqata-streetscan
APP_USER=streetscan
DB_NAME=streetscan
DB_USER=streetscan
DOMAIN=scan.kortexd.com
STAGE=/tmp/streetscan-stage

echo "== system user =="
id -u $APP_USER &>/dev/null || useradd --system --home $DEPLOY_ROOT --shell /usr/sbin/nologin $APP_USER

echo "== copy files (preserving .env, venv, uploads) =="
mkdir -p $DEPLOY_ROOT
if [ -f $DEPLOY_ROOT/backend/.env ]; then cp $DEPLOY_ROOT/backend/.env /tmp/streetscan.env.bak; fi
cp -r $STAGE/backend $STAGE/deploy $STAGE/scripts $DEPLOY_ROOT/
mkdir -p $DEPLOY_ROOT/frontend
rm -rf $DEPLOY_ROOT/frontend/dist
cp -r $STAGE/frontend/dist $DEPLOY_ROOT/frontend/
if [ -f /tmp/streetscan.env.bak ]; then mv /tmp/streetscan.env.bak $DEPLOY_ROOT/backend/.env; fi

echo "== python venv =="
cd $DEPLOY_ROOT/backend
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

echo "== ai worker deps =="
# opencv needs these even in a headless install path (ultralytics pulls opencv-python)
apt-get install -y -q libgl1 libglib2.0-0 >/dev/null
# Tesseract OCR engine + Hebrew/Arabic/English language packs (business signs)
apt-get install -y -q tesseract-ocr tesseract-ocr-heb tesseract-ocr-ara tesseract-ocr-eng >/dev/null
if ! .venv/bin/python -c "import ultralytics" 2>/dev/null; then
    .venv/bin/pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
    .venv/bin/pip install -q -r requirements-ai.txt
else
    echo "ultralytics already installed"
fi

echo "== postgres =="
ROLE_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" || true)
DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" || true)
if [ ! -f $DEPLOY_ROOT/backend/.env ]; then
    DB_PASS=$(openssl rand -hex 24)
    if [ "$ROLE_EXISTS" = "1" ]; then
        sudo -u postgres psql -c "ALTER ROLE $DB_USER WITH PASSWORD '$DB_PASS'"
    else
        sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS'"
    fi
    [ "$DB_EXISTS" = "1" ] || sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"
    cat > $DEPLOY_ROOT/backend/.env <<EOF
APP_NAME=Buqata StreetScan
API_PREFIX=/api
DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASS@127.0.0.1:5432/$DB_NAME
UPLOAD_DIR=$DEPLOY_ROOT/backend/uploads
CORS_ORIGINS=https://$DOMAIN
EOF
    chmod 600 $DEPLOY_ROOT/backend/.env
else
    echo "existing .env kept"
    [ "$DB_EXISTS" = "1" ] || sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"
fi
# JWT secret (append once; never rotate silently — it would log everyone out)
grep -q '^JWT_SECRET=' $DEPLOY_ROOT/backend/.env || \
    echo "JWT_SECRET=$(openssl rand -hex 32)" >> $DEPLOY_ROOT/backend/.env

echo "== schema migrations =="
# create_all only creates missing tables; columns added later need ALTERs here.
sudo -u postgres psql $DB_NAME -c "ALTER TABLE IF EXISTS video_segments ADD COLUMN IF NOT EXISTS orientation_hint INTEGER NOT NULL DEFAULT 0"
sudo -u postgres psql $DB_NAME -c "ALTER TABLE IF EXISTS gps_points ADD COLUMN IF NOT EXISTS heading_deg DOUBLE PRECISION"
sudo -u postgres psql $DB_NAME -c "ALTER TABLE IF EXISTS captured_images ADD COLUMN IF NOT EXISTS ocr_processed BOOLEAN NOT NULL DEFAULT FALSE"
sudo -u postgres psql $DB_NAME -c "ALTER TABLE IF EXISTS video_segments ADD COLUMN IF NOT EXISTS ocr_processed BOOLEAN NOT NULL DEFAULT FALSE"
for col in bbox_cx bbox_cy bbox_w bbox_h; do
  sudo -u postgres psql $DB_NAME -c "ALTER TABLE IF EXISTS training_samples ADD COLUMN IF NOT EXISTS $col DOUBLE PRECISION" 2>/dev/null || true
done
sudo -u postgres psql $DB_NAME -c "ALTER TABLE IF EXISTS detections ADD COLUMN IF NOT EXISTS image_id INTEGER REFERENCES captured_images(id)" 2>/dev/null || true

echo "== permissions =="
mkdir -p $DEPLOY_ROOT/backend/uploads
chown -R $APP_USER:$APP_USER $DEPLOY_ROOT

echo "== systemd =="
cp $DEPLOY_ROOT/deploy/systemd/streetscan-api.service /etc/systemd/system/
cp $DEPLOY_ROOT/deploy/systemd/streetscan-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable streetscan-api streetscan-worker
systemctl restart streetscan-api
systemctl restart streetscan-worker

echo "== nginx + tls =="
if [ ! -d /etc/letsencrypt/live/$DOMAIN ]; then
    # First run: bootstrap on port 80 only, obtain the certificate, then
    # install the canonical TLS vhost (never leave certbot's edits in place —
    # the next deploy would overwrite them).
    cp $DEPLOY_ROOT/deploy/nginx/streetscan.conf /etc/nginx/sites-available/$DOMAIN
    ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
    nginx -t && systemctl reload nginx
    certbot --nginx -d $DOMAIN --non-interactive --agree-tos -m salman.abuawad@gmail.com --redirect
fi
cp $DEPLOY_ROOT/deploy/nginx/streetscan-tls.conf /etc/nginx/sites-available/$DOMAIN
ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN
nginx -t
systemctl reload nginx

echo "== bootstrap admin =="
cd $DEPLOY_ROOT/backend
sudo -u $APP_USER .venv/bin/python -m app.bootstrap_admin
# detections.image_id may not have existed before create_all made captured_images
sudo -u postgres psql $DB_NAME -c "ALTER TABLE IF EXISTS detections ADD COLUMN IF NOT EXISTS image_id INTEGER REFERENCES captured_images(id)"

echo "== health check =="
sleep 2
systemctl is-active streetscan-api
systemctl is-active streetscan-worker
curl -sf http://127.0.0.1:8005/api/health && echo
echo "== done =="
