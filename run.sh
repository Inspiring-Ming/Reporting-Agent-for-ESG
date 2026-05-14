#!/usr/bin/env bash
# Start the AMP AI Expo demo. Run this from the project directory.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

if [ ! -d venv ]; then
  echo "Creating venv + installing deps (one-time)..."
  python3 -m venv venv
  venv/bin/pip install --quiet -r requirements.txt
fi

PORT="${PORT:-5050}"
echo
echo "Open http://localhost:${PORT}"
echo "Ctrl+C to stop"
echo
PORT="$PORT" venv/bin/python -m app.server
