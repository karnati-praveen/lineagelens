#!/usr/bin/env bash
# LineageLens Plus — Quick Start
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
COMPOSE_FILE="$DEPLOY_DIR/docker-compose.plus.yml"
PROJECT_NAME="lineagelens-plus"
BACKEND_URL="http://localhost:8787"

# ── helpers ───────────────────────────────────────────────────────────────────

step() {
    local num="$1"
    local title="$2"
    echo -e "\n${BOLD}${CYAN}[$num]${RESET} ${BOLD}$title${RESET}"
}
ok()   { echo -e "  ${GREEN}✓${RESET}  $*"; }
info() { echo -e "  ${YELLOW}→${RESET}  $*"; }
die()  { echo -e "\n  ${RED}✗  ERROR:${RESET} $*\n" >&2; exit 1; }

# Print the command then run it
cmd() {
    echo -e "  ${YELLOW}\$${RESET}  $*"
    "$@"
}

# Compose shorthand — always includes project name and env file
compose() {
    $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   LineageLens Plus  —  Quick Start       ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${RESET}"

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
step "1/7" "Checking prerequisites"

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

# ── 2. Remove any conflicting containers and stale volumes ────────────────────
step "2/7" "Removing conflicting Postgres containers and stale volumes"

# a) Stop this project's own containers (clean slate)
if $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" ps -q 2>/dev/null | grep -q .; then
    info "Stopping existing $PROJECT_NAME containers..."
    $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" down --remove-orphans
    ok "Project containers stopped."
fi

# b) Kill ANY other container using our ports so they don't block startup
for PORT in 5432 8787 8788; do
    CIDS=$(docker ps -q --filter "publish=$PORT" 2>/dev/null || true)
    if [[ -n "$CIDS" ]]; then
        info "Port $PORT in use by container(s) [$CIDS] — stopping them..."
        docker stop $CIDS >/dev/null 2>&1 || true
        ok "Port $PORT freed."
    fi
done

# c) Remove this project's Postgres volume so Postgres always inits cleanly
#    with the correct password from .env (avoids auth failures on re-run)
for VOL in \
    "${PROJECT_NAME}_postgres_team_data" \
    "lineagelens-plus_postgres_team_data"; do
    if docker volume ls -q 2>/dev/null | grep -qx "$VOL"; then
        info "Removing stale volume: $VOL"
        docker volume rm "$VOL" >/dev/null 2>&1 || true
        ok "Removed: $VOL"
    fi
done

ok "Port and volume conflicts resolved."

# ── 3. Generate secrets & write .env ─────────────────────────────────────────
step "3/7" "Generating secrets"

if [[ -f "$ENV_FILE" ]]; then
    info ".env already exists — reusing existing secrets."
    # Verify that all required secrets are present and non-empty.
    # A partially-written .env (e.g. from a failed first run) will cause silent
    # Postgres auth failures or JWT signing errors that are hard to diagnose.
    for required_key in POSTGRES_PASSWORD JWT_SECRET_KEY JWT_REFRESH_SECRET_KEY; do
        key_value=$(grep -E "^${required_key}=.+" "$ENV_FILE" | head -1 | cut -d= -f2-)
        if [[ -z "$key_value" ]]; then
            die "Required secret '${required_key}' is missing or empty in $ENV_FILE. Delete the file and re-run to regenerate secrets, or run bash reset.sh."
        fi
    done
    ok "All required secrets present."
    info "Run bash reset.sh first if you want a completely fresh setup."
else
    POSTGRES_PASSWORD=$(openssl rand -hex 32)
    JWT_SECRET_KEY=$(openssl rand -hex 48)
    JWT_REFRESH_SECRET_KEY=$(openssl rand -hex 48)

    ok "POSTGRES_PASSWORD      generated (hex-32)"
    ok "JWT_SECRET_KEY         generated (hex-48)"
    ok "JWT_REFRESH_SECRET_KEY generated (hex-48)"

    cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
JWT_SECRET_KEY=$JWT_SECRET_KEY
JWT_REFRESH_SECRET_KEY=$JWT_REFRESH_SECRET_KEY
EXPLAIN_LLM_API_KEY=
EOF

    ok "Written to: $ENV_FILE"
fi

# ── 4. Start PostgreSQL ───────────────────────────────────────────────────────
step "4/7" "Starting PostgreSQL"

info "Starting postgres service..."
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE up -d postgres"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d postgres

info "Waiting for postgres to accept connections..."
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
    if [[ "$WAITED" -ge "$MAX_WAIT" ]]; then
        echo ""
        echo -e "\n  ${YELLOW}Logs from postgres:${RESET}"
        $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --tail 20 postgres
        die "PostgreSQL did not become ready within ${MAX_WAIT}s."
    fi
    printf "."
    sleep 3
    WAITED=$((WAITED + 3))
done

# ── 5. Run migrations ─────────────────────────────────────────────────────────
step "5/7" "Running database migrations"

info "Building backend and proxy images..."
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE build backend proxy"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" build backend proxy

info "Running: alembic upgrade head"
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE run --rm --no-deps backend alembic upgrade head"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
    run --rm --no-deps backend alembic upgrade head

ok "All migrations applied."

# ── 6. Start the backend ──────────────────────────────────────────────────────
step "6/7" "Starting the backend and proxy"

echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE up -d"
$COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

info "Waiting for backend at $BACKEND_URL ..."
MAX_WAIT=60
WAITED=0
printf "  "
while true; do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$BACKEND_URL/health" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" = "200" ]]; then
        echo ""
        ok "Backend is up."
        break
    fi
    if [[ "$WAITED" -ge "$MAX_WAIT" ]]; then
        echo ""
        echo -e "\n  ${YELLOW}Logs from backend:${RESET}"
        $COMPOSE --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --tail 30 backend
        die "Backend did not respond within ${MAX_WAIT}s."
    fi
    printf "."
    sleep 3
    WAITED=$((WAITED + 3))
