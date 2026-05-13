import type { AgentOperationType, AgentSessionKind, NormalizedAgentContext } from './types';

const VALID_OPERATION_TYPES = new Set<AgentOperationType>([
  'edit', 'refactor', 'test-fix', 'explain', 'multi-file-run', 'chat', 'unknown'
]);

const VALID_SESSION_KINDS = new Set<AgentSessionKind>([
  'agentic', 'assistant', 'cli', 'unknown'
]);

const VALID_MATCH_SOURCES = new Set<string>(['adapter', 'heuristic']);

export function assertContextInvariants(
  context: NormalizedAgentContext,
  registeredAdapterNames: ReadonlySet<string>
): void {
  if (!Number.isFinite(context.confidence) || context.confidence < 0 || context.confidence > 1) {
    throw new Error(
      `[LineageLens invariant] confidence out of bounds: ${context.confidence} (adapter="${context.adapterName}")`
    );
  }

  if (!registeredAdapterNames.has(context.adapterName)) {
    throw new Error(
      `[LineageLens invariant] adapter "${context.adapterName}" not in registry (registered: ${[...registeredAdapterNames].join(', ')})`
    );
  }

  if (!VALID_OPERATION_TYPES.has(context.operationType)) {
    throw new Error(
      `[LineageLens invariant] unknown operationType "${context.operationType}" (adapter="${context.adapterName}")`
    );
  }

  if (!VALID_SESSION_KINDS.has(context.sessionKind)) {
    throw new Error(
      `[LineageLens invariant] unknown sessionKind "${context.sessionKind}" (adapter="${context.adapterName}")`
    );
  }

  if (!VALID_MATCH_SOURCES.has(context.matchSource)) {
    throw new Error(
      `[LineageLens invariant] unknown matchSource "${context.matchSource}" (adapter="${context.adapterName}")`
    );
  }

  for (const ev of context.evidence) {
    if (!Number.isFinite(ev.weight) || ev.weight < 0) {
      throw new Error(
        `[LineageLens invariant] evidence weight out of range: ${ev.weight} (field="${ev.field}", adapter="${context.adapterName}")`
      );
    }
  }
}

export function assertFallbackNotSelectedOverSpecialist(
  selected: NormalizedAgentContext,
  candidates: ReadonlyArray<NormalizedAgentContext & { adapterOrder: number }>
): void {
  if (selected.adapterName !== 'legacy-heuristic') return;

  const specialistWithEqualOrHigherConfidence = candidates.find(
    (c) => c.adapterName !== 'legacy-heuristic' && c.confidence >= selected.confidence
  );

  if (specialistWithEqualOrHigherConfidence) {
    throw new Error(
      `[LineageLens invariant] legacy-heuristic selected over specialist "${specialistWithEqualOrHigherConfidence.adapterName}" ` +
      `with equal/higher confidence (specialist=${specialistWithEqualOrHigherConfidence.confidence}, fallback=${selected.confidence})`
    );
  }
}

export function assertAdapterOrdersUnique(adapters: ReadonlyArray<{ name: string; order: number }>): void {
  const seen = new Map<number, string>();
  for (const adapter of adapters) {
    const existing = seen.get(adapter.order);
    if (existing !== undefined) {
      throw new Error(
        `[LineageLens invariant] duplicate adapter order ${adapter.order} used by both "${existing}" and "${adapter.name}"`
      );
    }
    seen.set(adapter.order, adapter.name);
  }
}
