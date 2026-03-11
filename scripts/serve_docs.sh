#!/usr/bin/env bash
# Serve MkDocs locally for development.
# Usage: ./scripts/serve_docs.sh [--port PORT]
set -euo pipefail

cd "$(dirname "$0")/../docs"

exec uv run --group docs mkdocs serve --strict "$@"
