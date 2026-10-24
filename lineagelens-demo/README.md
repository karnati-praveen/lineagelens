# lineagelens-demo

Remotion project that renders the LineageLens product videos:

- **Marketing cut** — 41 seconds, 1230 frames at 30fps, two aspect ratios
  (`LineageLensDemo`, `LineageLensDemoSquare`)
- **Hackathon cut** — ~2 minutes (`HackathonDemo`): Llama out of the box →
  live capture proof → editor receipts → offline-verifiable Evidence Capsule

The **source** is committed (CI renders it via `.github/workflows/demo-video.yml`);
`out/`, `node_modules/`, and real screen recordings stay gitignored. This is a
build tool for producing video, not part of the shipped product.

## The rule this project follows

**Every number on screen comes from a real `lineagelens scan` run.** Nothing is
typed by hand into a slide. `src/data/scan-result.json` is a captured snapshot of
actual CLI output, and the compositions read from it. If the snapshot changes,
the video changes.

Regenerate it against this repo:

```bash
npm run capture
```

Or against any other repo:

```bash
node scripts/capture-scan.js ../some-other-repo
```

The script refuses to write a snapshot with zero AI-attributed lines, so the
demo can never show an empty result dressed up as a finding.

## Commands

```bash
npm install
npm run dev              # Remotion Studio — scrub and edit live
npm run build            # 1920x1080 MP4 → out/lineagelens-demo.mp4
npm run build:social     # 1080x1080 MP4 → out/lineagelens-demo-square.mp4
npm run build:hackathon  # ~2 min hackathon cut → out/lineagelens-hackathon.mp4
npm run still            # poster frame → out/poster.png
npm run typecheck
```

CI (`.github/workflows/demo-video.yml`) typechecks and renders both cuts on
every PR touching this folder, uploading the MP4s as artifacts.

## Hackathon cut — recordings

The hackathon cut embeds three real screen recordings from
`public/recordings/` (see **RECORDINGS.md** for the exact shot list). Until a
clip exists, its scene renders a styled placeholder naming the missing file —
the video is never broken, just honest about what's pending. Drop the clip in,
re-render (`npm run build:hackathon` or CI), done. Clips are gitignored; render
the final cut locally or commit the clips if you accept the repo-size cost.

## Structure

| Path | Contents |
|---|---|
| `src/Root.tsx` | Composition registry — landscape, square, hackathon |
| `src/Demo.tsx` | Marketing scene order, durations, and the scaled stage |
| `src/Hackathon.tsx` | Hackathon scene order and durations |
| `src/scenes/scenes.tsx` | The marketing cut's six scenes |
| `src/scenes/hackathon-scenes.tsx` | Llama flow, proof scenes, capsule scene, close |
| `src/hooks/useAssetExists.ts` | Recording-or-placeholder switch (delayRender + HEAD) |
| `src/scenes/scan-lines.ts` | Turns the real scan snapshot into terminal output |
| `src/components/Terminal.tsx` | Typing command + staggered output reveal |
| `src/components/CodeBlame.tsx` | Code pane with the human gutter and the AI gutter |
| `src/components/primitives.tsx` | Reveal, Kicker, Headline, CountUp, Bar, Panel, Scene |
| `src/theme.ts` | Colour and type scale |
| `scripts/capture-scan.js` | Regenerates the scan snapshot from the real CLI |
| `RECORDINGS.md` | Shot list for the three real-recorded clips |

## Layout note

Scenes are laid out once for a fixed 1920x1080 surface in absolute pixels, and
`Stage` in `Demo.tsx` scales that surface to fit whatever composition renders it.
The square cut is therefore the same frame, letterboxed — there is only one
layout that can break, and fixing it fixes both outputs.

## The story, in order

1. `git blame` tells you who touched a line, not what wrote it
2. so "which files did an agent write?" is unanswerable today
3. one read-only command answers it on a repo it has never seen
4. and it names the risk surfaces that code landed on
5. with the boundary stated plainly: a scan reads git, it cannot invent prompts
6. closing on the number that makes someone act — AI-written auth code, live now
