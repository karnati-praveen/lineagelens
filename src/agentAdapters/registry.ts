import type { AgentAdapter, AgentAdapterInput, NormalizedAgentContext } from './types';
import { normalizeAgentContext } from './shared';
import { createCursorAdapter } from './cursor';
import { createClaudeCodeAdapter } from './claudeCode';
import { createAiderAdapter } from './aider';
import { createLegacyHeuristicAdapter } from './legacy';

export class AgentAdapterRegistry {
  private readonly adapters: AgentAdapter[];

  public constructor(adapters: AgentAdapter[]) {
    this.adapters = [...adapters].sort((left, right) => left.order - right.order);
  }

  public detect(input: AgentAdapterInput): NormalizedAgentContext | null {
    const candidates: Array<NormalizedAgentContext & { adapterOrder: number }> = [];

    for (const adapter of this.adapters) {
      try {
        const detected = adapter.detect(input);
        if (detected) {
          const normalized = normalizeAgentContext(detected) ?? detected;
          candidates.push({
            ...normalized,
            adapterOrder: adapter.order
          });
        }
      } catch {
        // non-fatal; continue to next adapter
      }
    }

    if (candidates.length === 0) {
      return null;
    }

    candidates.sort((left, right) => {
      if (left.confidence !== right.confidence) {
        return right.confidence - left.confidence;
      }

      if (left.adapterOrder !== right.adapterOrder) {
        return left.adapterOrder - right.adapterOrder;
      }

      return left.adapterName.localeCompare(right.adapterName);
    });

    const best = candidates[0];
    if (!best) {
      return null;
    }

    const { adapterOrder: _adapterOrder, ...context } = best;
    return context;
  }

  public getCompatibilityMatrix(): AgentAdapterCompatibility[] {
    return this.adapters.map((adapter) => ({
      adapterName: adapter.name,
      order: adapter.order,
      capabilities: [...adapter.capabilities]
    }));
  }
}

export type AgentAdapterCompatibility = {
  adapterName: string;
  order: number;
  capabilities: string[];
};

export function createDefaultAgentAdapterRegistry(): AgentAdapterRegistry {
  return new AgentAdapterRegistry([
    createCursorAdapter(),
    createClaudeCodeAdapter(),
    createAiderAdapter(),
    createLegacyHeuristicAdapter()
  ]);
}

export function detectAgentContext(input: AgentAdapterInput): NormalizedAgentContext | null {
  return createDefaultAgentAdapterRegistry().detect(input);
}
