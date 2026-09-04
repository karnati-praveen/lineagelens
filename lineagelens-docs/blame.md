# lineagelens scan, blame & report — git blame for AI-generated code

> **Which command do I want?**
>
> | Command | Needs prior capture? | Answers |
> |---|---|---|
> | `scan` | **No** — reads git history | "What AI code is already in this repo?" |
> | `blame` | Yes | "Which AI wrote each line of this one file?" |
> | `report` | Yes | "How much of this repo did AI write, per file?" |
>
> Start with `scan` on a repo that has never had LineageLens installed. Use `blame`
> and `report` once capture is running and you have prompt- and model-level records.

---

## lineagelens scan — retroactive attribution, zero setup

`scan` is the only command that works on a repo LineageLens has never touched. It
reads AI authorship out of git metadata that the tools wrote themselves.

```bash
npx lineagelens scan .                      # this repo
npx lineagelens scan ../acme-api            # any repo
npx lineagelens scan . --md                 # paste-ready inventory
lineagelens --json scan .                   # full machine-readable result
```

> The `npx` form requires the CLI to be published to npm. Until it is, run it from a
> clone: `cd lineagelens-cli && npm install && node bin/lineagelens-cli.js scan <repo>`.
> Requirements are Node ≥ 18.17 and `git` — no Docker, database, or account.

### How attribution works

1. **Find declaring commits.** Every signature in
   [`lineagelens-cli/src/scan/lineagelens-cli-scan-signatures.js`](../lineagelens-cli/src/scan/lineagelens-cli-scan-signatures.js)
   matches something an AI tool writes into git itself — a `Co-Authored-By` trailer,
   a bot author identity, or a generated subject line. Nothing is guessed from
   commit shape or size, which is why matches are classed `declared` evidence.
2. **Blame HEAD.** `git blame --incremental` maps current file contents back to the
   commit that introduced each hunk, so the count reflects lines that **survived** —
   not historical churn that was later deleted or rewritten.
3. **Categorize.** The surviving AI lines are matched against the same risk slugs the
   backend uses (`auth`, `secrets`, `sql`, `shell`, `dom`, `payments`, `eval`, `ci`,
   `infra`, `large-block`) so a filter means the same thing in every command.

### Honesty guarantees

These are enforced in the engine and covered by tests, not just conventions:

- **Unattributed ≠ human.** Lines with no AI signal are reported as unattributed. The
  scan never claims a human wrote them.
- **A low percentage can be a floor.** If AI tooling is configured in the repo (a
  tracked *or* gitignored `CLAUDE.md`, `.cursorrules`, `.claude/`, `AGENTS.md`, …) but
  few commits declare it, the result is marked `known_incomplete` with
  `measurementKind: "lower_bound"`, and the headline renders as `≥N%`. A repo where an
  agent wrote everything and the developer stripped the trailers must not scan as
  "1% AI" without saying so.
- **Missing fields are named.** `assurance.unavailable` always lists what a retroactive
  read cannot recover: `prompt`, `model_reasoning`, `accepted_rejected_status`,
  `review_state`, `human_attestation`.

### Options

| Flag | Meaning |
|---|---|
| `--since <date>` | Only examine commits after a git date expression (`2026-01-01`, `90 days ago`) |
| `--max-commits <n>` | Examine at most n commits, newest first |
| `--tool <name>` | Attribute only one tool, e.g. `--tool "Claude Code"` |
| `--category <slug>` | Only show files whose AI code hits a risk surface |
| `--md` | Paste-ready markdown inventory |
| `--top <n>` | Files shown in the table (default 15) |
| `--no-color` | Disable ANSI colors |

### Limits worth knowing

- A tool that leaves no trace in git history is invisible to `scan`. That is what the
  coverage warning exists to surface.
- Blame does not follow moves across files, so a block relocated to a new file is
  attributed to the commit that placed it there.
- Lockfiles, minified bundles, SVGs, CSVs and binaries are excluded from both the
  numerator and the denominator.

---

## lineagelens blame — per-line attribution from captured records

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
