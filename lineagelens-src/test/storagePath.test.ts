import assert from 'node:assert/strict';
import test from 'node:test';
import {
  normalizeLocalStorageLocation,
  resolveLocalStorageFilePath
} from '../localStoragePath';
import { pathsReferToSameFile, toPortableStoragePath } from '../pathUtils';

// These tests use Windows drive-letter paths and backslash expectations.
// They are only valid on Windows; skip on Linux/macOS CI runners.
const onWindows = process.platform === 'win32';

test('toPortableStoragePath stores workspace files as forward-slash relative paths',
  { skip: !onWindows },
  () => {
    const storedPath = toPortableStoragePath(
      String.raw`E:\Lineagelens\src\extension.ts`,
      String.raw`E:\Lineagelens`
    );
    assert.equal(storedPath, 'src/extension.ts');
  }
);

test('toPortableStoragePath keeps files outside the workspace absolute',
  { skip: !onWindows },
  () => {
    const storedPath = toPortableStoragePath(
      String.raw`E:\Shared\snippet.ts`,
      String.raw`E:\Lineagelens`
    );
    assert.equal(storedPath, 'E:/Shared/snippet.ts');
  }
);

test('pathsReferToSameFile matches legacy absolute paths to new relative paths',
  { skip: !onWindows },
  () => {
    const matches = pathsReferToSameFile(
      String.raw`E:\Lineagelens\src\extension.ts`,
      'src/extension.ts'
    );
    assert.equal(matches, true);
  }
);

test('pathsReferToSameFile rejects different files', () => {
  const matches = pathsReferToSameFile(
    String.raw`E:\Lineagelens\src\extension.ts`,
    'src/provenance.ts'
  );
  assert.equal(matches, false);
});

test('resolveLocalStorageFilePath returns undefined for globalState', () => {
  const filePath = resolveLocalStorageFilePath({
    location: 'globalState',
    workspaceRoot: String.raw`E:\Lineagelens`,
    defaultWorkspaceRelativePath: '.vscode/ai-provenance/records.json'
  });
  assert.equal(filePath, undefined);
});

test('resolveLocalStorageFilePath resolves default workspace file',
  { skip: !onWindows },
  () => {
    const filePath = resolveLocalStorageFilePath({
      location: 'workspaceFile',
      workspaceRoot: String.raw`E:\Lineagelens`,
      defaultWorkspaceRelativePath: '.vscode/ai-provenance/records.json'
    });
    assert.equal(filePath, String.raw`E:\Lineagelens\.vscode\ai-provenance\records.json`);
  }
);

test('resolveLocalStorageFilePath resolves custom workspace placeholder path',
  { skip: !onWindows },
  () => {
    const filePath = resolveLocalStorageFilePath({
      location: 'customFile',
      workspaceRoot: String.raw`E:\Lineagelens`,
      customFilePath: '${workspaceFolder}\\provenance\\records.json',
      defaultWorkspaceRelativePath: '.vscode/ai-provenance/records.json'
    });
    assert.equal(filePath, String.raw`E:\Lineagelens\provenance\records.json`);
  }
);

test('resolveLocalStorageFilePath resolves relative custom path under workspace',
  { skip: !onWindows },
  () => {
    const filePath = resolveLocalStorageFilePath({
      location: 'customFile',
      workspaceRoot: String.raw`E:\Lineagelens`,
      customFilePath: 'audit/records.json',
      defaultWorkspaceRelativePath: '.vscode/ai-provenance/records.json'
    });
    assert.equal(filePath, String.raw`E:\Lineagelens\audit\records.json`);
  }
);

test('normalizeLocalStorageLocation supports customFile and defaults safely', () => {
  assert.equal(normalizeLocalStorageLocation('customFile'), 'customFile');
  assert.equal(normalizeLocalStorageLocation('workspaceFile'), 'workspaceFile');
  assert.equal(normalizeLocalStorageLocation('unexpected'), 'globalState');
});
