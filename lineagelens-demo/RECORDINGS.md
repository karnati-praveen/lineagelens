# Recording shot list — hackathon cut

Three clips make the hackathon video real. Record at **1920×1080, 30fps**
(OBS default `ada`, x264, CRF ~20 is fine), a clean browser/VS Code profile,
light theme OFF (the video's dark world matches the product), cursor visible.
Save each clip into `public/recordings/` with the exact file name — the
composition picks it up automatically on the next render (no code change).

Aim for the durations below; the timeline holds a little slack on either side.
Do each flow once for practice, then record. One take each is enough — trim
nothing, the timeline shows what you show.

## Setup (before any clip)

```bash
# 1. Backend + proxy up (Quickstart or your usual way); dashboard at :8787
# 2. A Llama server on any OpenAI-compatible endpoint. Simplest:
ollama pull llama3.2          # or llama3.1:8b — anything with a visible model name
ollama serve
# 3. Point the proxy at it (proxy config: OPENAI upstream → http://localhost:11434/v1)
# 4. Seed one clean repo folder for the demo (few files, python or ts)
```

## R1 — `public/recordings/llama-capture.mp4` (~35s)

**The claim on screen: Llama writes code, LineageLens writes the receipt.**

1. Terminal A (3s): `ollama list` — the Llama model name visible. Keep it on
   screen a beat; this is the "real Llama, local" proof.
2. Terminal B (10s): type, don't paste:
   ```bash
   export OPENAI_BASE_URL=http://localhost:8788/v1
   curl -s localhost:8788/v1/chat/completions -d '{
     "model": "llama3.2",
     "messages": [{"role":"user","content":"Create utils/health.py with a health() function returning ok"}],
     "tools": [{"type":"function","function":{"name":"write_file",
       "parameters":{"type":"object","properties":{"path":{},"content":{}}}}}]
   }' | python -m json.tool
   ```
   The response shows `"model": "llama3.2"` and the `write_file` tool_call.
3. Browser (20s): open the dashboard → the new record: **model column says
   llama3.2**, prompt text visible, edit status **applied**. Hover/scroll so
   all three land in frame. End on the record detail.

## R2 — `public/recordings/ide-receipt.mp4` (~30s)

**The claim on screen: every AI edit carries its receipt into the editor.**

1. VS Code opens the file R1 created (~5s): the CodeLens `AI capture` above the
   inserted block is in frame.
2. Hover the block (~8s): the receipt shows prompt, model, confidence.
3. Review flow (~8s): mark it *reviewed* (or needs-changes) from the receipt.
4. Pre-commit gate (~9s): run *LineageLens: Check AI Changes Before Commit* —
   the gate output listing the AI change (and the unreviewed one blocking).

## R3 — `public/recordings/capsule-verify.mp4` (~20s)

**The claim on screen: evidence that outlives the company.**

1. Dashboard (~6s): *Export Evidence Capsule* → download starts/completes.
2. Clean terminal (~14s):
   ```bash
   pip install lineagelens-verifier -q
   lineagelens-verifier ~/Downloads/capsule.lllpkg
   ```
   End on `capsule: VALID` / `exit 0`. Leave it on screen two beats.

## After recording

```bash
cd lineagelens-demo
npm run build:hackathon     # out/lineagelens-hackathon.mp4 — the real cut
```

Until a clip exists, its scene renders a styled placeholder naming the missing
file — the video is never broken, just honest about what's pending.

Clips are gitignored (`public/recordings/*.mp4`): CI renders the placeholder
cut; render the final locally (above) or commit the clips if you accept the
repo size cost.
