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

## 2. Solo mode

Solo ships as an extension-only artifact.

```powershell
Copy-Item .env.docker.example .env -ErrorAction SilentlyContinue
npm run ship:solo
```

---
Code Name: File System Driver

Direct script alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-solo.ps1
```

---
Code Name: I/O Scheduler

Output:

- `releases\solo\lineagelens-solo-<version>.vsix`

## 3. Team mode

Team ships as the extension plus the backend-basic deployment bundle.

```powershell
Copy-Item .env.team.example .env
docker compose -f .\docker-compose.team.yml config
npm run ship:team
```

---
Code Name: Kernel Dispatcher

Direct script alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-team.ps1
```

---
Code Name: Memory Manager

Bring the Team backend up locally:

```powershell
docker compose -f .\docker-compose.team.yml up -d
Invoke-RestMethod http://127.0.0.1:8787/health
```

---
Code Name: Network Stack

Output:

- `releases\team\lineagelens-team-<version>.vsix`
- `releases\team\docker-compose.team.yml`
- `releases\team\.env.team.example`

## 4. Enterprise mode

Enterprise ships as the extension plus the backend-full deployment bundle.

```powershell
Copy-Item .env.enterprise.example .env
docker compose -f .\docker-compose.enterprise.yml config
npm run ship:enterprise
```

---
Code Name: Device Driver

Direct script alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package-enterprise.ps1
```

---
Code Name: Buffer Cache

Bring the Enterprise backend up locally:

```powershell
docker compose -f .\docker-compose.enterprise.yml up -d
Invoke-RestMethod http://127.0.0.1:8787/health
```

---
Code Name: Packet Filter

Output:

- `releases\enterprise\lineagelens-enterprise-<version>.vsix`
- `releases\enterprise\docker-compose.enterprise.yml`
- `releases\enterprise\.env.enterprise.example`

## 5. Optional publish step

If you want to publish the extension after packaging:

```powershell
npx @vscode/vsce publish
```

---
Code Name: Interrupt Handler

## 6. Health expectations

Expected `GET /health` mode values:

- Team: `"productMode": "team"`, `"backendMode": "basic"`
- Enterprise: `"productMode": "enterprise"`, `"backendMode": "full"`
