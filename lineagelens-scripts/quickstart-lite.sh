#!/usr/bin/env bash
# LineageLens Lite — Quick Start
# Single container, SQLite, zero external dependencies.
# Runs on a $5 VPS or a laptop.
#
# Usage: bash quickstart-lite.sh

set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/lineagelens-deploy"
ENV_FILE="$DEPLOY_DIR/.env"
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.lite.yml"
DATA_DIR="$DEPLOY_DIR/data"
PROJECT_NAME="lineagelens"
BACKEND_URL="http://localhost:8787"

step()  { echo -e "\n${BOLD}${CYAN}[$1]${RESET} ${BOLD}$2${RESET}"; }
ok()    { echo -e "  ${GREEN}✓${RESET}  $*"; }
info()  { echo -e "  ${YELLOW}→${RESET}  $*"; }
die()   { echo -e "\n  ${RED}✗  ERROR:${RESET} $*\n" >&2; exit 1; }

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   LineageLens Lite  —  Quick Start       ║${RESET}"
echo -e "${BOLD}║   Single container · SQLite · No deps    ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
step "1/4" "Checking prerequisites"

command -v docker >/dev/null 2>&1 || die "Docker not found. Install from https://docs.docker.com/get-docker/"

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    die "Docker Compose not found. Install from https://docs.docker.com/compose/install/"
fi

docker --version
$COMPOSE version
ok "Docker is ready."

# ── 2. Check port 8787 for conflicts ─────────────────────────────────────────
CIDS=$(docker ps -q --filter "publish=8787" 2>/dev/null || true)
if [[ -n "$CIDS" ]]; then
    CONTAINERS=$(docker ps --filter "publish=8787" --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')
    die "Port 8787 is already in use by container(s): ${CONTAINERS:-$CIDS}. Stop them manually and re-run."
fi

# ── 3. Generate secrets ───────────────────────────────────────────────────────
step "2/4" "Setting up configuration"

mkdir -p "$DATA_DIR"
chmod 777 "$DATA_DIR"

if [[ -f "$ENV_FILE" ]]; then
    info ".env already exists — reusing existing secrets."
    key_value=$(grep -E "^JWT_SECRET_KEY=.+" "$ENV_FILE" | head -1 | cut -d= -f2-)
    if [[ -z "$key_value" ]]; then
        die "JWT_SECRET_KEY is missing in $ENV_FILE. Delete the file and re-run to regenerate."
    fi
    ok "Secrets verified."
else
    JWT_SECRET_KEY=$(openssl rand -hex 48 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(48))")
    JWT_REFRESH_SECRET_KEY=$(openssl rand -hex 48 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(48))")

    cat > "$ENV_FILE" <<EOF
JWT_SECRET_KEY=$JWT_SECRET_KEY
JWT_REFRESH_SECRET_KEY=$JWT_REFRESH_SECRET_KEY
# Optional: set this to enable AI-powered explanations
# EXPLAIN_LLM_API_KEY=sk-...
# BACKEND_CORS_ORIGINS=http://localhost:3000
EOF

    ok "JWT_SECRET_KEY         generated"
    ok "JWT_REFRESH_SECRET_KEY generated"
    ok "Written to: $ENV_FILE"
fi

# ── 4. Start LineageLens ──────────────────────────────────────────────────────
step "3/4" "Starting LineageLens (building image if needed)"

$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build

info "Waiting for LineageLens to be ready..."
MAX_WAIT=90
WAITED=0
printf "  "
while true; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/health" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" = "200" ]]; then
        echo ""
        ok "LineageLens is up."
        break
    fi
    if [[ "$WAITED" -ge "$MAX_WAIT" ]]; then
        echo ""
        echo -e "\n  ${YELLOW}Container logs:${RESET}"
        $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --tail 30
        die "LineageLens did not respond within ${MAX_WAIT}s."
    fi
    printf "."
    sleep 3
    WAITED=$((WAITED + 3))
done

# ── Done ──────────────────────────────────────────────────────────────────────
step "4/4" "Opening setup wizard"

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   LineageLens Lite is ready!             ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}Open this URL in your browser:${RESET}"
echo ""
echo -e "  ${BOLD}${CYAN}  $BACKEND_URL/setup${RESET}"
echo ""
echo -e "  The setup wizard will create your admin account."
echo -e "  No curl commands needed — everything is in the browser."
echo ""
echo -e "  ${BOLD}Data is stored at:${RESET}  $DATA_DIR/lineagelens.db"
echo ""
echo -e "  ${BOLD}Stop:${RESET}   $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE down"
echo -e "  ${BOLD}Logs:${RESET}   $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE logs -f"
echo ""

# Auto-open browser if possible
if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$BACKEND_URL/setup" >/dev/null 2>&1 &
elif command -v open >/dev/null 2>&1; then
    open "$BACKEND_URL/setup" >/dev/null 2>&1 &
fi
