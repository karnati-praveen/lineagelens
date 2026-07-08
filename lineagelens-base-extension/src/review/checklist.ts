/**
 * Default human-review checklists by risk type — pure, no vscode.
 *
 * Surfaced in the capture receipt to give a reviewer concrete things to check
 * for AI-generated code in sensitive areas. The risk type is supplied generically
 * for now; Phase 4's local risk rules will classify captures and pick the list.
 */

export type RiskType =
  | 'auth'
  | 'crypto'
  | 'database'
  | 'dependencies'
  | 'ci'
  | 'infra'
  | 'tests'
  | 'migration'
  | 'generic';

const CHECKLISTS: Record<RiskType, string[]> = {
  auth: [
    'Are authentication and session checks actually enforced (not just declared)?',
    'Could any path bypass the check (early return, default-allow, missing await)?',
    'Are tokens/passwords compared in constant time and never logged?',
    'Is there a test covering the unauthenticated/forbidden case?',
  ],
  crypto: [
    'Is a vetted library used instead of hand-rolled crypto?',
    'Are key sizes, modes, and IV/nonce handling correct and non-reused?',
    'Are secrets sourced from config/secret storage, not literals?',
  ],
  database: [
    'Are all queries parameterized (no string concatenation of user input)?',
    'Is access scoped to the current user/tenant?',
    'Are migrations reversible and indexes considered?',
  ],
  dependencies: [
    'Is each added dependency necessary, maintained, and from a trusted source?',
    'Are versions pinned and the lockfile updated intentionally?',
    'Was the license checked for compatibility?',
  ],
  ci: [
    'Do the workflow changes avoid leaking secrets to forks/PRs?',
    'Are third-party actions pinned to a SHA, not a moving tag?',
    'Did permissions get widened beyond what the job needs?',
  ],
  infra: [
    'Are resources least-privilege and not publicly exposed by default?',
    'Are state/secret backends configured securely?',
    'Was the blast radius of this change considered?',
  ],
  tests: [
    'Do the tests assert behaviour, not just that code runs?',
    'Are edge cases and failure paths covered?',
    'Are the assertions meaningful (no always-true checks)?',
  ],
  migration: [
    'Is the migration backward compatible / safely rerunnable?',
    'Is there a tested rollback path?',
    'Will it lock or rewrite large tables in production?',
  ],
  generic: [
    'Do you understand what every line does?',
    'Are inputs validated and errors handled?',
    'Is there a test covering this change?',
    'Does it follow the conventions of the surrounding code?',
  ],
};

/** Checklist items for a risk type; falls back to the generic list for unknown types. */
export function checklistFor(riskType: string): string[] {
  return CHECKLISTS[riskType as RiskType] ?? CHECKLISTS.generic;
}
