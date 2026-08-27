#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
  kill "$backend_pid" "$frontend_pid" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

cd "$project_dir"
.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8000 &
backend_pid=$!

cd "$project_dir/frontend"
npm run dev &
frontend_pid=$!

wait "$backend_pid" "$frontend_pid"

