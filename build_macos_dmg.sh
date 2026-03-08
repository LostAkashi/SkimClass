#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python -m pip install --upgrade pip
python -m pip install pyinstaller

rm -rf build dist

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name SkimClass \
  --recursive-copy-metadata streamlit \
  launcher.py \
  --add-data "app.py:." \
  --add-data "agent.py:." \
  --add-data "auto_capture.py:." \
  --add-data "db.py:." \
  --add-data "paths.py:." \
  --add-data "pages:pages" \
  --add-data "data/recordings:data/recordings" \
  --add-data ".env.example:."

hdiutil create \
  -volname "SkimClass" \
  -srcfolder "dist/SkimClass.app" \
  -ov \
  -format UDZO \
  "dist/SkimClass-macOS.dmg"

echo "Done: dist/SkimClass-macOS.dmg"
