#!/usr/bin/env bash
# Train the asset detector with maximum CPU by briefly pausing NON-critical
# neighbor apps, then GUARANTEEING they restart (trap runs on any exit, incl.
# error / Ctrl-C / kill). Usage:  bash train-with-pause.sh [epochs]
#
# NEVER paused (protected): postgres, nginx, mysql, redis, the two live
# flagship apps (advancedparking = parking.kortexd.com, helpdesk =
# support.kortexd.com), and streetscan itself.
#
# Review PAUSE_SERVICES before relying on this — each one is briefly down
# (~a few minutes) during training. Remove any you must keep online.
set -u

PAUSE_SERVICES=(
  dwg-engine-backend
  academic-feedback-api
  trail-api
  hermon-cherries
  solarica-backend
  solarica2-backend
  inv-backend
  wavelync-ftp
)

EPOCHS="${1:-40}"
DEPLOY=/opt/buqata-streetscan/backend

restart_all() {
  echo "== restarting paused services =="
  for s in "${PAUSE_SERVICES[@]}"; do
    systemctl start "$s" 2>/dev/null && echo "  started $s" || echo "  (skip $s)"
  done
}
trap restart_all EXIT   # always bring them back, whatever happens

echo "== pausing non-critical neighbors =="
for s in "${PAUSE_SERVICES[@]}"; do
  if systemctl is-active --quiet "$s"; then
    systemctl stop "$s" && echo "  paused $s"
  else
    echo "  (already stopped $s)"
  fi
done

echo "== training ($EPOCHS epochs) =="
cd "$DEPLOY"
# lowest CPU + IO priority so even the protected apps stay responsive
sudo -u streetscan nice -n 19 ionice -c3 .venv/bin/python -m app.train_model "$EPOCHS"
# trap restarts everything on exit
