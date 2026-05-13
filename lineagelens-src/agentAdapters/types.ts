import type { PromptCorrelationResult } from '../correlation';

export type AgentOperationType = 'edit' | 'refactor' | 'test-fix' | 'explain' | 'multi-file-run' | 'chat' | 'unknown';

export type AgentCapability =
  | 'tool-name'
  | 'provider'
  | 'model'
  | 'session-id'
  | 'conversation-id'
  | 'run-id'
  | 'request-id'
  | 'user-agent'
  | 'headers'
  | 'prompt-body'
  | 'response-body'
  | 'file-context'
  | 'file-diff'
  | 'workspace';

export type AgentEvidence = {
  source: 'header' | 'user-agent' | 'payload' | 'system-prompt' | 'routing' | 'response' | 'heuristic';
  field: string;
  value: string;
  weight: number;
  note?: string;
};

export type AgentSessionKind = 'agentic' | 'assistant' | 'cli' | 'unknown';

export type NormalizedAgentContext = {
  toolName: string | null;
  provider: string | null;
  sessionId: string | null;
  conversationId: string | null;
  runId: string | null;
  modelName: string | null;
  userAgent: string | null;
  workspaceHint: string | null;
  operationType: AgentOperationType;
  confidence: number;
  evidence: AgentEvidence[];
  adapterName: string;
  matchSource: 'adapter' | 'heuristic';
  sessionKind: AgentSessionKind;
  host: string | null;
  sessionSignature: string;
  detectedAtIso: string;
};

export type AgentAdapterInput = {
  timestampIso: string;
  filePath: string;
  fileLanguageId: string;
  workspaceHint: string | null;
  insertedText: string;
  correlation: PromptCorrelationResult;
};

export interface AgentAdapter {
  readonly name: string;
  readonly order: number;
  readonly capabilities: readonly AgentCapability[];
  detect(input: AgentAdapterInput): NormalizedAgentContext | undefined;
}
