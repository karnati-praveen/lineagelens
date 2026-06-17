export const workspace = {
  getConfiguration: (_section?: string) => ({
    get: <T>(_key: string, defaultValue: T): T => defaultValue,
  }),
  getWorkspaceFolder: (_uri: unknown) => null,
  onDidChangeTextDocument: (_listener: unknown) => ({ dispose: () => {} }),
};

export const window = {
  createStatusBarItem: () => ({
    text: '',
    tooltip: '',
    show: () => {},
    hide: () => {},
    dispose: () => {},
  }),
  showInformationMessage: async () => undefined as string | undefined,
  showErrorMessage: async () => undefined as string | undefined,
  showInputBox: async (_options?: unknown): Promise<string | undefined> => undefined,
  showQuickPick: async (_items: unknown, _options?: unknown): Promise<unknown> => undefined,
};

export const StatusBarAlignment = { Left: 1, Right: 2 };
export const TextDocumentChangeReason = { Undo: 1, Redo: 2 };
export const Uri = {
  file: (p: string) => ({ fsPath: p, scheme: 'file' }),
};
export class Disposable {
  constructor(private fn: () => void) {}
  dispose() { this.fn(); }
}
export const extensions = {
  all: [] as { id: string; isActive: boolean }[],
};
export const env = {
  clipboard: {
    readText: () => Promise.resolve(''),
    writeText: () => Promise.resolve(),
  },
};
