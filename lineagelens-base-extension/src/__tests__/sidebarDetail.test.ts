import { buildDetailPanel } from '../sidebar';
import { CaptureRecord } from '../store';

function record(overrides: Partial<CaptureRecord> = {}): CaptureRecord {
  return {
    id: 'cap-1',
    timestamp: '2026-09-13T11:30:00.000Z',
    filePath: 'C:/repo/src/category.ts',
    fileName: 'category.ts',
    language: 'typescript',
    insertedCode: 'export const x = 1;\nexport const y = 2;',
    linesAdded: 2,
    workspaceFolder: 'repo',
    confidence: 0.95,
    source: 'ai',
    reviewState: 'unreviewed',
    riskSignals: [],
    ...overrides,
  };
}

describe('capture detail webview', () => {
  it('emits parseable JavaScript', () => {
    const panel = { webview: { cspSource: 'vscode-webview://lineagelens-test', html: '' } } as any;
    buildDetailPanel(panel, record());
    const scripts = [...panel.webview.html.matchAll(/<script nonce="[^"]+">([\s\S]*?)<\/script>/g)]
      .map(m => m[1]);

    expect(scripts.length).toBeGreaterThan(0);
    for (const script of scripts) {
      expect(() => new Function(script)).not.toThrow();
    }
  });
});
