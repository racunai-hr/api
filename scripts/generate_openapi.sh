#!/usr/bin/env bash
# Generate api/openapi.yaml from Django code. Do not edit the YAML by hand.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/openapi.yaml"
TMP="$(mktemp)"

generate_in_container() {
  docker exec finestar_erp python manage.py spectacular \
    --file /tmp/openapi.yaml \
    --validate \
    --fail-on-warn
  docker cp finestar_erp:/tmp/openapi.yaml "$TMP"
}

generate_local() {
  (cd "$ROOT/app" && python manage.py spectacular \
    --file "$TMP" \
    --validate \
    --fail-on-warn)
}

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'finestar_erp'; then
  generate_in_container
elif command -v python >/dev/null && [ -f "$ROOT/app/manage.py" ]; then
  generate_local
else
  echo "Need running finestar_erp container or local manage.py" >&2
  exit 1
fi

mv "$TMP" "$OUT"
echo "Wrote $OUT"
