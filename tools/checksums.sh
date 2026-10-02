#!/usr/bin/env bash
# Regenerates SHA256SUMS over everything install.sh copies. Run after any change, before committing.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
{ find jev skills agents -type f \( -name '*.py' -o -name '*.md' \) ! -path '*/__pycache__/*' | sort; echo bin/jev; echo hooks/hooks.json; echo .mcp.json; echo .claude-plugin/plugin.json; echo .claude-plugin/marketplace.json; } | xargs shasum -a 256 > SHA256SUMS
echo "$(wc -l < SHA256SUMS | tr -d ' ') files in SHA256SUMS"
