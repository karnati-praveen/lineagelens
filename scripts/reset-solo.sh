#!/usr/bin/env bash
# LineageLens Solo — Clean Reset
# Destroys ALL containers, volumes, and data for a completely fresh start.
# Usage: bash reset-solo.sh

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/deploy"
ENV_FILE="$DEPLOY_DIR/.env"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.solo.yml"
PROJECT_NAME="lineagelens-solo"

step() { echo -e "\n${BOLD}${CYAN}[$1]${RESET} ${BOLD}$2${RESET}"; }
ok()   { echo -e "  ${GREEN}✓${RESET}  $*"; }
info() { echo -e "  ${YELLOW}→${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}!${RESET}  $*"; }

echo ""
echo -e "${BOLD}${RED}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${RED}║   LineageLens Solo  —  Clean Reset       ║${RESET}"
echo -e "${BOLD}${RED}║   ALL containers, volumes and data       ║${RESET}"
echo -e "${BOLD}${RED}║   will be permanently deleted.           ║${RESET}"
echo -e "${BOLD}${RED}╚══════════════════════════════════════════╝${RESET}"
echo ""

command -v docker >/dev/null 2>&1 || { echo "Docker not found." >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "Docker Compose not found." >&2; exit 1
fi

# ── 1. Stop and remove containers ─────────────────────────────────────────────
step "1/3" "Stopping and removing containers"

echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE down --remove-orphans"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null || true
ok "Containers removed."

# ── 2. Remove volumes ─────────────────────────────────────────────────────────
step "2/3" "Removing volumes and all data"

echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE down -v --remove-orphans"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true

LEFTOVER=$(docker volume ls --filter "name=${PROJECT_NAME}" -q 2>/dev/null || true)
if [ -n "$LEFTOVER" ]; then
    echo "$LEFTOVER" | while read -r vol; do
        echo -e "  ${YELLOW}\$${RESET}  docker volume rm $vol"
        docker volume rm "$vol" 2>/dev/null && ok "Removed volume: $vol" || warn "Could not remove: $vol"
    done
else
    info "No leftover volumes found."
fi

ok "Volumes removed."

# ── 3. Remove .env ────────────────────────────────────────────────────────────
step "3/3" "Removing .env file"

if [ -f "$ENV_FILE" ]; then
    echo -e "  ${YELLOW}\$${RESET}  rm $ENV_FILE"
    rm -f "$ENV_FILE"
    ok "Removed: $ENV_FILE"
else
    info ".env not found — already clean."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   Reset complete.                        ║${RESET}"
echo -e "${BOLD}${GREEN}║   Run: bash quickstart-solo.sh           ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${RESET}"
echo ""
