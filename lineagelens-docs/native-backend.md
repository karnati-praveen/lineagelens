# Native Python Backend

Use this path if you want LineageLens Plus or LineageLens Max without Docker Desktop.
It runs the backend in a local Python virtual environment and connects to the PostgreSQL and Neo4j services you provide.

## What You Still Need

- Python 3.11+
- PostgreSQL 16 with pgvector for LineageLens Plus
- PostgreSQL 16 plus Neo4j 5 for LineageLens Max
- A working `lineagelens-lineagelens-backend/.env` file if you want custom connection strings or secrets

If you do not want to run the databases locally, point the backend at managed PostgreSQL and Neo4j services instead.

## Setup

1. Create and activate a Python virtual environment at the repo root.
2. Install backend dependencies with `python -m pip install -r lineagelens-backend/requirements.txt`.
3. For LineageLens Plus, copy `lineagelens-deploy/.env.plus.example` to `lineagelens-lineagelens-backend/.env` and fill in your database credentials.
4. For LineageLens Max, copy `lineagelens-deploy/.env.max.example` to `lineagelens-lineagelens-backend/.env` and fill in your PostgreSQL and Neo4j credentials.
5. Start the backend with the native PowerShell script.

## Commands

LineageLens Plus:

```powershell
npm run native:plus
```

LineageLens Max:

```powershell
npm run native:max
```

Run backend tests without Docker:

```powershell
npm run native:test
```

## Notes

- LineageLens Plus runs with `BACKEND_MODE=team`, `NEO4J_ENABLED=false`, and `VECTOR_SEARCH_ENABLED=false`.
- LineageLens Max runs with `BACKEND_MODE=enterprise`, `NEO4J_ENABLED=true`, and `VECTOR_SEARCH_ENABLED=true`.
- The native path avoids Docker Desktop memory overhead, but your database services still need to run somewhere.
