# Ship Commands

This file gives the exact commands to build and ship each LineageLens product mode from the current repo.

## 1. Common verification

Run these before shipping any mode:

```powershell
npm run compile
npm test
Set-Location backend
$env:PYTHONPATH='.'
..\.venv\Scripts\python.exe -m pytest tests
Set-Location ..
```

---
Code Name: Process Scheduler

## 2. LineageLens Base

LineageLens Base ships as an extension-only artifact.

```powershell
Copy-Item .\deploy\.env.docker.example .env -ErrorAction SilentlyContinue
npm run ship:base
```

---
Code Name: File System Driver

Direct script alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-base.ps1
```

---
Code Name: I/O Scheduler

Output:

- `releases\base\lineagelens-base-<version>.zip`

## 3. LineageLens Plus

LineageLens Plus ships as the extension plus the backend-basic deployment bundle.

```powershell
Copy-Item .\deploy\.env.plus.example .env
docker compose -f .\deploy\docker-compose.plus.yml config
npm run ship:plus
```

---
Code Name: Kernel Dispatcher

Direct script alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-plus.ps1
```

---
Code Name: Memory Manager

Bring the LineageLens Plus backend up locally:

```powershell
docker compose -f .\deploy\docker-compose.plus.yml up -d
Invoke-RestMethod http://127.0.0.1:8787/health
```

---
Code Name: Network Stack

Output:

- `releases\plus\lineagelens-plus-<version>.zip`
- `releases\plus\lineagelens-plus-<version>\backend\`
- `releases\plus\lineagelens-plus-<version>\deploy\docker-compose.plus.yml`
- `releases\plus\lineagelens-plus-<version>\deploy\.env.example`
- `releases\plus\lineagelens-plus-<version>\docs\native-backend.md`
- `releases\plus\lineagelens-plus-<version>\scripts\run-backend-native.ps1`
- `releases\plus\lineagelens-plus-<version>\scripts\test-backend-native.ps1`

## 4. LineageLens Max

LineageLens Max ships as the extension plus the backend-full deployment bundle.

```powershell
Copy-Item .\deploy\.env.max.example .env
docker compose -f .\deploy\docker-compose.max.yml config
npm run ship:max
```

---
Code Name: Device Driver

Direct script alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-max.ps1
```

---
Code Name: Buffer Cache

Bring the LineageLens Max backend up locally:

```powershell
docker compose -f .\deploy\docker-compose.max.yml up -d
Invoke-RestMethod http://127.0.0.1:8787/health
```

---
Code Name: Packet Filter

Output:

- `releases\max\lineagelens-max-<version>.zip`
- `releases\max\lineagelens-max-<version>\backend\`
- `releases\max\lineagelens-max-<version>\deploy\docker-compose.max.yml`
- `releases\max\lineagelens-max-<version>\deploy\.env.example`
- `releases\max\lineagelens-max-<version>\docs\native-backend.md`
- `releases\max\lineagelens-max-<version>\scripts\run-backend-native.ps1`
- `releases\max\lineagelens-max-<version>\scripts\test-backend-native.ps1`

## 5. Optional publish step

If you want to publish the extension after packaging:

```powershell
npx @vscode/vsce publish
```

---
Code Name: Interrupt Handler

## 6. Health expectations

Expected `GET /health` mode values (non-production only):

- LineageLens Plus: `"productMode": "plus"`, `"backendMode": "team"`
- LineageLens Max: `"productMode": "max"`, `"backendMode": "enterprise"`
