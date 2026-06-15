#!/usr/bin/env bash
# Serve the built codelab on :8080 (Cloud Shell → Web Preview, or forward the port).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ID="game-character-designer"
PORT="${1:-8080}"
[[ -f "$HERE/$ID/index.html" ]] || { echo "Not built yet — run ./build.sh first."; exit 1; }
cd "$HERE/$ID"
echo "Serving on :$PORT  (Ctrl-C to stop)  →  http://localhost:$PORT"
exec python3 -m http.server "$PORT" --bind 0.0.0.0
