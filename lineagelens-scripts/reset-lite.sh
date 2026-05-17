#!/usr/bin/env bash
# LineageLens Lite — Clear All Data
# Removes the SQLite database so the setup wizard runs again on next boot.
# Usage: bash reset-lite.sh

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $*"; }
info() { echo -e "  ${YELLOW}→${RESET}  $*"; }
die()  { echo -e "\n  ${RED}✗  ERROR:${RESET} $*\n" >&2; exit 1; }

echo ""
echo -e "${BOLD}LineageLens Lite — Clear All Data${RESET}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/lineagelens-deploy/data"
DB_FILE="$DATA_DIR/lineagelens.db"

if [[ ! -f "$DB_FILE" ]]; then
    info "No database found at: $DB_FILE"
    info "Nothing to clear."
    exit 0
fi

read -r -p "  Delete database at $DB_FILE? All data will be lost. [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    docker compose --project-name lineagelens -f "$SCRIPT_DIR/lineagelens-deploy/docker-compose.lite.yml" down 2>/dev/null || true
    rm -f "$DB_FILE"
    ok "Database cleared."
    info "Run bash quickstart.sh to start fresh."
else
    info "Cancelled — no changes made."
fi
echo ""
