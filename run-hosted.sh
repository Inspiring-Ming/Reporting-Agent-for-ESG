#!/usr/bin/env bash
# Start the demo with the password gate + rate limits ON, then expose it via
# ngrok on the reserved custom domain. Use this when sharing externally.
#
# Local-only: use ./run.sh instead (no gate, no limits).

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# Visitor password — change here if you want to rotate it.
export ACCESS_PASSWORD="${ACCESS_PASSWORD:-JasminESG}"

# Custom reserved domain on your ngrok Hobby plan.
NGROK_DOMAIN="${NGROK_DOMAIN:-esg-reporting.ngrok.app}"

PORT="${PORT:-5050}"

if [ ! -d venv ]; then
  echo "Creating venv + installing deps (one-time)..."
  python3 -m venv venv
  venv/bin/pip install --quiet -r requirements.txt
fi

# Start Flask in the background; tear it down on exit.
echo "Starting Flask on :${PORT} with password gate ON…"
PORT="$PORT" venv/bin/python -m app.server &
FLASK_PID=$!
trap "echo; echo 'Stopping…'; kill $FLASK_PID 2>/dev/null; exit 0" INT TERM

# Wait until Flask is actually listening.
for i in {1..15}; do
  if curl -fsS "http://localhost:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo
echo "============================================================"
echo "  Public URL:  https://${NGROK_DOMAIN}"
echo "  Password:    ${ACCESS_PASSWORD}"
echo "  Press Ctrl+C to stop (kills both Flask and ngrok)."
echo "============================================================"
echo

# Run ngrok in the foreground so its UI is visible.
ngrok http "${PORT}" --domain="${NGROK_DOMAIN}"
