import type { AgentAdapter, AgentAdapterInput, NormalizedAgentContext } from './types';
import { normalizeAgentContext } from './shared';
import { createCursorAdapter } from './cursor';
import { createClaudeCodeAdapter } from './claudeCode';
import { createCopilotAdapter } from './copilot';
import { createCodeiumAdapter } from './codeium';
import { createAiderAdapter } from './aider';
import { createContinueAdapter } from './continue';
import { createCodyAdapter } from './cody';
import { createAmazonQAdapter } from './amazonQ';
import { createGeminiCliAdapter } from './geminiCli';
import { createCodexCliAdapter } from './codex';
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
          const normalized = normalizeAgentContext(detected);
          if (!normalized) {
            continue;
          }
          candidates.push({
            ...normalized,
            adapterOrder: adapter.order
          });
        }
      } catch (error) {
        console.warn(`[AgentAdapterRegistry] Adapter "${adapter.name}" threw:`, error);
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
    createCopilotAdapter(),
    createCodeiumAdapter(),
    createAiderAdapter(),
    createContinueAdapter(),
    createCodyAdapter(),
    createAmazonQAdapter(),
    createGeminiCliAdapter(),
    createCodexCliAdapter(),
    createLegacyHeuristicAdapter()
  ]);
}

let defaultRegistry: AgentAdapterRegistry | undefined;

export function detectAgentContext(input: AgentAdapterInput): NormalizedAgentContext | null {
  defaultRegistry ??= createDefaultAgentAdapterRegistry();
  return defaultRegistry.detect(input);
}
