#!/usr/bin/env bash
# Builds dist/kb-serve/ (the engine Graeae bundles). Run from the repo root on the target OS.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
uv run pyinstaller packaging/kb-serve.spec --noconfirm --distpath dist --workpath build/pyinstaller
echo "built: dist/kb-serve/kb-serve"
