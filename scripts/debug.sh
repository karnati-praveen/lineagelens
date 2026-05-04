#!/usr/bin/env bash
# LineageLens bundle debug script.
# Run from the packaged bundle root: bash debug.sh

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/deploy"
ENV_FILE="$DEPLOY_DIR/.env"
BACKEND_URL="http://localhost:8787"
FAILED_STEPS=()

if [[ -f "$DEPLOY_DIR/docker-compose.plus.yml" ]]; then
    BUNDLE_MODE="team"
    COMPOSE_FILE="$DEPLOY_DIR/docker-compose.plus.yml"
    PROJECT_NAME="lineagelens-plus"
    SERVICE_NAMES=(postgres backend)
elif [[ -f "$DEPLOY_DIR/docker-compose.max.yml" ]]; then
    BUNDLE_MODE="enterprise"
    COMPOSE_FILE="$DEPLOY_DIR/docker-compose.max.yml"
    PROJECT_NAME="lineagelens-max"
    SERVICE_NAMES=(postgres neo4j backend)
else
    echo -e "${RED}ERROR${RESET}: could not detect Plus or Max bundle contents under $DEPLOY_DIR" >&2
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
else
    echo -e "${RED}ERROR${RESET}: Docker Compose is not available." >&2
    exit 1
fi

compose() {
    "${COMPOSE_CMD[@]}" --project-name "$PROJECT_NAME" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
    return
}

pass() {
    local msg="$1"
    echo -e "  ${GREEN}PASS${RESET} $msg"
    return
}

fail() {
    local msg="$1"
    echo -e "  ${RED}FAIL${RESET} $msg"
    FAILED_STEPS+=("$msg")
    return
}

section() {
    local title="$1"
    echo
    echo -e "${CYAN}==> $title${RESET}"
    return
}

run_check() {
    local label="$1"
    shift
    echo -e "  ${YELLOW}\$${RESET} $*"
    if "$@"; then
        pass "$label"
    else
        fail "$label"
    fi
    return
}

wait_for_health() {
    local service_name="$1"
    local timeout_seconds="$2"
    local elapsed_seconds=0
    local current_health="missing"

    while [[ "$elapsed_seconds" -le "$timeout_seconds" ]]; do
        local container_id
        container_id="$(compose ps -q "$service_name" 2>/dev/null || true)"
        if [[ -n "$container_id" ]]; then
            current_health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || echo unknown)"
            if [[ "$current_health" = "healthy" ]]; then
                return 0
            fi
        else
            current_health="missing"
        fi

        sleep 3
        elapsed_seconds=$((elapsed_seconds + 3))
    done

    echo -e "  ${RED}last health:${RESET} $service_name -> $current_health"
    return 1
}

print_logs() {
    local service_name="$1"
    echo ""
    echo "  recent logs for $service_name:"
    compose logs --tail 20 "$service_name" || true
    return
}

section "Bundle"
run_check "detected $BUNDLE_MODE bundle" test -f "$COMPOSE_FILE"
run_check "compose file exists" test -f "$COMPOSE_FILE"
run_check "environment file exists" test -f "$ENV_FILE"

section "Prerequisites"
run_check "docker available" command -v docker
run_check "curl available" command -v curl
run_check "python3 available" command -v python3

section "Environment"
if [[ -f "$ENV_FILE" ]]; then
    required_keys=(POSTGRES_PASSWORD JWT_SECRET_KEY JWT_REFRESH_SECRET_KEY)
    if [[ "$BUNDLE_MODE" = "enterprise" ]]; then
        required_keys+=(NEO4J_PASSWORD)
    fi

    missing_keys=()
    for key in "${required_keys[@]}"; do
        if ! grep -q "^${key}=" "$ENV_FILE"; then
            missing_keys+=("$key")
        fi
    done

    if [[ ${#missing_keys[@]} -eq 0 ]]; then
        pass "required env keys present"
    else
        fail "missing env keys: ${missing_keys[*]}"
    fi
fi

section "Compose validation"
run_check "compose config parses" compose config

section "Database services"
if [[ "$BUNDLE_MODE" = "team" ]]; then
    run_check "start postgres" compose up -d --build postgres
    run_check "postgres becomes healthy" wait_for_health postgres 60
    if [[ ${#FAILED_STEPS[@]} -gt 0 ]]; then
        print_logs postgres
    fi
else
    run_check "start postgres and neo4j" compose up -d --build postgres neo4j
    run_check "postgres becomes healthy" wait_for_health postgres 60
    run_check "neo4j becomes healthy" wait_for_health neo4j 120
    if [[ ${#FAILED_STEPS[@]} -gt 0 ]]; then
        print_logs postgres
        print_logs neo4j
    fi
fi

section "Migration checks"
run_check "alembic heads" compose run --rm --no-deps backend alembic heads
run_check "alembic current" compose run --rm --no-deps backend alembic current
run_check "alembic upgrade head" compose run --rm --no-deps backend alembic upgrade head

section "Backend"
run_check "start backend" compose up -d backend
run_check "backend becomes healthy" bash -lc "for i in {1..20}; do curl -fsS '$BACKEND_URL/health' >/dev/null && exit 0; sleep 3; done; exit 1"
if [[ ${#FAILED_STEPS[@]} -gt 0 ]]; then
    print_logs backend
fi

section "Summary"
if [[ ${#FAILED_STEPS[@]} -eq 0 ]]; then
    echo -e "${GREEN}All checks passed.${RESET}"
    exit 0
fi

echo -e "${RED}Failed checks:${RESET}"
for step_name in "${FAILED_STEPS[@]}"; do
    echo "  - $step_name"
done
exit 1
