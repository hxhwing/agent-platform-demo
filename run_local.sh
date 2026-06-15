#!/usr/bin/env bash
# Launch the full local demo from the scaffolded (agents-cli) projects:
#   - Localization Studio (A2A "external" org)  → uvicorn on :8000
#   - Game Producer        (ADK Web UI)          → adk web on :8080
# Each project has its own uv venv; we run inside them with `uv run`.
# Usage:  ./run_local.sh        (Ctrl-C stops both)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "▶ Starting Localization Studio (A2A server) on :8000 ..."
( cd "$ROOT/localization-studio" && \
  uv run uvicorn app.fast_api_app:app --host 0.0.0.0 --port 8000 ) &
LOC_PID=$!

# The scaffolded A2A server publishes its card under /a2a/app/.well-known/...
LOC_CARD="http://localhost:8000/a2a/app/.well-known/agent-card.json"
for i in $(seq 1 30); do
  if curl -sf "$LOC_CARD" >/dev/null 2>&1; then
    echo "  ✓ Agent Card live: $LOC_CARD"; break
  fi; sleep 1
done

echo "▶ Starting Game Producer (ADK Web UI) on :8080 ..."
# game-producer's agent package is `app/` → it shows up as "app" in the dropdown.
# Local dev uses ADK's default in-memory sessions + memory. (Managed Sessions +
# Memory Bank are exercised by the DEPLOYED agents — Agent Runtime / Cloud Run.)
( cd "$ROOT/game-producer" && uv run adk web --host 0.0.0.0 --port 8080 . ) &
WEB_PID=$!

trap 'echo; echo "Stopping..."; kill $LOC_PID $WEB_PID 2>/dev/null || true' INT TERM EXIT

echo
echo "================================================================"
echo "  ADK Web UI   → http://localhost:8080   (select agent: app)"
echo "  A2A card     → $LOC_CARD"
echo "  Sample sketch→ assets/sample_sketch.png"
echo "================================================================"
wait
