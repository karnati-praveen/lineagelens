# Native Python Backend

Use this path if you want Team or Enterprise mode without Docker Desktop.
It runs the backend in a local Python virtual environment and connects to the PostgreSQL and Neo4j services you provide.

## What You Still Need

- Python 3.11+
- PostgreSQL 16 with pgvector for Team mode
- PostgreSQL 16 plus Neo4j 5 for Enterprise mode
- A working `backend/.env` file if you want custom connection strings or secrets

If you do not want to run the databases locally, point the backend at managed PostgreSQL and Neo4j services instead.

## Setup

1. Create and activate a Python virtual environment at the repo root.
2. Install backend dependencies with `python -m pip install -r backend/requirements.txt`.
3. For Team mode, copy `.env.team.example` to `backend/.env` and fill in your database credentials.
4. For Enterprise mode, copy `.env.enterprise.example` to `backend/.env` and fill in your PostgreSQL and Neo4j credentials.
5. Start the backend with the native PowerShell script.

## Commands

Team mode:

```powershell
npm run native:team
```

Enterprise mode:

```powershell
npm run native:enterprise
```

Run backend tests without Docker:

```powershell
npm run native:test
```

## Notes

- Team mode runs with `BACKEND_MODE=basic`, `NEO4J_ENABLED=false`, and `VECTOR_SEARCH_ENABLED=false`.
- Enterprise mode runs with `BACKEND_MODE=full`, `NEO4J_ENABLED=true`, and `VECTOR_SEARCH_ENABLED=true`.
- The native path avoids Docker Desktop memory overhead, but your database services still need to run somewhere.
