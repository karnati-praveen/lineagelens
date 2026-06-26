# Offline / Air-Gapped Install & Mirror Kit

Resilience against GitHub / marketplace / package-registry outage or vendor
shutdown (improvement plan Part 3 #23–#26 and Part 5 #59). Everything LineageLens
needs to run and to verify evidence can be carried on disk — no registry calls at
runtime.

## What you already get in a release

Each tagged release publishes (see `.github/workflows/release.yml`):

- **Base VSIX** (`lineagelens-base-<ver>.vsix`) — install offline with
  `code --install-extension lineagelens-base-<ver>.vsix`.
- **Lite / Plus / Max bundles** (`.zip`) — each contains the full backend + proxy
  **source**, compose files, and quickstart scripts. This *is* the customer source
  export: you can run, rebuild, and audit from the bundle alone.
- **`SHA256SUMS.txt`** — verify every asset: `sha256sum -c SHA256SUMS.txt`.
- **`*.spdx.json` SBOM** — full dependency inventory (SPDX).
- **SLSA build-provenance attestation** — verify with
  `gh attestation verify <asset> --owner <org>`.

## 1. Build an offline dependency kit

Run these once on a machine **with** network; carry the outputs to the air-gapped host.

### Python wheelhouse (pinned, hashed)

```bash
cd lineagelens-backend
# Freeze the exact resolved versions you tested with:
pip freeze > requirements.lock.txt
# Download every wheel (incl. transitive) for the target platform:
pip download -r requirements.lock.txt -d wheelhouse/
# On the air-gapped host:
pip install --no-index --find-links wheelhouse/ -r requirements.lock.txt
```

For hash-pinned installs, generate hashes with `pip-compile --generate-hashes`
(pip-tools) and install with `pip install --require-hashes -r requirements.lock.txt`.

### npm (base extension) — offline from the lockfile

```bash
cd lineagelens-base-extension
npm ci            # reproducible install from package-lock.json
npm pack          # or cache node_modules/ alongside the bundle
```

### Container images — save & load by digest

```bash
# Pin by digest, then save:
docker pull postgres:16@sha256:<digest>
docker pull neo4j:5@sha256:<digest>
docker save postgres:16 neo4j:5 -o lineagelens-images.tar
# Air-gapped host:
docker load -i lineagelens-images.tar
```

Pin compose/image references to `@sha256:<digest>` (not floating tags) in your
deploy `.env` / compose overrides so a rebuild is reproducible.

## 2. Independent source & archive mirrors

- Mirror this repository to a second forge (GitLab, Gitea, or a tarball in object
  storage with a retention lock). The release `.zip` bundles double as a source
  archive at the exact released commit.
- Keep a copy of `SHA256SUMS.txt` and the SBOM with each mirror so integrity is
  checkable without the original source.

## 3. Signed VSIX

- Sign the VSIX during release (`vsce` supports signing) and publish the signature
  next to the `.vsix`. Installs then survive marketplace removal:
  `code --install-extension lineagelens-base-<ver>.vsix` works from a local file.
- Verify the SLSA provenance attestation for the `.vsix` before installing in a
  locked-down environment.

## 4. License continuity

See **VENDOR_COVENANT.md**. License verification is fully offline; perpetual
licenses keep purchased features unlocked even if the vendor disappears.

## 5. Graph independence (#26) — status

- The **canonical** store is PostgreSQL; Neo4j is an optional, rebuildable
  projection. Nothing in the evidence plane requires Neo4j to verify history.
- Blast-radius descendant lineage currently lives in Neo4j. When Neo4j is absent
  the API reports `coverageStatus: "unavailable"` (it never fakes "zero
  descendants" — Part 2 #14), so results stay honest.
- **Scoped follow-up (not yet built):** a SQL-native lineage-edge projection so
  descendant queries have a Postgres-only fallback and the graph is fully
  rebuildable from the event ledger without Neo4j. Tracked as a continuity task;
  it needs an edges projection table + rebuild job and is intentionally left as a
  designed-but-unbuilt item rather than a partial implementation.
