import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

export const HOOK_EVENTS_DIR = path.join(os.homedir(), '.lineagelens');
export const HOOK_EVENTS_FILE = path.join(HOOK_EVENTS_DIR, 'hook-events.jsonl');
export const HOOK_CAPTURE_SCRIPT = path.join(HOOK_EVENTS_DIR, 'hook-capture.js');

const DEFAULT_RETENTION_MS = 5 * 60_000;

export type HookToolInput = {
  file_path?: string;
  path?: string;
  content?: string;
  new_content?: string;
  new_string?: string;
  edits?: Array<{ new_string?: string; [key: string]: unknown }>;
  [key: string]: unknown;
};

export type HookEvent = {
  session_id: string | null;
  hook_event_name: string | null;
  tool_name: string | null;
  tool_input: HookToolInput;
  tool_response: Record<string, unknown> | null;
  capturedAtIso: string;
};

export type StoredHookEvent = HookEvent & { capturedAtMs: number };

export class ClaudeCodeHookListener {
  private readonly events: StoredHookEvent[] = [];
  private fileOffset = 0;
  private watcher: fs.FSWatcher | undefined;
  private cleanupTimer: NodeJS.Timeout | undefined;

  public constructor(private readonly retentionMs = DEFAULT_RETENTION_MS) {}

  public start(): void {
    try {
      const stat = fs.statSync(HOOK_EVENTS_FILE);
      this.fileOffset = stat.size;
    } catch {
      this.fileOffset = 0;
    }

    this.watchDir();
    this.cleanupTimer = setInterval(() => this.pruneExpired(), 60_000);
  }

  public stop(): void {
    this.watcher?.close();
    this.watcher = undefined;
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = undefined;
    }
    this.events.length = 0;
  }

  public getRecentEvents(windowMs: number): StoredHookEvent[] {
    const cutoff = Date.now() - windowMs;
    return this.events.filter((e) => e.capturedAtMs >= cutoff);
  }

  private watchDir(): void {
    try {
      fs.mkdirSync(HOOK_EVENTS_DIR, { recursive: true });
      this.watcher = fs.watch(HOOK_EVENTS_DIR, { persistent: false }, (_eventType, filename) => {
        if (filename === 'hook-events.jsonl') {
          this.readNewLines();
        }
      });
    } catch {
      // directory watch is best-effort
    }
  }

  private readNewLines(): void {
    try {
      const stat = fs.statSync(HOOK_EVENTS_FILE);
      if (stat.size <= this.fileOffset) {
        return;
      }

      const fd = fs.openSync(HOOK_EVENTS_FILE, 'r');
      const buffer = Buffer.alloc(stat.size - this.fileOffset);
      fs.readSync(fd, buffer, 0, buffer.length, this.fileOffset);
      fs.closeSync(fd);
      this.fileOffset = stat.size;

      for (const line of buffer.toString('utf8').split('\n')) {
        const trimmed = line.trim();
        if (!trimmed) {
          continue;
        }
        try {
          const event = JSON.parse(trimmed) as HookEvent;
          const capturedAtMs = event.capturedAtIso
            ? new Date(event.capturedAtIso).getTime()
            : Date.now();
          this.events.push({ ...event, capturedAtMs });
        } catch {
          // skip malformed line
        }
      }
    } catch {
      // file may not exist yet
    }
  }

  private pruneExpired(): void {
    const cutoff = Date.now() - this.retentionMs;
    while (this.events.length > 0 && (this.events[0]?.capturedAtMs ?? 0) < cutoff) {
      this.events.shift();
    }
  }
}

export function extractHookEventContent(event: HookEvent): string {
  const input = event.tool_input;
  if (typeof input.content === 'string') {
    return input.content;
  }
  if (typeof input.new_content === 'string') {
    return input.new_content;
  }
  if (typeof input.new_string === 'string') {
    return input.new_string;
  }
  if (Array.isArray(input.edits)) {
    return input.edits
      .map((edit) => (typeof edit.new_string === 'string' ? edit.new_string : ''))
      .join('\n');
  }
  return '';
}

export function extractHookEventFilePath(event: HookEvent): string {
  const input = event.tool_input;
  return typeof input.file_path === 'string' ? input.file_path : input.path ?? '';
}

export function generateHookCaptureScript(): string {
  return [
    '#!/usr/bin/env node',
    '// LineageLens — Claude Code hook capture script',
    '// Writes tool-use events to ~/.lineagelens/hook-events.jsonl for provenance correlation.',
    "const fs = require('fs');",
    "const os = require('os');",
    "const path = require('path');",
    "let raw = '';",
    "process.stdin.setEncoding('utf8');",
    "process.stdin.on('data', (c) => { raw += c; });",
    "process.stdin.on('end', () => {",
    '  try {',
    '    const event = JSON.parse(raw);',
    "    const dir = path.join(os.homedir(), '.lineagelens');",
    "    fs.mkdirSync(dir, { recursive: true });",
    '    const entry = Object.assign({}, event, { capturedAtIso: new Date().toISOString() });',
    String.raw`    fs.appendFileSync(path.join(dir, 'hook-events.jsonl'), JSON.stringify(entry) + '\n');`,
    '  } catch (_) {',
    '    // never block Claude Code on errors',
    '  }',
    '});',
    ''
  ].join('\n');
}

export function generateHookConfigSnippet(scriptPath: string): string {
  const config = {
    hooks: {
      PostToolUse: [
        {
          matcher: 'Write|Edit|MultiEdit',
          hooks: [{ type: 'command', command: 'node ' + scriptPath }]
        }
      ]
    }
  };
  return JSON.stringify(config, null, 2);
}
