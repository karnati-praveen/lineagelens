# Lightweight Adapters

LineageLens supports three practical operating profiles:

- `base`: records are stored locally as a JSON file. No backend or services required.
- `plus`: the backend stores provenance without requiring Neo4j or vector search.
- `max`: the backend enables graph lineage and vector search.

The lightweight adapter boundary is for tools that can identify an insertion event without depending on any editor API. Use it when you want to create provenance from a CLI, a script, a CI job, a manual reviewer flow, or any non-editor environment.

## Recommended Shape

The shared backend normalizer accepts both the full provenance record shape and a smaller event payload. At minimum, provide:

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

Use [src/lightweightRecord.ts](../src/lightweightRecord.ts) when you want a pure helper with no editor API dependencies.

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

That helper returns the same provider-agnostic event contract used by the capture layer, so LineageLens Plus can accept it without any extra adapter-specific code.

## Backend Behavior

In `plus` mode:

- Neo4j startup is optional.
- Search falls back to keyword matching when vector search is disabled.
- Ingest responses include warnings when graph lineage is unavailable.

In `max` mode:

- Neo4j lineage is initialized at startup.
- Vector search is enabled.
- The same ingest contract still works, but more features are available.
