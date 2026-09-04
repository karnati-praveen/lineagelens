import assert from 'node:assert/strict';
import * as vscode from 'vscode';
import { ProvenanceReviewerService } from '../lineagelens-src/reviewer';

/**
 * Flaw 4: Reviewer Custom API URL Fallback Bug
 * Location: lineagelens-src/reviewer.ts
 * Description: When provider is set to 'openai-compatible', REVIEWER_ENDPOINTS['openai-compatible'] is undefined.
 * The apiUrl lookup evaluates (ep?.apiUrl) ?? legacyConfig.get('reviewer.apiUrl', DEFAULT_REVIEWER_API_URL),
 * completely ignoring newConfig.get('reviewer.apiUrl'). If legacy config is missing, it falls back to
 * hardcoded OpenAI endpoint ("https://api.openai.com/v1/chat/completions").
 */
async function reproduceFlaw4ReviewerUrlFallback(): Promise<void> {
  console.log('--- Reproducing Flaw 4: Reviewer Custom API URL Fallback Bug ---');

  const customEndpoint = 'https://custom-llm-proxy.corp.internal/v1/chat/completions';

  // Mock workspace.getConfiguration
  const origGetConfig = vscode.workspace.getConfiguration;
  (vscode.workspace as any).getConfiguration = (section?: string) => {
    if (section === 'lineagelens') {
      return {
        get: <T>(key: string, defaultValue: T): T => {
          if (key === 'reviewer.provider') return 'openai-compatible' as any;
          if (key === 'reviewer.apiUrl') return customEndpoint as any;
          return defaultValue;
        }
      };
    }
    if (section === 'aiInsertionDetector') {
      return {
        get: <T>(key: string, defaultValue: T): T => {
          if (key === 'reviewer.provider') return '' as any;
          // Legacy config does NOT have custom apiUrl set
          if (key === 'reviewer.apiUrl') return defaultValue;
          return defaultValue;
        }
      };
    }
    return origGetConfig(section);
  };

  try {
    const dummyContext = {
      secrets: {
        get: () => Promise.resolve('test-secret-key'),
        store: () => Promise.resolve()
      }
    } as unknown as vscode.ExtensionContext;

    const reviewerService = new ProvenanceReviewerService(dummyContext, () => {});

    // Inspect reviewer config resolved by private method getReviewerConfig
    const config = (reviewerService as any).getReviewerConfig();

    console.log('Resolved Reviewer Provider:', config.provider);
    console.log('Resolved Reviewer API URL:', config.apiUrl);

    // ASSERTION FOR EXPECTED CORRECT BEHAVIOR:
    // config.apiUrl should resolve to customEndpoint ("https://custom-llm-proxy.corp.internal/v1/chat/completions")
    assert.strictEqual(
      config.apiUrl,
      customEndpoint,
      `[FLAW DEMONSTRATED] Custom reviewer API URL '${customEndpoint}' was ignored! Fell back to hardcoded default: '${config.apiUrl}'`
    );
  } finally {
    (vscode.workspace as any).getConfiguration = origGetConfig;
  }
}

if (require.main === module) {
  reproduceFlaw4ReviewerUrlFallback().catch((err) => {
    console.error('Test Failed as Expected (Demonstrating Flaw 4):');
    console.error(err.message);
    process.exit(1);
  });
}

export { reproduceFlaw4ReviewerUrlFallback };
