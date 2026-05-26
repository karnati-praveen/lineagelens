#!/usr/bin/env bash
# LineageLens Base — VS Code Extension Installer
# Installs the base extension from a pre-built .vsix or the marketplace.
#
# Usage: bash quickstart-base.sh [path/to/lineagelens-base-x.x.x.vsix]

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $*"; }
info() { echo -e "  ${YELLOW}→${RESET}  $*"; }
die()  { echo -e "\n  ${RED}✗  ERROR:${RESET} $*\n" >&2; exit 1; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   LineageLens Base  —  VS Code Extension ║${RESET}"
echo -e "${BOLD}║   No server · No Docker · Zero setup     ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"
echo ""

# Locate the code CLI
if command -v code >/dev/null 2>&1; then
    CODE=code
elif command -v code-insiders >/dev/null 2>&1; then
    CODE=code-insiders
else
    echo -e "  ${YELLOW}VS Code CLI not found on PATH.${RESET}"
    echo ""
    echo -e "  Install manually:"
    echo -e "  1. Open VS Code"
    echo -e "  2. Press ${BOLD}Ctrl+P${RESET} (or Cmd+P on Mac)"
    echo -e "  3. Type: ${CYAN}ext install karnatipraveen.lineagelens${RESET}"
    echo ""
    exit 0
fi

VSIX="${1:-}"

if [[ -n "$VSIX" ]]; then
    # Install from local .vsix file
    if [[ ! -f "$VSIX" ]]; then
        die "File not found: $VSIX"
    fi
    info "Installing from local file: $VSIX"
    $CODE --install-extension "$VSIX"
    ok "Extension installed from VSIX."
else
    # Install from marketplace
    info "Installing lineagelens-base from VS Code Marketplace..."
    $CODE --install-extension karnatipraveen.lineagelens
    ok "Extension installed from Marketplace."
fi

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   LineageLens Base is ready!             ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Reload VS Code to activate the extension."
echo ""
echo -e "  ${BOLD}What it does:${RESET}"
echo -e "  • Detects AI-generated code insertions in real time"
echo -e "  • Stores captures locally — no account, no server"
echo -e "  • View history in the LineageLens sidebar"
echo ""
echo -e "  ${BOLD}Want team features, search, and risk scoring?${RESET}"
echo -e "  Run ${CYAN}bash quickstart-lite.sh${RESET} to start the full backend."
echo ""
