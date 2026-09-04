import assert from 'node:assert/strict';
import * as vscode from 'vscode';
import { handleTextDocumentChange, previousDocumentTexts } from '../lineagelens-src/extension';

/**
 * Flaw 2: Document Change Event Race Condition
 * Location: lineagelens-src/extension.ts
 * Description: previousDocumentTexts is updated in a `finally` block AFTER all async operations
 * (ensureRuntimeInitialized, captureContextSnapshot, correlateInsertionWithProxyRequest, persistProvenanceRecord).
 * This causes state desynchronization because any concurrent edit or state inspection during the async phase
 * sees stale document text.
 */
async function reproduceFlaw2DocChangeRace(): Promise<void> {
  console.log('--- Reproducing Flaw 2: Document Change Event Race Condition ---');

  const testUri = vscode.Uri.file('/path/to/test_race_file.ts');
  const key = testUri.toString();

  previousDocumentTexts.clear();
  const textV0 = 'const version = 0;\nfunction foo() {}';
  previousDocumentTexts.set(key, textV0);

  const textV1 = 'const version = 1;\nfunction foo() {}\nconsole.log("edit1");';
  const mockDocument = {
    uri: testUri,
    getText: () => textV1,
    positionAt: () => ({ line: 0, character: 0 })
  };

  const changeEvent1: vscode.TextDocumentChangeEvent = {
    document: mockDocument as any,
    contentChanges: [
      {
        range: {} as any,
        rangeOffset: 0,
        rangeLength: 0,
        text: '\nconsole.log("edit1");'
      }
    ],
    reason: undefined
  };

  // Start processing document change event (which performs async tasks)
  const promise1 = handleTextDocumentChange(changeEvent1);

  // IMMEDIATELY query previousDocumentTexts while handleTextDocumentChange is paused at async tasks
  const textInMapDuringAsyncPhase = previousDocumentTexts.get(key);

  console.log('Document text in previousDocumentTexts during async processing:', JSON.stringify(textInMapDuringAsyncPhase));

  await promise1;

  // ASSERTION FOR EXPECTED CORRECT BEHAVIOR:
  // previousDocumentTexts should be updated synchronously upon event handling to prevent state desynchronization.
  assert.strictEqual(
    textInMapDuringAsyncPhase,
    textV1,
    `[FLAW DEMONSTRATED] Race Condition! previousDocumentTexts was NOT updated synchronously upon receiving document change event. Stale text held in map during async phase: ${JSON.stringify(textInMapDuringAsyncPhase)}`
  );
}

if (require.main === module) {
  reproduceFlaw2DocChangeRace().catch((err) => {
    console.error('Test Failed as Expected (Demonstrating Flaw 2):');
    console.error(err.message);
    process.exit(1);
  });
}

export { reproduceFlaw2DocChangeRace };
