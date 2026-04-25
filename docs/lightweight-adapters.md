# Lightweight Adapters

LineageLens supports three practical operating profiles:

- `base`: everything stays inside VS Code storage or an optional workspace file.
- `plus`: the backend stores provenance without requiring Neo4j or vector search.
- `max`: the backend enables graph lineage and vector search.

The lightweight adapter boundary is for tools that can identify an insertion event without depending on VS Code internals. Use it when you want to create provenance from a CLI, a script, a manual reviewer flow, or another editor integration.

## Recommended Shape

The shared backend normalizer accepts both the full VS Code record shape and a smaller event payload. At minimum, provide:

- `eventId`
- `timestampIso`
- `filePath`
- `fileUri`
- `languageId`
- `insertedText`

Helpful optional fields include:

- `workspaceHint`
- `gitBranch`
- `insertedChunks`
- `contextSnapshot`
- `captureStatus`
- `promptStatus`
- `requestUuid`
- `fullPromptMessages`
- `rawModelResponse`
- `rawModelResponseBase64`
- `agentContext`

If the payload only contains the inserted code diff, the backend will normalize it as file-diff provenance when there is no prompt or response evidence.

## TypeScript Helper

Use [src/lightweightRecord.ts](../src/lightweightRecord.ts) when you want a pure helper that does not import VS Code APIs.

```ts
import { buildLightweightProvenanceRecord } from '../src/lightweightRecord';

const record = buildLightweightProvenanceRecord({
  eventId: crypto.randomUUID(),
  timestampIso: new Date().toISOString(),
  filePath: 'src/example.ts',
  fileUri: 'file:///workspace/src/example.ts',
  languageId: 'typescript',
  insertedText: 'const answer = 42;',
  promptStatus: 'not-captured',
  captureStatus: 'unavailable'
});
```

That helper returns the same provider-agnostic event contract used by the extension, so LineageLens Plus can accept it without any extra adapter-specific code.

## Backend Behavior

In `plus` mode:

- Neo4j startup is optional.
- Search falls back to keyword matching when vector search is disabled.
- Ingest responses include warnings when graph lineage is unavailable.

In `max` mode:

- Neo4j lineage is initialized at startup.
- Vector search is enabled.
- The same ingest contract still works, but more features are available.
