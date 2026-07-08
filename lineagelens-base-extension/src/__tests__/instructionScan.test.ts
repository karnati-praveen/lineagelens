import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { classifyInstructionFiles, scanInstructionFiles } from '../risk/instructionScan';

// ── classifyInstructionFiles (pure) ───────────────────────────────────────────

test('classifies known instruction files by tool', () => {
  const out = classifyInstructionFiles([
    '/repo/.cursorrules',
    '/repo/.windsurfrules',
    '/repo/.github/copilot-instructions.md',
    '/repo/.continue/config.yaml',
    '/repo/CLAUDE.md',
    '/repo/.cursor/rules/style.md',
    '/repo/src/app.ts', // not an instruction file
  ]);
  const byPath = Object.fromEntries(out.map((f) => [f.path, f.tool]));
  expect(byPath['/repo/.cursorrules']).toBe('Cursor');
  expect(byPath['/repo/.windsurfrules']).toBe('Windsurf');
  expect(byPath['/repo/.github/copilot-instructions.md']).toBe('GitHub Copilot');
  expect(byPath['/repo/.continue/config.yaml']).toBe('Continue');
  expect(byPath['/repo/CLAUDE.md']).toBe('Claude Code');
  expect(byPath['/repo/.cursor/rules/style.md']).toBe('Cursor');
  expect(byPath['/repo/src/app.ts']).toBeUndefined();
});

test('handles Windows-style backslash paths', () => {
  const out = classifyInstructionFiles(['C:\\repo\\.github\\copilot-instructions.md']);
  expect(out).toHaveLength(1);
  expect(out[0].tool).toBe('GitHub Copilot');
});

test('returns nothing for a project with no instruction files', () => {
  expect(classifyInstructionFiles(['/repo/index.ts', '/repo/README.md'])).toEqual([]);
});

// ── scanInstructionFiles (fs) ─────────────────────────────────────────────────

test('scans a workspace root for instruction files', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'll-instr-'));
  try {
    fs.writeFileSync(path.join(root, '.cursorrules'), 'be concise');
    fs.mkdirSync(path.join(root, '.github'), { recursive: true });
    fs.writeFileSync(path.join(root, '.github', 'copilot-instructions.md'), '# rules');
    fs.mkdirSync(path.join(root, '.cursor', 'rules'), { recursive: true });
    fs.writeFileSync(path.join(root, '.cursor', 'rules', 'style.md'), 'tabs');

    const found = scanInstructionFiles(root);
    const tools = found.map((f) => f.tool).sort();
    expect(tools).toEqual(['Cursor', 'Cursor', 'GitHub Copilot']);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('scanning a root with no instruction files returns an empty list', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'll-instr-'));
  try {
    fs.writeFileSync(path.join(root, 'index.ts'), 'export {}');
    expect(scanInstructionFiles(root)).toEqual([]);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
