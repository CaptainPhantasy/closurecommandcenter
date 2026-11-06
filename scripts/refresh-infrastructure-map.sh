#!/usr/bin/env bash
# refresh-infrastructure-map.sh
# Re-vendors the latest infrastructure-map.html from an operator-provided bundle.
# into control-center/static/. Run this when the source diagram changes.
#
# Source bundle is regenerated periodically by the architecture documentation
# workflow under the operator's Downloads directory.
#
# Authority: plans/controlboard.md Step 13

set -euo pipefail

SOURCE="${INFRA_MAP_SOURCE:-$HOME/Downloads/Legacy_AI_Delivery_Architecture_Package/network-map/infrastructure-map.html}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${PROJECT_ROOT}/static/infrastructure-map.html"

if [[ ! -f "$SOURCE" ]]; then
    echo "ERROR: source not found at $SOURCE" >&2
    echo "Set INFRA_MAP_SOURCE if the bundle lives elsewhere." >&2
    exit 1
fi

cp "$SOURCE" "$DEST"
echo "[refresh-infrastructure-map] copied $(wc -l <"$DEST") lines, $(wc -c <"$DEST") bytes"
echo "[refresh-infrastructure-map] dest: $DEST"
echo "[refresh-infrastructure-map] commit with: git add static/infrastructure-map.html && git commit -m 'chore: re-vendor infrastructure-map.html'"

