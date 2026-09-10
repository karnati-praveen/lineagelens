const vscode = {
  workspace: {
    getConfiguration: (_section?: string) => ({
      get: <T>(_key: string, defaultValue: T): T => defaultValue,
    }),
    getWorkspaceFolder: (_uri: unknown) => null,
    onDidChangeTextDocument: (_listener: unknown) => ({ dispose: () => {} }),
    findFiles: () => Promise.resolve([]),
    createFileSystemWatcher: () => ({
      onDidChange: (_listener: unknown) => ({ dispose: () => {} }),
      onDidCreate: (_listener: unknown) => ({ dispose: () => {} }),
      onDidDelete: (_listener: unknown) => ({ dispose: () => {} }),
      dispose: () => {},
    }),
  },
  window: {
    createStatusBarItem: () => ({
      text: '',
      tooltip: '',
      show: () => {},
      hide: () => {},
      dispose: () => {},
    }),
    showInformationMessage: () => Promise.resolve(undefined),
    showErrorMessage: () => Promise.resolve(undefined),
  },
  StatusBarAlignment: { Left: 1, Right: 2 },
  ConfigurationTarget: { Global: 1, Workspace: 2, WorkspaceFolder: 3 },
  TextDocumentChangeReason: { Undo: 1, Redo: 2 },
  Uri: {
    file: (p: string) => ({ fsPath: p, scheme: 'file' }),
  },
  Disposable: class {
    constructor(private fn: () => void) {}
    dispose() { this.fn(); }
  },
  extensions: {
    all: [] as { id: string; isActive: boolean }[],
    onDidChange: (_listener: unknown) => ({ dispose: () => {} }),
  },
  env: {
    appName: 'Visual Studio Code',
    clipboard: {
      readText: () => Promise.resolve(''),
      writeText: () => Promise.resolve(),
    },
  },
};

export = vscode;
