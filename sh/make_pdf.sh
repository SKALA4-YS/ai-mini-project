#!/usr/bin/env bash
# 발표 자료 doc/presentation/slides.html → PDF (Chrome headless)
set -euo pipefail
cd "$(dirname "$0")/../doc/presentation"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$PWD/AI_Mini_Project_발표.pdf" "file://$PWD/slides.html"
echo "→ doc/presentation/AI_Mini_Project_발표.pdf"
