export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export type StructuredLogEntry = {
  level: LogLevel;
  message: string;
  timestamp: string;
  sessionId: string;
  [key: string]: unknown;
};

export class StructuredLogger {
  private readonly sessionId: string;

  public constructor(
    private readonly sink: (line: string) => void,
    sessionId?: string
  ) {
    this.sessionId = sessionId ?? generateSessionId();
  }

  public debug(message: string, fields?: Record<string, unknown>): void {
    this.write('debug', message, fields);
  }

  public info(message: string, fields?: Record<string, unknown>): void {
    this.write('info', message, fields);
  }

  public warn(message: string, fields?: Record<string, unknown>): void {
    this.write('warn', message, fields);
  }

  public error(message: string, fields?: Record<string, unknown>): void {
    this.write('error', message, fields);
  }

  public plain(message: string): void {
    this.sink(message);
  }

  private write(level: LogLevel, message: string, fields?: Record<string, unknown>): void {
    const entry: StructuredLogEntry = {
      level,
      message,
      timestamp: new Date().toISOString(),
      sessionId: this.sessionId,
      ...fields
    };

    try {
      this.sink(JSON.stringify(entry));
    } catch {
      this.sink('[' + level.toUpperCase() + '] ' + message);
    }
  }
}

function generateSessionId(): string {
  const hex = Array.from({ length: 8 }, () =>
    Math.floor(Math.random() * 256).toString(16).padStart(2, '0')
  ).join('');
  return 'sess-' + hex;
}
