# Original User Request

## 2026-08-11T18:03:51Z

Find critical logic errors, edge cases, and performance bottlenecks in the `lineagelens-backend`, `lineagelens-src`, and `lineagelens-mcp` components of the LineageLens codebase, and write failing test cases that reproduce the discovered flaws.

Working directory: c:\Users\karna\OneDrive\Desktop\Lineagelens
Integrity mode: demo

## Requirements

### R1. Analyze Core Components
Thoroughly review the specified directories (`lineagelens-backend`, `lineagelens-src`, and `lineagelens-mcp`) for critical logic flaws and performance bottlenecks.

### R2. Reproduction Tests
For every critical flaw identified, develop a reproducible, automated test script that demonstrates the flaw by failing. Place these in a new `reproduction_tests/` directory.

### R3. Comprehensive Issue Report
Produce a detailed final report (`issue_report.md`) documenting each flaw, its impact, the steps to reproduce it using the automated test, and recommended remediation.

### R4. Safe Execution
Do not break anything. Be extremely cautious and ensure that all analysis and test scripts are non-destructive to the codebase and application state.

## Acceptance Criteria

### Objective Verification
- [ ] At least one automated reproduction test script is provided in the `reproduction_tests/` directory for every flaw found.
- [ ] Each test script is objectively verifiable (e.g., it runs without manual intervention and clearly demonstrates the flaw).
- [ ] A final report `issue_report.md` exists detailing the root cause, impact, and remediation for each flaw.
- [ ] No existing code, tests, or application state is permanently broken or destructively altered during the investigation.
