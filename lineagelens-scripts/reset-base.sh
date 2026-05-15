#!/usr/bin/env bash
# LineageLens Base — Clear All Captures
# Removes all locally stored AI capture records from VS Code's global storage.
# Usage: bash reset-base.sh

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
echo -e "${BOLD}LineageLens Base — Clear All Captures${RESET}"
echo ""

# VS Code stores extension data under:
#   Linux/Mac: ~/.vscode/extensions / globalStorage
#   Windows:   %APPDATA%\Code\User\globalStorage
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || -n "${APPDATA:-}" ]]; then
    STORAGE_DIR="${APPDATA}/Code/User/globalStorage/lineagelens.lineagelens-base"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    STORAGE_DIR="$HOME/Library/Application Support/Code/User/globalStorage/lineagelens.lineagelens-base"
else
    STORAGE_DIR="$HOME/.config/Code/User/globalStorage/lineagelens.lineagelens-base"
fi

CAPTURES_FILE="$STORAGE_DIR/captures.json"

if [[ ! -f "$CAPTURES_FILE" ]]; then
    info "No captures file found at: $CAPTURES_FILE"
    info "Nothing to clear."
    exit 0
fi

read -r -p "  Delete all captures at $CAPTURES_FILE? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -f "$CAPTURES_FILE"
    ok "All captures cleared."
    info "Reload VS Code to refresh the sidebar."
else
    info "Cancelled — no changes made."
fi
echo ""
