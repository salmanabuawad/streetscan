# Ubuntu deployment without Docker

Tested design target: Ubuntu Server 22.04 or 24.04.

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib nginx nodejs npm
```

For a newer Node.js version, use the official NodeSource repository.

## 2. Create PostgreSQL database

```bash
sudo -u postgres psql
```

```sql
CREATE USER streetscan WITH PASSWORD 'REPLACE_WITH_STRONG_PASSWORD';
CREATE DATABASE streetscan OWNER streetscan;
\q
```

## 3. Deploy backend

```bash
sudo mkdir -p /opt/buqata-streetscan
sudo chown -R $USER:$USER /opt/buqata-streetscan
cp -r backend frontend deploy scripts /opt/buqata-streetscan/
cd /opt/buqata-streetscan/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Set:

```text
DATABASE_URL=postgresql+psycopg://streetscan:STRONG_PASSWORD@127.0.0.1:5432/streetscan
CORS_ORIGINS=https://streetscan.buqata.muni.il
```

Install service:

```bash
sudo cp /opt/buqata-streetscan/deploy/systemd/streetscan-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now streetscan-api
```

## 4. Build frontend

```bash
cd /opt/buqata-streetscan/frontend
cp .env.example .env
echo "VITE_API_URL=/api" > .env
npm install
npm run build
sudo mkdir -p /var/www/streetscan
sudo cp -r dist/* /var/www/streetscan/
```

## 5. Nginx

```bash
sudo cp /opt/buqata-streetscan/deploy/nginx/streetscan.conf /etc/nginx/sites-available/streetscan
sudo ln -s /etc/nginx/sites-available/streetscan /etc/nginx/sites-enabled/streetscan
sudo nginx -t
sudo systemctl reload nginx
```

## 6. HTTPS

Use a real DNS name and Certbot:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d streetscan.buqata.muni.il
```

HTTPS is important because mobile browsers normally block camera and high-accuracy geolocation on insecure origins.

## 7. Storage

Create upload directory:

```bash
sudo mkdir -p /opt/buqata-streetscan/backend/uploads
sudo chown -R www-data:www-data /opt/buqata-streetscan/backend/uploads
```

## 8. Production next steps

- Add authentication and role-based access
- Add IndexedDB offline queue
- Add object storage or dedicated volume
- Add AI worker process and Redis/RabbitMQ
- Add Leaflet/OpenLayers GIS map
- Add face and license-plate blurring
- Add retention jobs
- Add backups
