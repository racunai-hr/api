#!/usr/bin/env bash
# Fail if committed openapi.yaml is stale or invalid.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

cd "$ROOT/app"
python manage.py spectacular \
  --file "$TMP" \
  --validate \
  --fail-on-warn

if ! diff -u "$ROOT/openapi.yaml" "$TMP"; then
  echo "openapi.yaml is stale. Run scripts/generate_openapi.sh and commit the artifact." >&2
  exit 1
fi
echo "openapi.yaml is in sync with API code."
