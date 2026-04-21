#!/usr/bin/env bash
# LineageLens Enterprise — Quick Start
# Run once after unzipping: bash quickstart.sh

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
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.enterprise.yml"
PROJECT_NAME="lineagelens-enterprise"
BACKEND_URL="http://localhost:8787"

# ── helpers ───────────────────────────────────────────────────────────────────

step() { echo -e "\n${BOLD}${CYAN}[$1]${RESET} ${BOLD}$2${RESET}"; }
ok()   { echo -e "  ${GREEN}✓${RESET}  $*"; }
info() { echo -e "  ${YELLOW}→${RESET}  $*"; }
die()  { echo -e "\n  ${RED}✗  ERROR:${RESET} $*\n" >&2; exit 1; }

cmd() {
    echo -e "  ${YELLOW}\$${RESET}  $*"
    "$@"
}

# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   LineageLens Enterprise — Quick Start   ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
step "1/8" "Checking prerequisites"

command -v docker  >/dev/null 2>&1 || die "Docker not found. Install from https://docs.docker.com/get-docker/"
command -v openssl >/dev/null 2>&1 || die "openssl not found. Install via: sudo apt install openssl"
command -v curl    >/dev/null 2>&1 || die "curl not found. Install via: sudo apt install curl"
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install via: sudo apt install python3"

cmd docker --version

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    die "Docker Compose not found. Install from https://docs.docker.com/compose/install/"
fi
cmd $COMPOSE version

ok "All prerequisites met."

# ── 2. Clean up any leftover containers ───────────────────────────────────────
step "2/8" "Cleaning up any existing containers"

if $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" ps -q 2>/dev/null | grep -q .; then
    info "Found existing containers. Stopping them first..."
    echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE down --remove-orphans"
    $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans
    ok "Existing containers stopped."
else
    ok "No existing containers found."
fi

# ── 3. Generate secrets & write .env ─────────────────────────────────────────
step "3/8" "Generating secrets"

if [ -f "$ENV_FILE" ]; then
    info ".env already exists — reusing existing secrets."
    for required_key in POSTGRES_PASSWORD NEO4J_PASSWORD JWT_SECRET_KEY JWT_REFRESH_SECRET_KEY; do
        key_value=$(grep -E "^${required_key}=.+" "$ENV_FILE" | head -1 | cut -d= -f2-)
        if [ -z "$key_value" ]; then
            die "Required secret '${required_key}' is missing or empty in $ENV_FILE. Delete the file and re-run to regenerate secrets, or run bash reset.sh."
        fi
    done
    ok "All required secrets present."
    info "Run bash reset.sh first if you want a completely fresh setup."
else
    POSTGRES_PASSWORD=$(openssl rand -hex 32)
    NEO4J_PASSWORD=$(openssl rand -hex 24)
    JWT_SECRET_KEY=$(openssl rand -hex 48)
    JWT_REFRESH_SECRET_KEY=$(openssl rand -hex 48)

    ok "POSTGRES_PASSWORD      generated (hex-32)"
    ok "NEO4J_PASSWORD         generated (hex-24)"
    ok "JWT_SECRET_KEY         generated (hex-48)"
    ok "JWT_REFRESH_SECRET_KEY generated (hex-48)"

    cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=$NEO4J_PASSWORD
JWT_SECRET_KEY=$JWT_SECRET_KEY
JWT_REFRESH_SECRET_KEY=$JWT_REFRESH_SECRET_KEY
EXPLAIN_LLM_API_KEY=
EOF

    ok "Written to: $ENV_FILE"
fi

# ── 4. Start PostgreSQL and Neo4j ─────────────────────────────────────────────
step "4/8" "Starting PostgreSQL and Neo4j"

info "Starting database services..."
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE up -d postgres neo4j"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d postgres neo4j

# ── 5. Wait for PostgreSQL ────────────────────────────────────────────────────
step "5/8" "Waiting for PostgreSQL"

MAX_WAIT=60
WAITED=0
printf "  "
while true; do
    if $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
        exec -T postgres pg_isready -U postgres -d provenance >/dev/null 2>&1; then
        echo ""
        ok "PostgreSQL is ready."
        break
    fi
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo ""
        echo -e "\n  ${YELLOW}Logs from postgres:${RESET}"
        $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --tail 20 postgres
        die "PostgreSQL did not become ready within ${MAX_WAIT}s."
    fi
    printf "."
    sleep 3
    WAITED=$((WAITED + 3))
done

# ── 6. Wait for Neo4j ─────────────────────────────────────────────────────────
step "6/8" "Waiting for Neo4j (first boot takes ~60s)"

MAX_WAIT=120
WAITED=0
printf "  "
while true; do
    HEALTH=$($COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" ps -q neo4j 2>/dev/null \
        | xargs -I{} docker inspect --format '{{.State.Health.Status}}' {} 2>/dev/null || echo "starting")
    if [ "$HEALTH" = "healthy" ]; then
        echo ""
        ok "Neo4j is healthy."
        break
    fi
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo ""
        echo -e "\n  ${YELLOW}Logs from neo4j:${RESET}"
        $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --tail 20 neo4j
        die "Neo4j did not become healthy within ${MAX_WAIT}s."
    fi
    printf "."
    sleep 5
    WAITED=$((WAITED + 5))
done

# ── 7. Run migrations ─────────────────────────────────────────────────────────
step "7/8" "Running database migrations"

info "Running: alembic upgrade head"
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE run --rm --no-deps backend alembic upgrade head"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    run --rm --no-deps backend alembic upgrade head

ok "All migrations applied."

# ── 8. Start backend & verify ─────────────────────────────────────────────────
step "8/8" "Starting backend and verifying"

echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE up -d"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

info "Waiting for backend at $BACKEND_URL ..."
MAX_WAIT=60
WAITED=0
printf "  "
while true; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/health" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        echo ""
        ok "Backend is up."
        break
    fi
    if [ "$WAITED" -ge "$MAX_WAIT" ]; then
        echo ""
        echo -e "\n  ${YELLOW}Logs from backend:${RESET}"
        $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --tail 30 backend
        die "Backend did not respond within ${MAX_WAIT}s."
    fi
    printf "."
    sleep 3
    WAITED=$((WAITED + 3))
done

echo -e "  ${YELLOW}\$${RESET}  curl -s $BACKEND_URL/health | python3 -m json.tool"
curl -s "$BACKEND_URL/health" | python3 -m json.tool

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   LineageLens Enterprise is ready!       ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  API:           ${CYAN}$BACKEND_URL${RESET}"
echo -e "  Health:        ${CYAN}$BACKEND_URL/health${RESET}"
echo -e "  Neo4j Browser: ${CYAN}http://localhost:7474${RESET}  (user: neo4j)"
echo ""
echo -e "  ${BOLD}Register your first admin user:${RESET}"
echo ""
echo -e "  ${YELLOW}\$${RESET}  curl -s -X POST $BACKEND_URL/auth/register \\"
echo       "       -H 'Content-Type: application/json' \\"
echo       "       -d '{\"username\":\"admin\",\"password\":\"YourPassword123!\",\"workspace_id\":\"my-workspace\"}' \\"
echo       "       | python3 -m json.tool"
echo ""
echo -e "  ${BOLD}Stop all services:${RESET}"
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE down"
echo ""
echo -e "  ${BOLD}Full reset (deletes all data):${RESET}"
echo -e "  ${YELLOW}\$${RESET}  bash reset.sh"
echo ""
echo "  See docs/native-backend.md and docs/architecture.md for the full reference."
echo ""