done

# ── 7. Verify ─────────────────────────────────────────────────────────────────
step "7/7" "Verifying"

echo -e "  ${YELLOW}\$${RESET}  curl -s $BACKEND_URL/health | python3 -m json.tool"
curl -s "$BACKEND_URL/health" | python3 -m json.tool

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   LineageLens Plus is ready!             ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  ${BOLD}Dashboard:${RESET} ${CYAN}$BACKEND_URL/dashboard${RESET}  ← open this in your browser"
echo -e "  API:       ${CYAN}$BACKEND_URL${RESET}"
echo -e "  Proxy:     ${CYAN}http://localhost:8788${RESET}"
echo ""
echo -e "  ${BOLD}Open the dashboard to register and manage your workspace:${RESET}"
echo -e "  ${CYAN}$BACKEND_URL/dashboard${RESET}"
echo ""
echo -e "  ${BOLD}Or register via API:${RESET}"
echo ""
echo -e "  ${YELLOW}\$${RESET}  curl -s -X POST $BACKEND_URL/auth/register \\"
echo       "       -H 'Content-Type: application/json' \\"
echo       "       -d '{\"username\":\"admin\",\"password\":\"YourPassword123!\",\"workspace_id\":\"my-workspace\"}' \\"
echo       "       | python3 -m json.tool"
echo ""
echo -e "  Proxy:  ${CYAN}http://localhost:8788${RESET}  (universal LLM capture)"
echo ""
echo -e "  ${BOLD}── Proxy setup (one-time after first login) ──────────────────────${RESET}"
echo ""
echo -e "  1. Register and log in to get your token (see above)"
echo -e "  2. Add your token to ${YELLOW}deploy/.env${RESET}:"
echo -e "       ${YELLOW}PROXY_INGEST_TOKEN=<your-access-token>${RESET}"
echo -e "  3. Restart the proxy:"
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE --env-file $ENV_FILE restart proxy"
echo ""
echo -e "  ${BOLD}── Point your AI tools at the proxy ─────────────────────────────${RESET}"
echo ""
echo -e "  ${CYAN}# Claude Code / Anthropic SDK${RESET}"
echo -e "  export ANTHROPIC_BASE_URL=http://localhost:8788"
echo ""
echo -e "  ${CYAN}# OpenAI SDK / Cursor / Copilot / any OpenAI-compatible tool${RESET}"
echo -e "  export OPENAI_BASE_URL=http://localhost:8788"
echo ""
echo -e "  ${CYAN}# Verify proxy is running${RESET}"
echo -e "  curl -s http://localhost:8788/proxy-health | python3 -m json.tool"
echo ""
echo -e "  ${BOLD}Stop all services:${RESET}"
echo -e "  ${YELLOW}\$${RESET}  $COMPOSE --project-name $PROJECT_NAME -f $COMPOSE_FILE down"
echo ""
echo -e "  ${BOLD}Full reset (deletes all data):${RESET}"
echo -e "  ${YELLOW}\$${RESET}  bash reset.sh"
echo ""
echo "  See docs/native-backend.md for the full API reference."
echo ""
