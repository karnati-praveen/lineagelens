# LineageLens Deployment Guide

LineageLens is a full-stack AI code provenance platform. This guide covers production deployment options.

## Prerequisites

- Docker 24+ and Docker Compose v2
- PostgreSQL 15+ (or use the bundled container)
- (Optional) Neo4j 5+ for graph lineage
- (Optional) Redis for distributed rate limiting

## Ports

| Service | Port | Notes |
|---|---|---|
| Backend API + Dashboard | 8787 | FastAPI + static dashboard |
| Universal LLM Proxy | 8788 | Transparent AI traffic capture |
| Local extension proxy | 8080 | VS Code extension sidecar |
| PostgreSQL | 5432 | |
| Neo4j Bolt | 7687 | Max mode only |
| Neo4j Browser | 7474 | Max mode only |

---

## Docker Compose (Recommended)

### Plus mode (PostgreSQL, no Neo4j)
```bash
# Copy and configure environment
cp lineagelens-deploy/.env.plus.example lineagelens-deploy/.env.plus

# Edit required values:
# - POSTGRES_PASSWORD
# - JWT_SECRET_KEY (generate: openssl rand -hex 32)
# - JWT_REFRESH_SECRET_KEY (generate a different secret: openssl rand -hex 32)
# - Open https://localhost:8787/setup in the browser and complete the setup wizard

docker compose -f lineagelens-deploy/docker-compose.plus.yml up -d
```

### Max mode (PostgreSQL + Neo4j)
```bash
cp lineagelens-deploy/.env.max.example lineagelens-deploy/.env.max
# Configure NEO4J_AUTH, NEO4J_URI in addition to Plus settings
docker compose -f lineagelens-deploy/docker-compose.max.yml up -d
```

### Verify health
```bash
curl http://localhost:8787/health
lineagelens status
```

---

## Production Checklist

Before going to production:

- [ ] Set `APP_ENV=production` — enables HSTS, disables debug endpoints
- [ ] Use strong, unique `JWT_SECRET_KEY` and `JWT_REFRESH_SECRET_KEY` (>=32 bytes)
- [ ] Set `BACKEND_TRUSTED_HOSTS` to your domain(s)
- [ ] Configure `BACKEND_CORS_ORIGINS` to your frontend domain only
- [ ] Put the backend behind a reverse proxy (nginx/Caddy) with TLS
- [ ] Set `POSTGRES_PASSWORD` to a strong, unique password
- [ ] Configure `REDIS_URL` for multi-replica deployments
- [ ] Enable `LOG_FORMAT=json` for structured logging
- [ ] Set up database backups (pg_dump schedule)

---

## Reverse Proxy (nginx)

```nginx
server {
    listen 443 ssl http2;
    server_name lineagelens.yourcompany.com;

    ssl_certificate /etc/ssl/certs/lineagelens.crt;
    ssl_certificate_key /etc/ssl/private/lineagelens.key;

    location / {
        proxy_pass http://localhost:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location /proxy/ {
        proxy_pass http://localhost:8788/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Kubernetes (Helm-style)

LineageLens does not ship an official Helm chart yet, but here is a reference deployment manifest for the backend:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lineagelens-backend
  namespace: lineagelens
spec:
  replicas: 2
  selector:
    matchLabels:
      app: lineagelens-backend
  template:
    metadata:
      labels:
        app: lineagelens-backend
    spec:
      containers:
        - name: backend
          image: lineagelens/backend:latest
          ports:
            - containerPort: 8787
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: lineagelens-secrets
                  key: database-url
            - name: JWT_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: lineagelens-secrets
                  key: jwt-secret
            - name: REDIS_URL
              value: redis://redis-service:6379/0
            - name: APP_ENV
              value: production
            - name: LOG_FORMAT
              value: json
          livenessProbe:
            httpGet:
              path: /health
              port: 8787
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /health
              port: 8787
            initialDelaySeconds: 5
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: lineagelens-backend
  namespace: lineagelens
spec:
  selector:
    app: lineagelens-backend
  ports:
    - port: 8787
      targetPort: 8787
```

**Multi-replica notes**:
- Set `REDIS_URL` so rate limiting is shared across replicas
- All replicas share the same PostgreSQL instance
- The proxy (port 8788) can also be deployed as a separate deployment

---

## Multi-Host Setup

For organizations with separate proxy and backend hosts:

```
Developer machines  ->  LLM Proxy (port 8788)  ->  AI Providers
                              |
                    Backend API (port 8787, different host)
```

1. Deploy the proxy on developer machines or a shared proxy host
2. Set `LINEAGELENS_BACKEND_URL` on the proxy to point to the backend host
3. The backend handles storage, auth, and the dashboard
4. Use Redis for cross-host rate limiting

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `JWT_SECRET_KEY` | Yes | — | Access token signing key (>=32 bytes) |
| `JWT_REFRESH_SECRET_KEY` | Yes | — | Refresh token signing key (>=32 bytes, different from `JWT_SECRET_KEY`) |
| `APP_ENV` | No | `development` | Set to `production` for security headers + HSTS |
| `REDIS_URL` | No | — | Redis URL for distributed rate limiting |
| `NEO4J_URI` | No | — | Neo4j bolt URI (Max mode only) |
| `NEO4J_USERNAME` | No | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | No | — | Neo4j password |
| `EMBEDDING_API_KEY` | No | — | For embedding-backed semantic search |
| `LOG_FORMAT` | No | text | Set to `json` for structured logging |
| `BACKEND_CORS_ORIGINS` | No | `*` | Comma-separated allowed origins |
| `BACKEND_TRUSTED_HOSTS` | No | — | Comma-separated allowed Host header values |
| `BACKEND_MODE` | No | `team` | `solo`, `team`, or `enterprise` |

---

## Native (No Docker)

See [native-backend.md](native-backend.md) for running the backend directly with Python.

---

## Backup and Restore

```bash
# Backup PostgreSQL
pg_dump -Fc lineagelens > lineagelens-$(date +%Y%m%d).dump

# Restore
pg_restore -d lineagelens lineagelens-20240101.dump

# With Docker
docker exec lineagelens-postgres pg_dump -U lineagelens -Fc lineagelens > backup.dump
docker exec -i lineagelens-postgres pg_restore -U lineagelens -d lineagelens < backup.dump
```

---

## Upgrading

```bash
# Via CLI
lineagelens upgrade --mode plus

# Manual Docker Compose
docker compose -f lineagelens-deploy/docker-compose.plus.yml pull
docker compose -f lineagelens-deploy/docker-compose.plus.yml up --force-recreate -d
```
