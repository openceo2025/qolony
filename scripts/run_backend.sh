#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"
FRONTEND_DIST_INDEX="$FRONTEND_DIR/dist/index.html"
FORCE_FRONTEND_BUILD="${QOLONY_FORCE_FRONTEND_BUILD:-0}"

if [[ "${QOLONY_SKIP_FRONTEND_BUILD:-0}" != "1" && ( "$FORCE_FRONTEND_BUILD" == "1" || ! -f "$FRONTEND_DIST_INDEX" ) ]]; then
  cd "$FRONTEND_DIR"
  npm install --no-fund --no-audit
  npm run build
fi

cd "$ROOT_DIR/backend"
echo "QOLONY URL: http://localhost:8000/"

# 既定は安定起動。DB更新での再起動ループを避けるため --reload は無効。
if [[ "${QOLONY_RELOAD:-0}" == "1" ]]; then
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --reload-exclude "data/*"
else
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
fi
