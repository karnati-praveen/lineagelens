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
  showWarningMessage: async () => undefined as string | undefined,
  showInputBox: async (_options?: unknown): Promise<string | undefined> => undefined,
  showQuickPick: async (_items: unknown, _options?: unknown): Promise<unknown> => undefined,
  createOutputChannel: () => ({ appendLine: () => {}, dispose: () => {} }),
  setStatusBarMessage: () => ({ dispose: () => {} }),
};

export const StatusBarAlignment = { Left: 1, Right: 2 };
export const TextDocumentChangeReason = { Undo: 1, Redo: 2 };
export const TreeItemCollapsibleState = { None: 0, Collapsed: 1, Expanded: 2 };

export class TreeItem {
  label?: string;
  collapsibleState?: number;
  constructor(label?: string, collapsibleState?: number) {
    this.label = label;
    this.collapsibleState = collapsibleState;
  }
}

export class ThemeIcon {
  constructor(public id: string) {}
}

export class EventEmitter<T> {
  event = (_listener: (e: T) => any) => ({ dispose: () => {} });
  fire(_data?: T): void {}
  dispose(): void {}
}

export const Uri = {
  file: (p: string) => ({ fsPath: p, scheme: 'file', toString: () => `file://${p}` }),
  parse: (s: string) => ({ fsPath: s, scheme: 'file', toString: () => s }),
};

export class Position {
  constructor(public line: number, public character: number) {}
}

export class Range {
  constructor(public start: Position, public end: Position) {}
}

export class Selection extends Range {}

export class CodeLens {
  constructor(public range: Range, public command?: any) {}
}

export class Hover {
  constructor(public contents: any, public range?: Range) {}
}

export class MarkdownString {
  constructor(public value: string = '') {}
  appendMarkdown(val: string) { this.value += val; return this; }
  appendCodeblock(code: string, lang?: string) { this.value += `\n\`\`\`${lang}\n${code}\n\`\`\`\n`; return this; }
}

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
