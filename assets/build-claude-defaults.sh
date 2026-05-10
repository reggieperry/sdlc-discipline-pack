#!/bin/sh
# build-claude-defaults.sh — build the .claude/ bootstrap tarball.
#
# Output: assets/claude-defaults.tar.gz
#
# Tarball contents (when extracted with `tar -xzf` from a rig's repo root):
#   .claude/
#   ├── rules/           (the 9 discipline rule files)
#   ├── settings.json    (portable hooks + permissions)
#   └── conditional_docs/
#       └── README.md    (convention starter)
#
# Operator usage:
#   cd <rig>
#   tar -xzf <pack-cache>/assets/claude-defaults.tar.gz
#
# Build:
#   cd <pack repo>
#   bash assets/build-claude-defaults.sh

set -eu

PACK_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$PACK_ROOT/assets"
OUT="$ASSETS/claude-defaults.tar.gz"
STAGE=$(mktemp -d)

mkdir -p "$STAGE/.claude/rules" "$STAGE/.claude/conditional_docs"
cp "$ASSETS/rules"/*.md "$STAGE/.claude/rules/"
cp "$ASSETS/settings.json" "$STAGE/.claude/settings.json"
cp "$ASSETS/conditional_docs-README.md" "$STAGE/.claude/conditional_docs/README.md"

tar -czf "$OUT" -C "$STAGE" .claude

rm -rf "$STAGE"

echo "Built: $OUT"
echo "Size:  $(du -h "$OUT" | cut -f1)"
echo
echo "To install into a rig:"
echo "  cd <rig>"
echo "  tar -xzf $OUT"
