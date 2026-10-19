#!/bin/bash
set -e
if ! python3 -c "import flask, requests, openpyxl, reportlab" 2>/dev/null; then
    apt-get update && apt-get install -y --no-install-recommends python3-flask python3-requests python3-openpyxl python3-reportlab python3-pil || pip install --no-cache-dir flask requests openpyxl reportlab pillow || true
fi
exec python3 -u app.py
