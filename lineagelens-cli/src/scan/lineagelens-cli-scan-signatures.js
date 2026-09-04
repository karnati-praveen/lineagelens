'use strict';

// Tool signatures for retroactive attribution.
//
// Every signature here matches something an AI coding tool *itself* writes into
// git history — a commit trailer, a bot author identity, or a generated message
// line. That is why matches are classed `declared` evidence: the tool declared
// its own involvement. Nothing in this file guesses.
//
// Each signature: { tool, patterns, field }
//   field 'trailer' — matched against the commit body (trailers live there)
//   field 'subject' — matched against the commit subject line
//   field 'identity' — matched against "Name <email>" of author and committer
//
// Adding a signature is the whole extension point for supporting a new tool.

/** @typedef {{tool: string, field: 'trailer'|'subject'|'identity', patterns: RegExp[]}} ToolSignature */

/** @type {ToolSignature[]} */
const TOOL_SIGNATURES = [
  {
    tool: 'Claude Code',
    field: 'trailer',
    patterns: [
      /^co-authored-by:\s*claude\b/im,
      /generated with \[?claude code\]?/i,
      /^\s*🤖 generated with/im,
    ],
  },
  {
    tool: 'Claude Code',
    field: 'identity',
    patterns: [/claude(?:-code)?\s*<[^>]*@anthropic\.com>/i],
  },
  {
    tool: 'GitHub Copilot',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*copilot\b/im, /^co-authored-by:[^<]*<[^>]*copilot[^>]*>/im],
  },
  {
    tool: 'GitHub Copilot',
    field: 'identity',
    patterns: [/copilot(?:-swe-agent)?\[bot\]/i, /<\d+\+copilot[^>]*@users\.noreply\.github\.com>/i],
  },
  {
    tool: 'Cursor',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*cursor(?:agent)?\b/im],
  },
  {
    tool: 'Cursor',
    field: 'identity',
    patterns: [/cursoragent/i, /<[^>]*@cursor\.(?:com|sh)>/i],
  },
  {
    tool: 'OpenAI Codex',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*codex\b/im, /generated (?:with|by) (?:openai )?codex/i],
  },
  {
    tool: 'OpenAI Codex',
    field: 'identity',
    patterns: [/<[^>]*codex[^>]*@openai\.com>/i, /chatgpt-codex-connector/i],
  },
  {
    tool: 'Gemini CLI',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*gemini\b/im, /generated with \[?gemini(?: cli)?\]?/i],
  },
  {
    tool: 'Aider',
    field: 'subject',
    patterns: [/^aider:/i],
  },
  {
    tool: 'Aider',
    field: 'identity',
    patterns: [/\(aider\)/i],
  },
  {
    tool: 'Devin',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*devin\b/im],
  },
  {
    tool: 'Devin',
    field: 'identity',
    patterns: [/devin-ai-integration\[bot\]/i],
  },
  {
    tool: 'Windsurf',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*windsurf\b/im, /generated with \[?windsurf\]?/i],
  },
  {
    tool: 'Sourcegraph Cody',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*cody\b/im],
  },
  {
    tool: 'Google Jules',
    field: 'identity',
    patterns: [/google-labs-jules\[bot\]/i],
  },
  {
    tool: 'Amp',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*amp\b/im, /generated with \[?amp\]?/i],
  },
  {
    tool: 'Continue',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*continue\b/im],
  },
  {
    tool: 'Cline',
    field: 'trailer',
    patterns: [/^co-authored-by:\s*cline\b/im],
  },
];

// Model names an AI tool may name in its own trailer. Only reported when the
// commit is already declared-AI; a model name alone attributes nothing.
const MODEL_PATTERNS = [
  /\b(claude-(?:opus|sonnet|haiku)-[\w.-]+)\b/i,
  /\b(gpt-[\w.-]+)\b/i,
  /\b(o[34](?:-mini)?(?:-[\w.]+)?)\b/i,
  /\b(gemini-[\w.-]+)\b/i,
  /\b(deepseek-[\w.-]+)\b/i,
  /\b(qwen[\w.-]*coder[\w.-]*)\b/i,
];

// Repo-level artifacts that prove an AI tool is *configured* for this repo.
// These attribute no lines. They are used to detect a coverage gap: tooling is
// configured but nothing in history declares it, so attribution is incomplete.
const TOOL_ARTIFACTS = [
  { tool: 'Claude Code', paths: ['CLAUDE.md', '.claude/'] },
  { tool: 'Cursor', paths: ['.cursorrules', '.cursor/'] },
  { tool: 'GitHub Copilot', paths: ['.github/copilot-instructions.md'] },
  { tool: 'Windsurf', paths: ['.windsurfrules', '.windsurf/'] },
  { tool: 'Aider', paths: ['.aider.conf.yml', '.aider.chat.history.md'] },
  { tool: 'Continue', paths: ['.continue/'] },
  { tool: 'Cline', paths: ['.clinerules', '.clinerules/'] },
  { tool: 'Roo Code', paths: ['.roo/', '.roomodes'] },
  { tool: 'OpenAI Codex', paths: ['AGENTS.md', '.codex/'] },
  { tool: 'Gemini CLI', paths: ['GEMINI.md', '.gemini/'] },
  { tool: 'Amp', paths: ['AGENT.md'] },
];

module.exports = { TOOL_SIGNATURES, MODEL_PATTERNS, TOOL_ARTIFACTS };
