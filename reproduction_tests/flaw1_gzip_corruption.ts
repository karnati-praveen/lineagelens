import assert from 'node:assert/strict';
import * as http from 'node:http';
import * as zlib from 'node:zlib';
import { startLocalLlmProxy } from '../lineagelens-src/proxy';

/**
 * Flaw 1: Gzip Proxy Payload Corruption
 * Location: lineagelens-src/proxy.ts
 * Description: The local proxy collects raw binary chunks from response stream and calls
 * `rawBuffer.toString('utf8')` without checking for Content-Encoding: gzip/deflate.
 * This stores raw gzipped binary strings into rawBodyUtf8, causing downstream prompt
 * correlation and JSON parsing to fail.
 */
async function reproduceFlaw1GzipCorruption(): Promise<void> {
  console.log('--- Reproducing Flaw 1: Gzip Proxy Payload Corruption ---');

  const upstreamPayload = JSON.stringify({
    id: 'chatcmpl-test-gzip',
    object: 'chat.completion',
    choices: [{ message: { role: 'assistant', content: 'function add(a, b) { return a + b; }' } }]
  });
  const gzippedPayload = zlib.gzipSync(upstreamPayload);

  // 1. Upstream HTTP server returning Content-Encoding: gzip
  const upstreamServer = http.createServer((_req, res) => {
    res.writeHead(200, {
      'Content-Type': 'application/json',
      'Content-Encoding': 'gzip'
    });
    res.end(gzippedPayload);
  });

  await new Promise<void>((resolve) => upstreamServer.listen(0, '127.0.0.1', resolve));
  const upstreamPort = (upstreamServer.address() as { port: number }).port;

  // 2. Start local LLM proxy with regex host patterns
  const proxyRuntime = await startLocalLlmProxy({
    port: 0,
    hostPatterns: [/127\.0\.0\.1/, /api\.openai\.com/]
  });

  try {
    // Make request through proxy to gzipped server
    await new Promise<void>((resolve, reject) => {
      const reqOptions: http.RequestOptions = {
        hostname: '127.0.0.1',
        port: proxyRuntime.port,
        path: `http://127.0.0.1:${upstreamPort}/v1/chat/completions`,
        method: 'POST',
        headers: {
          'Host': 'api.openai.com',
          'Content-Type': 'application/json'
        }
      };

      const req = http.request(reqOptions, (res) => {
        res.on('data', () => {});
        res.on('end', () => resolve());
      });
      req.on('error', reject);
      req.write(JSON.stringify({ model: 'gpt-4o', messages: [{ role: 'user', content: 'write add' }] }));
      req.end();
    });

    await new Promise((r) => setTimeout(r, 200));

    const pairs = proxyRuntime.getRecentPairs();
    assert.strictEqual(pairs.length, 1, 'Expected 1 proxy request/response pair captured');

    const capturedPair = pairs[0];
    const rawBodyUtf8 = capturedPair.response?.rawBodyUtf8 ?? '';

    console.log('Captured pair rawBodyUtf8 snippet:', JSON.stringify(rawBodyUtf8.slice(0, 30)));

    // ASSERTION FOR EXPECTED CORRECT BEHAVIOR:
    // rawBodyUtf8 must contain decompressed text ("function add")
    assert.ok(
      rawBodyUtf8.includes('function add'),
      `[FLAW DEMONSTRATED] rawBodyUtf8 does NOT contain decompressed UTF-8 text! Payload is binary garbage starting with gzipped bytes: ${JSON.stringify(rawBodyUtf8.slice(0, 20))}`
    );
  } finally {
    await proxyRuntime.stop();
    upstreamServer.close();
  }
}

if (require.main === module) {
  reproduceFlaw1GzipCorruption().catch((err) => {
    console.error('Test Failed as Expected (Demonstrating Flaw 1):');
    console.error(err.message);
    process.exit(1);
  });
}

export { reproduceFlaw1GzipCorruption };
