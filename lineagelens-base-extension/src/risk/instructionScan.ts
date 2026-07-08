/**
 * AI instruction-file inventory (blueprint §8.2 / Feature 11).
 *
 * Modern IDE agents are shaped by hidden/semi-hidden project instruction files
 * (.cursorrules, copilot-instructions.md, …). Teams rarely know which ones may
 * have influenced generated code. This inventories them so the receipt can note
 * "this change may have been influenced by these project instructions."
 *
 * `classifyInstructionFiles` is pure (testable); `scanInstructionFiles` is the
 * thin fs shell that enumerates known locations under a workspace root.
 */

import * as fs from 'fs';
import * as path from 'path';

export interface InstructionFile {
  path: string;
  tool: string;
}

const PATTERNS: { test: (p: string) => boolean; tool: string }[] = [
  { test: (p) => /(^|\/)\.cursorrules$/.test(p), tool: 'Cursor' },
  { test: (p) => /(^|\/)\.cursor\/rules\//.test(p), tool: 'Cursor' },
  { test: (p) => /(^|\/)\.windsurfrules$/.test(p), tool: 'Windsurf' },
  { test: (p) => /(^|\/)\.github\/copilot-instructions\.md$/.test(p), tool: 'GitHub Copilot' },
  { test: (p) => /(^|\/)\.github\/instructions\//.test(p), tool: 'GitHub Copilot' },
  { test: (p) => /(^|\/)\.continue\/config\.(json|ya?ml)$/.test(p), tool: 'Continue' },
  { test: (p) => /(^|\/)\.clinerules$/.test(p), tool: 'Cline' },
  { test: (p) => /(^|\/)CLAUDE\.md$/.test(p), tool: 'Claude Code' },
  { test: (p) => /(^|\/)AGENTS\.md$/.test(p), tool: 'AI agents' },
];

/** Pure: classify a list of paths into known AI instruction files. */
export function classifyInstructionFiles(paths: string[]): InstructionFile[] {
  const out: InstructionFile[] = [];
  for (const p of paths) {
    const normalized = p.replace(/\\/g, '/');
    const match = PATTERNS.find((rule) => rule.test(normalized));
    if (match) {
      out.push({ path: p, tool: match.tool });
    }
  }
  return out;
}

// Fixed single-file candidates checked directly under the workspace root.
const FIXED_CANDIDATES = [
  '.cursorrules',
  '.windsurfrules',
  '.github/copilot-instructions.md',
  '.continue/config.json',
  '.continue/config.yaml',
  '.continue/config.yml',
  '.clinerules',
  'CLAUDE.md',
  'AGENTS.md',
];

// Directories whose contained files are all instruction files.
const SCAN_DIRS = ['.cursor/rules', '.github/instructions'];

/** Enumerate AI instruction files under a workspace root (best-effort; never throws). */
export function scanInstructionFiles(root: string): InstructionFile[] {
  const found: string[] = [];
  for (const rel of FIXED_CANDIDATES) {
    const abs = path.join(root, rel);
    try {
      if (fs.existsSync(abs) && fs.statSync(abs).isFile()) { found.push(abs); }
    } catch {
      // ignore unreadable candidates
    }
  }
  for (const dir of SCAN_DIRS) {
    const abs = path.join(root, dir);
    try {
      if (fs.statSync(abs).isDirectory()) {
        for (const entry of fs.readdirSync(abs)) {
          found.push(path.join(abs, entry));
        }
      }
    } catch {
      // directory absent — skip
    }
  }
  return classifyInstructionFiles(found);
}
