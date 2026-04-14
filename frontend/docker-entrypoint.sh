#!/bin/sh
set -e
# Bind mount ./frontend + named volume on /app/node_modules hides image node_modules.
# Fresh volume is empty — install deps before `next` exists.
if [ ! -f node_modules/next/dist/bin/next ]; then
  echo "frontend: node_modules missing or incomplete; running npm ci..."
  npm ci
fi
exec "$@"
