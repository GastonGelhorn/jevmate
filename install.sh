#!/usr/bin/env bash
# Installs jev from this checkout WITHOUT the plugin system: the package to ~/.local/share/jev, the launcher
# to ~/.local/bin/jev, the skill into every agent skills directory that exists. Claude Code users can instead
# `claude plugin marketplace add GastonGelhorn/jevmate && claude plugin install jevmate@gastongelhorn`, which also wires the hooks. Verifies SHA256SUMS first, so what you
# audited is what runs (tools/checksums.sh regenerates the file after a change).
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SRC"

if [ ! -f SHA256SUMS ]; then echo "SHA256SUMS missing; run tools/checksums.sh" >&2; exit 1; fi
if ! shasum -a 256 -c SHA256SUMS --status; then
  echo "refusing to install: a file does not match SHA256SUMS" >&2
  shasum -a 256 -c SHA256SUMS | grep -v ': OK$' >&2 || true
  exit 1
fi

BIN="${HOME}/.local/bin"; LIB="${HOME}/.local/share/jev"
mkdir -p "$BIN" "$LIB"
rm -rf "$LIB/jev" "$LIB/hooks" "$LIB/jev.py" "$LIB/statusline.py" "$LIB/__pycache__"   # older layouts
cp -R jev "$LIB/jev"
find "$LIB/jev" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
install -m 755 bin/jev "$BIN/jev"
python3 -m compileall -q "$LIB/jev" || true
echo "installed  $BIN/jev  ($("$BIN/jev" --version))"
echo "package    $LIB/jev  (import jev; jev scaffold --libpath)"

SKILL="${HOME}/.local/share/jev-skill"; mkdir -p "$SKILL"
cp skills/jev/SKILL.md "$SKILL/SKILL.md"
for d in "$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.codex/skills" "$HOME/.config/opencode/skills"; do
  if [ -d "$d" ]; then ln -sfn "$SKILL" "$d/jev" && echo "skill      $d/jev"; fi
done

case ":$PATH:" in *":$BIN:"*) ;; *) echo "add $BIN to your PATH";; esac
echo "next       jev auth set <key> · jev doctor · jev hooks install · jev statusline install   (skip hooks/statusline if the plugin is installed)"
