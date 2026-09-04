import assert from 'node:assert/strict';
import { countApproximateLines, extractInsertedChunksFromDiff } from '../lineagelens-src/extension';

/**
 * Flaw 5: Line Threshold Enter Keystroke Flaw
 * Location: lineagelens-src/extension.ts
 * Description: countApproximateLines("\n") evaluates endsInNewline = true and returns 1.
 * When a developer presses Enter in VS Code editor (text = "\n"), extractInsertedChunksFromDiff calculates
 * netAddedLines = 1. If lineThreshold is configured to 1, this single Enter key press triggers full AI insertion
 * detection for blank line newlines.
 */
async function reproduceFlaw5LineThresholdEnter(): Promise<void> {
  console.log('--- Reproducing Flaw 5: Line Threshold Enter Keystroke Flaw ---');

  const enterKeystrokeChange = [
    {
      rangeOffset: 10,
      rangeLength: 0,
      text: '\n'
    }
  ];

  const diffResult = extractInsertedChunksFromDiff('const x = 1;', enterKeystrokeChange);
  const standaloneLines = countApproximateLines('\n');

  console.log('countApproximateLines("\\n") returned:', standaloneLines);
  console.log('extractInsertedChunksFromDiff netAddedLines returned:', diffResult.netAddedLines);

  // ASSERTION FOR EXPECTED CORRECT BEHAVIOR:
  // Pressing Enter to insert a newline separator should result in 0 added code lines.
  assert.strictEqual(
    diffResult.netAddedLines,
    0,
    `[FLAW DEMONSTRATED] Pressing Enter key press returned netAddedLines = ${diffResult.netAddedLines} instead of 0! This triggers false-positive AI insertion detection when lineThreshold = 1.`
  );
}

if (require.main === module) {
  reproduceFlaw5LineThresholdEnter().catch((err) => {
    console.error('Test Failed as Expected (Demonstrating Flaw 5):');
    console.error(err.message);
    process.exit(1);
  });
}

export { reproduceFlaw5LineThresholdEnter };
