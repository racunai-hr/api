#!/usr/bin/env bash
# Instalira OTP Sandbox certifikate (TPP) iz ClientCertificate ZIP-a u ERP deploy.
#
# Portal (ručno, prije ovog skripta):
#   1. TPP → Certifikat generator → generiraj auth + signature certove
#   2. Import AuthenticationCertificate.crt + SignatureCertificate.crt u portal
#   3. Application → PSD-SANDBOX-ID<TPP> → Enabled + redirect URI
#   4. Preuzmi ZIP (ClientCertificate<TPP>.zip)
#
# Upotreba:
#   ./scripts/otp_install_sandbox_tpp.sh 166 /path/to/ClientCertificate166.zip <client_secret>
#
set -euo pipefail

TPP_ID="${1:-}"
ZIP_PATH="${2:-}"
CLIENT_SECRET="${3:-}"

if [[ -z "$TPP_ID" || -z "$ZIP_PATH" ]]; then
  echo "Usage: $0 <tpp_id> <ClientCertificate.zip> [client_secret]" >&2
  exit 1
fi

EXPECTED_ORG="PSD-SANDBOX-ID${TPP_ID}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STACK_ROOT="$(cd "$API_ROOT/.." && pwd)"
CERT_DIR="$STACK_ROOT/.temp/otp-certs-id${TPP_ID}"
OTP_MOUNT="$STACK_ROOT/.certificates/otp"
ENV_FILE="$STACK_ROOT/.env"

if [[ ! -f "$ZIP_PATH" ]]; then
  echo "ZIP not found: $ZIP_PATH" >&2
  exit 1
fi

mkdir -p "$CERT_DIR" "$OTP_MOUNT"
rm -rf "$CERT_DIR"/*
python3 - "$ZIP_PATH" "$CERT_DIR" <<'PY'
import sys, zipfile
zip_path, out_dir = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(zip_path) as zf:
    zf.extractall(out_dir)
    for name in zf.namelist():
        print(name)
PY

AUTH_CRT="$CERT_DIR/AuthenticationCertificate.crt"
SIG_CRT="$CERT_DIR/SignatureCertificate.crt"
AUTH_PFX="$CERT_DIR/AuthenticationCertificatePfx.pfx"
SIG_PFX="$CERT_DIR/SignatureCertificatePfx.pfx"
PFX_PASS_FILE="$CERT_DIR/PfxPassword.txt"

for required in "$AUTH_CRT" "$SIG_CRT" "$AUTH_PFX" "$SIG_PFX" "$PFX_PASS_FILE"; do
  if [[ ! -f "$required" ]]; then
    echo "Missing in ZIP: $(basename "$required")" >&2
    exit 1
  fi
done

ORG_ID="$(openssl x509 -in "$AUTH_CRT" -noout -subject 2>/dev/null | sed -n 's/.*organizationIdentifier = \([^,]*\).*/\1/p')"
if [[ "$ORG_ID" != "$EXPECTED_ORG" ]]; then
  echo "Auth cert org ID mismatch: got '$ORG_ID', expected '$EXPECTED_ORG'" >&2
  exit 1
fi

PFX_PASS="$(tr -d '\r\n' < "$PFX_PASS_FILE")"
cp "$AUTH_PFX" "$OTP_MOUNT/client.p12"
cp "$SIG_PFX" "$OTP_MOUNT/signature.p12"
chmod 600 "$OTP_MOUNT/client.p12" "$OTP_MOUNT/signature.p12"

echo "Certificates installed:"
openssl x509 -in "$AUTH_CRT" -noout -subject -serial -dates
openssl x509 -in "$SIG_CRT" -noout -serial -dates

update_env() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    echo "${key}=${value}" >> "$ENV_FILE"
  fi
}

update_env OTP_CLIENT_ID "$EXPECTED_ORG"
update_env OTP_CERT_PASSWORD "$PFX_PASS"
update_env OTP_SIGNATURE_CERT_PASSWORD "$PFX_PASS"
update_env OTP_CERT_PATH "/run/secrets/otp-cert/client.p12"
update_env OTP_SIGNATURE_CERT_PATH "/run/secrets/otp-cert/signature.p12"

if [[ -n "$CLIENT_SECRET" ]]; then
  update_env OTP_CLIENT_SECRET "$CLIENT_SECRET"
else
  echo "WARN: OTP_CLIENT_SECRET not provided — set it in $ENV_FILE after creating Application in portal." >&2
fi

echo ""
echo "Done. Next:"
echo "  1. Set OTP_CLIENT_SECRET in $ENV_FILE (if not passed)"
echo "  2. cd $STACK_ROOT && docker compose restart django celery-worker"
echo "  3. docker compose exec django python manage.py otp_healthcheck"
