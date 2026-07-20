#!/usr/bin/env bash
# Cross-note: final EXE should be built on Windows. This script is a Linux/mac check build.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m pip install -r requirements.txt pyinstaller
rm -rf dist build
pyinstaller \
  --noconfirm --clean \
  --name scan2csv \
  --onedir --console \
  --add-data "scan2csv/web:scan2csv/web" \
  --add-data "scan2csv/prompts:scan2csv/prompts" \
  --collect-all uvicorn \
  --collect-all fastapi \
  --hidden-import scan2csv.server \
  --hidden-import scan2csv.pipeline.run \
  -m scan2csv
echo "Output under dist/scan2csv/"
