# lineagelens blame & report — git blame for AI-generated code

`git blame` tells you **who** wrote a line. `lineagelens blame` tells you **which AI** wrote it.

```
AI  claude-opus-4-8  2026-06-01   3 │ def fetch_user(user_id):
AI  claude-opus-4-8  2026-06-01   4 │     """Load a user by id."""
AI  claude-opus-4-8  2026-06-01   5 │     conn = get_connection()
                                  6 │
                                  7 │ def main():

── lineagelens blame ─ src/users.py
   3/7 lines AI-attributed (42.9%)
     claude-opus-4-8: 3 lines
```

It works on **every tier**, including Base (no backend at all): it maps your captured
provenance records onto the *current* contents of a file and attributes each line.

## Usage

```bash
# Base tier — no backend. Run "LineageLens: Export JSON" in VS Code first:
lineagelens blame src/users.py --input captures.json

# Agent Trace export (best-effort: the trace format carries only a code preview):
lineagelens blame src/users.py --input trace.jsonl

# Plus/Max tier — query the backend directly:
lineagelens blame src/users.py \
  --url https://lineagelens.internal --token "$JWT" --workspace my-team
# or via env vars: LINEAGELENS_URL, LINEAGELENS_TOKEN, LINEAGELENS_WORKSPACE
```

### Options

| Flag | Meaning |
|---|---|
| `-i, --input <file>` | Record source: extension `captures.json`, agent-trace `.jsonl`, or a saved `/search` response |
| `-u, --url <backendUrl>` | LineageLens backend URL (env: `LINEAGELENS_URL`) |
| `-t, --token <jwt>` | Backend access token (env: `LINEAGELENS_TOKEN`) |
| `-w, --workspace <id>` | Workspace id for backend mode (env: `LINEAGELENS_WORKSPACE`) |
| `--review-status <status>` | Filter by review status: `unreviewed` \| `pending` \| `reviewed` — **backend mode only** |
| `--category <slug>` | Filter by risk category: `auth` \| `secrets` \| `sql` \| `shell` \| `dom` \| `payments` \| `eval` \| `large-block` — **backend mode only** |
| `--stats` | Print only the summary, not the annotated file |
| `--min-confidence <n>` | Ignore records below this capture confidence (0–1) |
| `--no-color` | Disable ANSI colors |
| `--json` (global flag) | Machine-readable output for CI / scripting |

## lineagelens report — repo-wide attribution

`blame` answers the question for one file; `report` answers it for a whole repo:
**"How much of this codebase did AI write?"**

The flagship Risk Discovery command — show all unreviewed AI-generated auth code in this repo:

```bash
lineagelens report . \
  --url https://lineagelens.internal --token "$JWT" --workspace my-team \
  --review-status unreviewed --category auth
```

Other examples:

```bash
# Scan the current repo against an extension export:
lineagelens report . --input captures.json

# Paste-ready markdown for a README or PR description:
lineagelens report . --input captures.json --md

# Backend mode (pulls all workspace records, paged):
lineagelens report . --url https://lineagelens.internal --token "$JWT" --workspace my-team

# All unreviewed records (no category filter):
lineagelens report . --url https://lineagelens.internal --token "$JWT" \
  --workspace my-team --review-status unreviewed
```

> **Note:** `--review-status` and `--category` require backend mode (`--url`/`--token`/`--workspace` or env vars). They cannot be evaluated from a local `--input` file; the CLI exits with a clear error if you try.

```
── lineagelens report ─ my-repo

  src/users.py    ██████████████░░░░░░  71.4%  5/7 lines
  src/api.ts      ████████░░░░░░░░░░░░  40.0%  12/30 lines

  Repo total: 17/37 lines AI-attributed (45.9%) across 2 of 14 scanned files
    claude-opus-4-8: 17 lines
```

It skips `node_modules`, `.git`, build output, binaries, and files over 1 MB,
and only blames files that have at least one plausible provenance record, so
repo-wide runs stay fast. Extra options: `--top <n>` (table size, default 25),
`--md`, and the same `--input`/backend/`--min-confidence`/`--json`/`--review-status`/`--category` flags as `blame`.

When `--review-status` or `--category` is active, the summary line includes a filter note:

```
  Repo total: 142/4,210 lines AI-attributed (3.4%) across 7 of 182 scanned files — filter: unreviewed, auth
```

## How matching works

1. Records are filtered to the target file (exact path, then basename — capture
   paths are often absolute paths from another machine).
2. Records are applied **oldest → newest**, so the most recent insertion wins
   overlapping lines, exactly like `git blame`.
3. **Exact match** (`AI` marker): the record's inserted lines appear contiguously
   in the file. Comparison is whitespace-normalized, so re-indenting doesn't
   break attribution.
4. **Fuzzy fallback** (`AI?` marker): if the block was edited after insertion,
   individual *significant* lines (≥ 8 chars, not punctuation-only) that still
   match are attributed line-by-line. Trivial lines like `}` are never
   attributed on their own.

## CI usage

`--json` makes the output scriptable — for example, failing a pipeline when a
file is mostly unreviewed AI code:

```bash
PERCENT=$(lineagelens --json blame src/payments.py --input captures.json | jq '.stats.percent')
if (( $(echo "$PERCENT > 80" | bc -l) )); then
  echo "::warning::src/payments.py is ${PERCENT}% AI-generated — request human review"
fi
```

## Limitations

- Attribution is content-based, not keystroke-based: heavily rewritten AI code
  will (correctly) stop being attributed.
- Agent Trace input (`.jsonl`) carries only a 120-character preview per record,
  so matching is best-effort; prefer the extension JSON export or backend mode.
- Common boilerplate lines may be fuzzy-attributed (`AI?`) if they also appear
  inside an AI insertion; exact contiguous matches always take precedence.
