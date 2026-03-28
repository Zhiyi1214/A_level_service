#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT="$SCRIPT_DIR/selfsigned.crt"
KEY="$SCRIPT_DIR/selfsigned.key"

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
    echo "Certificates already exist: $CERT / $KEY"
    echo "Delete them and re-run if you want to regenerate."
    exit 0
fi

openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "$KEY" \
    -out "$CERT" \
    -subj "/C=CN/ST=Local/L=Local/O=AI-Assistant/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "$KEY"
chmod 644 "$CERT"

echo "Self-signed certificate generated:"
echo "  cert: $CERT"
echo "  key:  $KEY"
echo ""
echo "For production, replace these with real certificates (Let's Encrypt, etc.)."
